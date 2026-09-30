use crate::{common::do_check_software_update, hbbs_http::create_http_client_with_url_strict};
#[cfg(target_os = "linux")]
use base::update::replace_file_transaction;
use base::{
    config::keys,
    update::{
        classify_update_preinstall_failure, decide_update_action, try_update_sources,
        update_signature_payload, update_target_key, validate_manifest_contract,
        validate_target_metadata, PendingUpdateEvent, UpdateAction, UpdateSource,
    },
};
use hbb_common::base64::{
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
    Engine as _,
};
use hbb_common::{bail, config, log, ResultType};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc::{channel, Receiver, Sender},
        Mutex,
    },
    time::{Duration, Instant},
};

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};

#[cfg(target_os = "macos")]
use std::os::unix::fs::MetadataExt;

enum UpdateMsg {
    CheckUpdate,
    Exit,
}

lazy_static::lazy_static! {
    static ref TX_MSG : Mutex<Sender<UpdateMsg>> = Mutex::new(start_auto_update_check());
}

static CONTROLLING_SESSION_COUNT: AtomicUsize = AtomicUsize::new(0);

/// Initial wait after startup before the first update check (30 seconds).
pub const INITIAL_CHECK_DELAY: Duration = Duration::from_secs(30);

/// One full day — default interval between update checks.
pub const DUR_ONE_DAY: Duration = Duration::from_secs(60 * 60 * 24);

/// Minimum interval between consecutive update checks (10 minutes).
pub const MIN_INTERVAL: Duration = Duration::from_secs(60 * 10);

/// Retry interval when an update check fails or a session is active (30 minutes).
pub const RETRY_INTERVAL: Duration = Duration::from_secs(60 * 30);

pub fn update_controlling_session_count(count: usize) {
    CONTROLLING_SESSION_COUNT.store(count, Ordering::SeqCst);
}

#[allow(dead_code)]
pub fn start_auto_update() {
    let _sender = TX_MSG.lock().unwrap();
}

#[allow(dead_code)]
pub fn manually_check_update() -> ResultType<()> {
    let sender = TX_MSG.lock().unwrap();
    sender.send(UpdateMsg::CheckUpdate)?;
    Ok(())
}

#[allow(dead_code)]
pub fn stop_auto_update() {
    let sender = TX_MSG.lock().unwrap();
    sender.send(UpdateMsg::Exit).unwrap_or_default();
}

#[inline]
/// Returns true when there are no active incoming or outgoing connections.
/// Used to avoid updating while a remote session is in progress.
pub fn has_no_active_conns() -> bool {
    let conns = crate::Connection::alive_conns();
    conns.is_empty() && has_no_controlling_conns()
}

#[cfg(any(not(target_os = "windows"), feature = "flutter"))]
fn has_no_controlling_conns() -> bool {
    CONTROLLING_SESSION_COUNT.load(Ordering::SeqCst) == 0
}

#[cfg(not(any(not(target_os = "windows"), feature = "flutter")))]
fn has_no_controlling_conns() -> bool {
    let app_exe = format!("{}.exe", crate::get_app_name().to_lowercase());
    for arg in [
        "--connect",
        "--play",
        "--file-transfer",
        "--view-camera",
        "--port-forward",
        "--rdp",
    ] {
        if !crate::platform::get_pids_of_process_with_first_arg(&app_exe, arg).is_empty() {
            return false;
        }
    }
    true
}

fn start_auto_update_check() -> Sender<UpdateMsg> {
    let (tx, rx) = channel();
    std::thread::spawn(move || start_auto_update_check_(rx));
    return tx;
}

fn start_auto_update_check_(rx_msg: Receiver<UpdateMsg>) {
    std::thread::sleep(INITIAL_CHECK_DELAY);
    if let Err(e) = check_update(false) {
        log::error!("Error checking for updates: {}", e);
    }

    let mut last_check_time = Instant::now();
    let mut check_interval = DUR_ONE_DAY;
    loop {
        let recv_res = rx_msg.recv_timeout(check_interval);
        match &recv_res {
            Ok(UpdateMsg::CheckUpdate) | Err(_) => {
                let manually = matches!(recv_res, Ok(UpdateMsg::CheckUpdate));
                if !manually && last_check_time.elapsed() < MIN_INTERVAL {
                    // log::debug!("Update check skipped due to minimum interval.");
                    continue;
                }
                // Don't check update if there are alive connections.
                if !has_no_active_conns() {
                    check_interval = RETRY_INTERVAL;
                    continue;
                }
                if let Err(e) = check_update(manually) {
                    log::error!("Error checking for updates: {}", e);
                    check_interval = RETRY_INTERVAL;
                } else {
                    last_check_time = Instant::now();
                    check_interval = DUR_ONE_DAY;
                }
            }
            Ok(UpdateMsg::Exit) => break,
        }
    }
}

fn check_update(manually: bool) -> ResultType<()> {
    // On macOS, auto-update is handled by check_update_as_root() in the service process.
    // The shared check_update() path is only used for manual update checks from the GUI.
    #[cfg(target_os = "macos")]
    if !manually {
        return Ok(());
    }
    if !(manually || config::Config::get_bool_option(keys::OPTION_ALLOW_AUTO_UPDATE)) {
        return Ok(());
    }
    do_check_software_update()?;

    let response = crate::common::SOFTWARE_UPDATE_RESPONSE
        .lock()
        .unwrap()
        .clone();
    let Some(response) = response else {
        log::debug!("No update available.");
        return Ok(());
    };
    if !response.update_available || response.mode == "disabled" {
        return Ok(());
    }
    let Some(ref manifest) = response.manifest else {
        return Ok(());
    };
    let target_key = crate::common::SOFTWARE_UPDATE_TARGET_KEY
        .lock()
        .unwrap()
        .clone();
    if target_key.is_empty() {
        bail!("update target selection is missing");
    }
    #[cfg(target_os = "windows")]
    let update_msi = target_key.contains("-msi-");
    validate_manifest_contract(
        &response,
        &manifest,
        crate::VERSION,
        crate::BUILD_SEQ,
        crate::PRODUCT,
        crate::EDITION,
        crate::CHANNEL,
        &target_key,
    )?;
    let target = manifest.targets.get(&target_key).cloned();
    let Some(target) = target else {
        return Ok(());
    };
    let mut action = decide_update_action(
        &response,
        config::Config::get_bool_option(keys::OPTION_ALLOW_AUTO_UPDATE),
    );
    if manually && response.update_available && response.mode != "disabled" {
        action = UpdateAction::AutoInstall;
    }
    if matches!(action, UpdateAction::Ignore | UpdateAction::Notify) {
        return Ok(());
    }
    let should_install = action == UpdateAction::AutoInstall;
    let version = manifest.version.as_str();
    report_update_event(
        "started",
        manifest.version.as_str(),
        manifest.build_seq,
        "none",
    );
    let (file_path, source) = match download_verified_target(manifest, &target_key, &target) {
        Ok(downloaded) => downloaded,
        Err(err) => {
            report_update_event(
                "failed",
                manifest.version.as_str(),
                manifest.build_seq,
                "none",
            );
            return Err(err);
        }
    };
    let source = source.as_str();
    report_update_event(
        "downloaded",
        manifest.version.as_str(),
        manifest.build_seq,
        source,
    );
    {
        #[cfg(target_os = "windows")]
        log::debug!("New version available: {}", version);
        // Recheck because a session can start while the verified asset is downloading.
        if should_install && has_no_active_conns() {
            report_update_event("installing", version, manifest.build_seq, source);
            #[cfg(target_os = "windows")]
            update_new_version(update_msi, version, manifest.build_seq, source, &file_path);
            #[cfg(target_os = "linux")]
            if let Err(err) = install_linux_appimage(&file_path) {
                log::error!("Failed to install AppImage update: {}", err);
                report_update_event("rolled_back", version, manifest.build_seq, source);
                remove_download_artifact(&file_path);
            } else {
                report_update_event("installed", version, manifest.build_seq, source);
                remove_download_artifact(&file_path);
            }
            #[cfg(target_os = "macos")]
            if let Some(path) = file_path.to_str() {
                let event = PendingUpdateEvent {
                    transaction_id: new_update_transaction_id(),
                    from_version: crate::VERSION.to_owned(),
                    from_build_seq: crate::BUILD_SEQ,
                    version: version.to_owned(),
                    build_seq: manifest.build_seq,
                    source: if source == UpdateSource::Mirror.as_str() {
                        UpdateSource::Mirror
                    } else {
                        UpdateSource::Primary
                    },
                };
                if let Err(err) = crate::platform::request_update_from_dmg_as_root(path, &event) {
                    log::error!("Failed to install verified macOS update: {}", err);
                    report_update_event(
                        classify_update_preinstall_failure(&err.to_string()),
                        version,
                        manifest.build_seq,
                        source,
                    );
                }
                remove_download_artifact(&file_path);
            } else {
                remove_download_artifact(&file_path);
            }
        } else if should_install {
            #[cfg(target_os = "macos")]
            report_update_event("deferred", version, manifest.build_seq, source);
            remove_download_artifact(&file_path);
        }
    }
    Ok(())
}

pub fn current_update_target_key() -> ResultType<String> {
    current_update_target().map(|(target_key, _)| target_key)
}

fn new_update_transaction_id() -> String {
    hex::encode(hbb_common::sodiumoxide::randombytes::randombytes(16))
}

pub fn current_update_target() -> ResultType<(String, String)> {
    let platform = std::env::consts::OS;
    let arch = std::env::consts::ARCH;
    let kind = update_target_kind()?;
    Ok((
        update_target_key(platform, arch, kind, crate::EDITION),
        kind.to_owned(),
    ))
}

#[cfg(target_os = "windows")]
fn update_target_kind() -> ResultType<&'static str> {
    Ok(if crate::platform::is_msi_installed()? {
        "msi"
    } else {
        "exe"
    })
}

#[cfg(target_os = "linux")]
fn update_target_kind() -> ResultType<&'static str> {
    Ok("appimage")
}

#[cfg(target_os = "macos")]
fn update_target_kind() -> ResultType<&'static str> {
    Ok("dmg")
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn update_target_kind() -> ResultType<&'static str> {
    Ok("pkg")
}

const UPDATE_SIGNATURE_KEY_ID: &str = "yan-release-2026";
const UPDATE_PUBLIC_KEY_BASE64: &str = "YNphn2SGjnetwp0bb/uEGpzfQi8OavMWTHCmqPbMuxg=";

fn decode_signature_value(value: &str) -> ResultType<Vec<u8>> {
    STANDARD
        .decode(value)
        .or_else(|_| URL_SAFE_NO_PAD.decode(value))
        .map_err(|e| hbb_common::anyhow::anyhow!("invalid update signature encoding: {e}"))
}

fn verify_update_signature(
    manifest: &hbb_common::UpdateManifest,
    target_key: &str,
    target: &hbb_common::UpdateTarget,
) -> ResultType<()> {
    if target.signature_key_id != UPDATE_SIGNATURE_KEY_ID {
        bail!("update signing key not trusted");
    }
    let public_key = decode_signature_value(UPDATE_PUBLIC_KEY_BASE64)?;
    let signature = decode_signature_value(&target.signature)?;
    let payload = update_signature_payload(manifest, target_key, target)?;
    verify_detached_signature(&payload, &signature, &public_key)
}

fn verify_detached_signature(data: &[u8], signature: &[u8], public_key: &[u8]) -> ResultType<()> {
    let public_key = hbb_common::sodiumoxide::crypto::sign::PublicKey::from_slice(&public_key)
        .ok_or_else(|| hbb_common::anyhow::anyhow!("invalid update public key"))?;
    let signature = hbb_common::sodiumoxide::crypto::sign::Signature::from_bytes(signature)
        .map_err(|_| hbb_common::anyhow::anyhow!("invalid update signature"))?;
    if !hbb_common::sodiumoxide::crypto::sign::verify_detached(&signature, data, &public_key) {
        bail!("update signature verification failed");
    }
    Ok(())
}

const UPDATE_DOWNLOAD_DIR_PREFIX: &str = ".rustdesk-update-";

struct UpdateDownloadStaging {
    dir: PathBuf,
    file: PathBuf,
    keep: bool,
}

impl UpdateDownloadStaging {
    fn create(filename: &str) -> ResultType<Self> {
        if !is_plain_update_filename(filename) {
            bail!("invalid update filename");
        }

        for _ in 0..16 {
            let suffix = hex::encode(hbb_common::sodiumoxide::randombytes::randombytes(16));
            let dir = std::env::temp_dir().join(format!("{UPDATE_DOWNLOAD_DIR_PREFIX}{suffix}"));
            let mut builder = std::fs::DirBuilder::new();
            #[cfg(unix)]
            builder.mode(0o700);
            match builder.create(&dir) {
                Ok(()) => {
                    return Ok(Self {
                        file: dir.join(filename),
                        dir,
                        keep: false,
                    });
                }
                Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(err.into()),
            }
        }
        bail!("failed to allocate a unique update download directory")
    }

    fn create_file(&self) -> ResultType<File> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        options
            .mode(0o600)
            .custom_flags(hbb_common::libc::O_NOFOLLOW | hbb_common::libc::O_CLOEXEC);
        let file = options.open(&self.file)?;
        Ok(file)
    }

    fn persist(mut self) -> PathBuf {
        self.keep = true;
        self.file.clone()
    }
}

impl Drop for UpdateDownloadStaging {
    fn drop(&mut self) {
        if !self.keep {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }
}

#[cfg(target_os = "macos")]
struct DownloadArtifactCleanup(PathBuf);

#[cfg(target_os = "macos")]
impl Drop for DownloadArtifactCleanup {
    fn drop(&mut self) {
        remove_download_artifact(&self.0);
    }
}

#[cfg(target_os = "macos")]
struct UpdateDirectoryCleanup(PathBuf);

#[cfg(target_os = "macos")]
impl Drop for UpdateDirectoryCleanup {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn update_filename_from_url(url: &str) -> Option<String> {
    let filename = url::Url::parse(url)
        .ok()?
        .path_segments()?
        .next_back()?
        .to_owned();
    is_plain_update_filename(&filename).then_some(filename)
}

fn remove_download_artifact(path: &Path) {
    let _ = std::fs::remove_file(path);
    let Some(parent) = path.parent() else {
        return;
    };
    let is_update_dir = parent
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with(UPDATE_DOWNLOAD_DIR_PREFIX));
    if is_update_dir && parent.parent() == Some(std::env::temp_dir().as_path()) {
        let _ = std::fs::remove_dir(parent);
    }
}

fn download_and_verify(
    url: &str,
    manifest: &hbb_common::UpdateManifest,
    target_key: &str,
    target: &hbb_common::UpdateTarget,
) -> ResultType<PathBuf> {
    validate_target_metadata(target, UPDATE_SIGNATURE_KEY_ID)?;
    let client = create_http_client_with_url_strict(url)?;
    let Some(filename) = update_filename_from_url(url) else {
        bail!("invalid update URL");
    };
    let response = client.get(url).send()?;
    if !response.status().is_success() {
        bail!("download failed: {}", response.status());
    }
    let data = response.bytes()?;
    if data.len() as u64 != target.size {
        bail!("size mismatch");
    }
    let actual = hex::encode(Sha256::digest(&data));
    if !actual.eq_ignore_ascii_case(&target.sha256) {
        bail!("sha256 mismatch");
    }
    verify_update_signature(manifest, target_key, target)?;
    let staging = UpdateDownloadStaging::create(&filename)?;
    let mut file = staging.create_file()?;
    file.write_all(&data)?;
    file.sync_all()?;
    drop(file);
    Ok(staging.persist())
}

fn download_verified_target(
    manifest: &hbb_common::UpdateManifest,
    target_key: &str,
    target: &hbb_common::UpdateTarget,
) -> ResultType<(PathBuf, UpdateSource)> {
    validate_target_metadata(target, UPDATE_SIGNATURE_KEY_ID)?;
    try_update_sources(target, |url| {
        download_and_verify(url, manifest, target_key, target)
    })
    .map_err(|failures| {
        for failure in &failures {
            log::warn!(
                "Update {} source failed: {}: {}",
                failure.source.as_str(),
                failure.url,
                failure.error
            );
        }
        hbb_common::anyhow::anyhow!("all update sources failed")
    })
}

pub(crate) fn report_update_event(status: &str, version: &str, build_seq: u64, source: &str) {
    let _ = report_update_event_with_origin(
        status,
        crate::VERSION,
        crate::BUILD_SEQ,
        version,
        build_seq,
        source,
    );
}

pub(crate) fn report_update_event_with_origin(
    status: &str,
    from_version: &str,
    from_build_seq: u64,
    version: &str,
    build_seq: u64,
    source: &str,
) -> bool {
    #[cfg(feature = "flutter")]
    {
        let event = serde_json::json!({
            "name": "software_update_event",
            "status": status,
            "version": version,
            "build_seq": build_seq,
            "source": source,
        });
        let _ = crate::flutter::push_global_event(crate::flutter::APP_TYPE_MAIN, event.to_string());
    }
    const EVENTS_URL: &str = "https://rdapi.yan.life/rd/update/v1/events";
    let Ok(client) = create_http_client_with_url_strict(EVENTS_URL) else {
        log::warn!("Failed to create update event HTTP client");
        return false;
    };
    let payload = serde_json::json!({
        "client_id": crate::get_app_name(),
        "client_uuid": hex::encode(hbb_common::fingerprint::get_fingerprint(None, None)),
        "status": status,
        "from_version": from_version,
        "from_build_seq": from_build_seq,
        "product": crate::PRODUCT,
        "edition": crate::EDITION,
        "build_number": crate::BUILD_NUMBER,
        "channel": crate::CHANNEL,
        "source_commit": crate::SOURCE_COMMIT,
        "to_version": version,
        "to_build_seq": build_seq,
        "error_code": match status {
            "failed" => "all_sources_failed",
            "deferred" => "active_session",
            "rolled_back" => "install_failed",
            "rollback_failed" => "rollback_failed",
            _ => "",
        },
        "source": source,
    });
    match client.post(EVENTS_URL).json(&payload).send() {
        Ok(response) if response.status().is_success() => true,
        Ok(response) => {
            log::warn!("Update event rejected with HTTP {}", response.status());
            false
        }
        Err(err) => {
            log::warn!("Failed to report update event: {}", err);
            false
        }
    }
}

#[cfg(target_os = "linux")]
fn install_linux_appimage(downloaded: &Path) -> ResultType<()> {
    let current = std::env::current_exe()?;
    let is_appimage = std::env::var_os("APPIMAGE").is_some()
        || current.extension().and_then(|ext| ext.to_str()) == Some("AppImage");
    if !is_appimage {
        bail!("running executable is not an AppImage");
    }
    replace_file_transaction(&current, downloaded, |installed| {
        std::fs::set_permissions(installed, std::fs::Permissions::from_mode(0o755))?;
        let metadata = std::fs::metadata(installed)?;
        if !metadata.is_file() || metadata.len() == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "installed AppImage is empty or not a regular file",
            ));
        }
        Ok(())
    })?;
    Ok(())
}

#[cfg(target_os = "windows")]
fn update_new_version(
    update_msi: bool,
    version: &str,
    build_seq: u64,
    source: &str,
    file_path: &PathBuf,
) {
    log::debug!(
        "New version is downloaded, update begin, update msi: {update_msi}, version: {version}, file: {:?}",
        file_path.to_str()
    );
    if let Some(p) = file_path.to_str() {
        if let Some(session_id) = crate::platform::get_current_process_session_id() {
            if update_msi {
                match crate::platform::update_me_msi(p, true) {
                    Ok(_) => {
                        log::debug!("New version \"{}\" updated.", version);
                        report_update_event("installed", version, build_seq, source);
                        remove_download_artifact(file_path);
                    }
                    Err(e) => {
                        log::error!(
                            "Failed to install the new msi version  \"{}\": {}",
                            version,
                            e
                        );
                        report_update_event("rolled_back", version, build_seq, source);
                        remove_download_artifact(&file_path);
                    }
                }
            } else {
                let pending_event = PendingUpdateEvent {
                    transaction_id: new_update_transaction_id(),
                    from_version: crate::VERSION.to_owned(),
                    from_build_seq: crate::BUILD_SEQ,
                    version: version.to_owned(),
                    build_seq,
                    source: if source == UpdateSource::Mirror.as_str() {
                        UpdateSource::Mirror
                    } else {
                        UpdateSource::Primary
                    },
                };
                let pending_args = pending_event.cli_args().join(" ");
                let custom_client_staging_dir = if crate::is_custom_client() {
                    let custom_client_staging_dir =
                        crate::platform::get_custom_client_staging_dir();
                    if let Err(e) = crate::platform::handle_custom_client_staging_dir_before_update(
                        &custom_client_staging_dir,
                    ) {
                        log::error!(
                            "Failed to handle custom client staging dir before update: {}",
                            e
                        );
                        remove_download_artifact(&file_path);
                        return;
                    }
                    Some(custom_client_staging_dir)
                } else {
                    // Clean up any residual staging directory from previous custom client
                    let staging_dir = crate::platform::get_custom_client_staging_dir();
                    hbb_common::allow_err!(crate::platform::remove_custom_client_staging_dir(
                        &staging_dir
                    ));
                    None
                };
                let update_launched = match crate::platform::launch_privileged_process(
                    session_id,
                    &format!("{} --update {}", p, pending_args),
                ) {
                    Ok(h) => {
                        if h.is_null() {
                            log::error!("Failed to update to the new version: {}", version);
                            report_update_event("rolled_back", version, build_seq, source);
                            false
                        } else {
                            log::debug!("New version \"{}\" is launched.", version);
                            true
                        }
                    }
                    Err(e) => {
                        log::error!("Failed to run the new version: {}", e);
                        report_update_event("rolled_back", version, build_seq, source);
                        false
                    }
                };
                if !update_launched {
                    if let Some(dir) = custom_client_staging_dir {
                        hbb_common::allow_err!(crate::platform::remove_custom_client_staging_dir(
                            &dir
                        ));
                    }
                    remove_download_artifact(&file_path);
                }
            }
        } else {
            log::error!(
                "Failed to get the current process session id, Error {}",
                std::io::Error::last_os_error()
            );
            remove_download_artifact(&file_path);
        }
    } else {
        // unreachable!()
        log::error!(
            "Failed to convert the file path to string: {}",
            file_path.display()
        );
        remove_download_artifact(file_path);
    }
}

pub fn get_update_download_file_from_url(url: &str) -> Option<PathBuf> {
    let parsed = url::Url::parse(url).ok()?;
    // Check the raw prefix before Url normalizes default ports.
    if !url.starts_with("https://github.com/")
        || parsed.scheme() != "https"
        || parsed.host_str() != Some("github.com")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return None;
    }

    let mut segments = parsed.path_segments()?;
    let owner = segments.next()?;
    let repo = segments.next()?;
    let releases = segments.next()?;
    let download = segments.next()?;
    let tag = segments.next()?;
    let filename = segments.next()?;

    if owner != "rustdesk"
        || repo != "rustdesk"
        || releases != "releases"
        || download != "download"
        || tag.is_empty()
        || segments.next().is_some()
        || !is_plain_update_filename(filename)
    {
        return None;
    }

    Some(std::env::temp_dir().join(filename))
}

fn is_plain_update_filename(filename: &str) -> bool {
    if filename.is_empty()
        || filename.contains('/')
        || filename.contains('\\')
        || filename.contains(':')
    {
        return false;
    }

    let mut components = Path::new(filename).components();
    matches!(
        components.next(),
        Some(Component::Normal(name)) if name.to_str() == Some(filename)
    ) && components.next().is_none()
}

pub fn get_download_file_from_url(url: &str) -> Option<PathBuf> {
    get_update_download_file_from_url(url)
}

/// Queries all active connections (remote, file-transfer, port-forward, camera, terminal)
/// from every logged-in user's --server process via IPC.
/// The root service cannot read connection state directly since connections
/// live in user --server processes. Handles fast user switching by querying
/// all GUI users, including the login-window server at UID 0. Falls back to
/// false (assumes sessions active) on any IPC error to avoid updating during
/// an unknown session state.
#[cfg(target_os = "macos")]
pub fn has_no_active_conns_ipc() -> bool {
    let rt = match hbb_common::tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(_) => return false,
    };
    rt.block_on(async {
        // Use the same GUI-domain-filtered UID set as the update script.
        // Shell-only SSH/TTY users are excluded, while an empty GUI set maps
        // to UID 0 so the LoginWindow server is queried rather than assumed idle.
        let uids = crate::platform::get_logged_in_uids();
        // Check each user's server — fail closed if any has active connections
        for uid in uids {
            if let Ok(mut conn) = crate::ipc::connect_for_uid(1000, uid, "").await {
                if conn
                    .send(&crate::ipc::Data::HasNoActiveConns(None))
                    .await
                    .is_ok()
                {
                    match conn.next_timeout(1000).await {
                        Ok(Some(crate::ipc::Data::HasNoActiveConns(Some(true)))) => {
                            // Explicit no active connections — safe to continue
                        }
                        Ok(Some(crate::ipc::Data::HasNoActiveConns(Some(false)))) => {
                            return false; // Explicit active connections
                        }
                        _ => {
                            return false; // Timeout/error/unexpected — fail closed
                        }
                    }
                } else {
                    return false; // Send failed — fail closed
                }
            } else {
                return false; // Connection failed — fail closed
            }
        }
        true // All users explicitly confirmed no active connections
    })
}

#[cfg(target_os = "macos")]
fn wait_for_failed_update_retry() {
    const FAILURE_MARKER: &str = "/var/root/.rustdeskupdate_failed";
    let marker = std::path::Path::new(FAILURE_MARKER);
    if !marker.exists() {
        return;
    }

    // The updater script records failure immediately before launchd restarts
    // the old daemon. Preserve the retry deadline across that restart instead
    // of consuming the marker and retrying the same broken release in 30 sec.
    let remaining = std::fs::metadata(marker)
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| std::time::SystemTime::now().duration_since(modified).ok())
        .map(|elapsed| RETRY_INTERVAL.saturating_sub(elapsed))
        .unwrap_or(RETRY_INTERVAL);
    if !remaining.is_zero() {
        log::info!(
            "[root-update] Previous update failed; retrying in {} seconds.",
            remaining.as_secs()
        );
        std::thread::sleep(remaining);
    }
    match std::fs::remove_file(marker) {
        Ok(()) => log::info!("[root-update] Previous update retry interval elapsed."),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
        Err(err) => log::warn!("[root-update] Failed to clear failure marker: {}", err),
    }
}

/// Starts the background silent auto-update scheduler for macOS.
/// Called from `start_os_service()` which runs as root via LaunchDaemon.
#[cfg(target_os = "macos")]
pub fn start_auto_update_macos() {
    let spawn_result = std::thread::Builder::new()
        .name("rustdesk-auto-update".to_owned())
        .spawn(|| {
            log::info!("[root-update] Auto-update scheduler thread started.");
            consume_mac_update_result();
            std::thread::sleep(INITIAL_CHECK_DELAY);
            wait_for_failed_update_retry();
            loop {
                log::info!("[root-update] Running scheduled update check...");
                let no_active_conns = has_no_active_conns_ipc();
                let interval = if !no_active_conns {
                    log::info!("[root-update] Active session in progress, retrying in 10 min.");
                    MIN_INTERVAL
                } else {
                    match check_update_as_root() {
                        Ok(update_started) => {
                            if update_started {
                                // The replacement script is detached and may fail
                                // after this process returns. Always retry at the
                                // failure interval until the new daemon replaces us.
                                RETRY_INTERVAL
                            } else {
                                DUR_ONE_DAY
                            }
                        }
                        Err(e) => {
                            log::error!("[root-update] Update check failed: {}", e);
                            RETRY_INTERVAL
                        }
                    }
                };
                std::thread::sleep(interval);
            }
        });
    if let Err(err) = spawn_result {
        log::error!("[root-update] Failed to start scheduler thread: {}", err);
    }
}

#[cfg(target_os = "macos")]
fn consume_mac_update_result() {
    for _ in 0..600 {
        let Some((result, claimed)) = crate::platform::consume_root_update_result() else {
            return;
        };
        if result.status == "pending" {
            std::thread::sleep(Duration::from_secs(1));
            continue;
        }
        if report_update_event_with_origin(
            &result.status,
            &result.event.from_version,
            result.event.from_build_seq,
            &result.event.version,
            result.event.build_seq,
            result.event.source.as_str(),
        ) {
            if !claimed {
                log::warn!("[root-update] terminal update result was not atomically claimed");
                return;
            }
            if let Err(err) = crate::platform::clear_claimed_root_update_result() {
                log::warn!("[root-update] Failed to clear reported update result: {err}");
            }
            return;
        }
        std::thread::sleep(Duration::from_secs(1));
    }
    log::warn!("[root-update] Timed out waiting for detached update result");
}

#[cfg(target_os = "macos")]
pub fn check_update_as_root() -> ResultType<bool> {
    // Allow-auto-update setting
    if !config::Config::get_bool_option(keys::OPTION_ALLOW_AUTO_UPDATE) {
        log::info!("[root-update] Auto update is disabled, skipping.");
        return Ok(false);
    }
    // Clean up only old temp dirs from previous failed updates. The detached
    // installer keeps using its update directory after this process exits and
    // releases the advisory lock, so a newly-started daemon must not remove a
    // directory that still belongs to the active transaction.
    if let Ok(entries) = std::fs::read_dir("/tmp") {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.starts_with(".rustdeskupdate-root-")
                || name_str.starts_with(".rustdeskdownload-")
                || name_str.starts_with(UPDATE_DOWNLOAD_DIR_PREFIX)
            {
                let path = entry.path();
                let Ok(metadata) = std::fs::symlink_metadata(&path) else {
                    continue;
                };
                let mode = metadata.mode() & 0o7777;
                let is_stale = metadata
                    .modified()
                    .ok()
                    .and_then(|modified| std::time::SystemTime::now().duration_since(modified).ok())
                    .is_some_and(|age| age >= RETRY_INTERVAL);
                if metadata.file_type().is_dir() && metadata.uid() == 0 && mode == 0o700 && is_stale
                {
                    if let Err(err) = std::fs::remove_dir_all(&path) {
                        log::warn!(
                            "[root-update] Failed to remove stale temp dir {}: {}",
                            path.display(),
                            err
                        );
                    }
                }
            }
        }
    }
    if let Err(e) = do_check_software_update() {
        bail!("[root-update] Failed to check for software update: {}", e);
    }
    let response = crate::common::SOFTWARE_UPDATE_RESPONSE
        .lock()
        .unwrap()
        .clone();
    let Some(response) = response else {
        log::info!("[root-update] No update available.");
        return Ok(false);
    };
    let Some(ref manifest) = response.manifest else {
        return Ok(false);
    };
    let target_key = crate::common::SOFTWARE_UPDATE_TARGET_KEY
        .lock()
        .unwrap()
        .clone();
    if target_key.is_empty() {
        bail!("[root-update] update target selection is missing");
    }
    validate_manifest_contract(
        &response,
        &manifest,
        crate::VERSION,
        crate::BUILD_SEQ,
        crate::PRODUCT,
        crate::EDITION,
        crate::CHANNEL,
        &target_key,
    )?;
    let target = manifest.targets.get(&target_key).cloned();
    let Some(target) = target else {
        return Ok(false);
    };
    let action = decide_update_action(
        &response,
        config::Config::get_bool_option(keys::OPTION_ALLOW_AUTO_UPDATE),
    );
    if action != UpdateAction::AutoInstall {
        return Ok(false);
    }
    let version = manifest.version.clone();
    report_update_event("started", &version, manifest.build_seq, "none");
    let (file_path, source) = match download_verified_target(manifest, &target_key, &target) {
        Ok(downloaded) => downloaded,
        Err(err) => {
            report_update_event("failed", &version, manifest.build_seq, "none");
            return Err(err);
        }
    };
    let _download_cleanup = DownloadArtifactCleanup(file_path.clone());
    let source = source.as_str();
    report_update_event("downloaded", &version, manifest.build_seq, source);
    // Use mktemp so a local user cannot pre-create a predictable path and
    // permanently deny updates for a reused service PID.
    let private_tmp_output = std::process::Command::new("/usr/bin/mktemp")
        .args(["-d", "/tmp/.rustdeskdownload-XXXXXX"])
        .output()?;
    if !private_tmp_output.status.success() {
        bail!(
            "[root-update] Failed to create private download directory: {}",
            String::from_utf8_lossy(&private_tmp_output.stderr).trim()
        );
    }
    let private_tmp = String::from_utf8(private_tmp_output.stdout)
        .map_err(|err| hbb_common::anyhow::anyhow!("[root-update] mktemp output error: {}", err))?
        .trim()
        .to_owned();
    if private_tmp.is_empty() {
        bail!("[root-update] mktemp returned an empty download directory");
    }
    let _private_tmp_cleanup = UpdateDirectoryCleanup(PathBuf::from(&private_tmp));
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&private_tmp, std::fs::Permissions::from_mode(0o700))?;
    }
    let filename = file_path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("rustdesk.dmg");
    let staged_path = std::path::PathBuf::from(format!("{}/{}", private_tmp, filename));
    std::fs::rename(&file_path, &staged_path)?;
    let tmp_path = staged_path.to_string_lossy().to_string();
    log::info!("[root-update] Downloaded to {}", tmp_path);
    // Recheck active sessions before installing — download can take minutes
    if !has_no_active_conns_ipc() {
        if let Err(e) = std::fs::remove_dir_all(&private_tmp) {
            log::warn!(
                "[root-update] Failed to remove temp dir {}: {}",
                private_tmp,
                e
            );
        }
        report_update_event("deferred", &version, manifest.build_seq, source);
        bail!("[root-update] Active session started during download, deferring update.");
    }
    // Install silently as root
    report_update_event("installing", &version, manifest.build_seq, source);
    let event = PendingUpdateEvent {
        transaction_id: new_update_transaction_id(),
        from_version: crate::VERSION.to_owned(),
        from_build_seq: crate::BUILD_SEQ,
        version: version.clone(),
        build_seq: manifest.build_seq,
        source: if source == UpdateSource::Mirror.as_str() {
            UpdateSource::Mirror
        } else {
            UpdateSource::Primary
        },
    };
    let result = crate::platform::update_from_dmg_as_root(&tmp_path, &version, &event);
    // Clean up download directory
    if let Err(e) = std::fs::remove_dir_all(&private_tmp) {
        log::warn!(
            "[root-update] Failed to remove temp dir {}: {}",
            private_tmp,
            e
        );
    }
    match result {
        Ok(_) => Ok(true),
        Err(err) => {
            report_update_event(
                classify_update_preinstall_failure(&err.to_string()),
                &version,
                manifest.build_seq,
                source,
            );
            Err(err)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        get_download_file_from_url, update_filename_from_url, verify_detached_signature,
        UpdateDownloadStaging,
    };

    #[test]
    fn update_download_file_accepts_expected_github_asset_urls() {
        let file = get_download_file_from_url(
            "https://github.com/rustdesk/rustdesk/releases/download/1.4.0/rustdesk-1.4.0-x86_64.dmg",
        )
        .expect("valid GitHub release asset URL");

        assert_eq!(
            file.file_name().and_then(|name| name.to_str()),
            Some("rustdesk-1.4.0-x86_64.dmg")
        );
    }

    #[test]
    fn update_download_file_rejects_untrusted_or_malformed_urls() {
        for url in [
            "http://github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe",
            "https://example.com/rustdesk.exe",
            "https://github.com/other/project/releases/download/1/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/download/1/",
            "https://github.com/rustdesk/rustdesk/releases/download/1/nested/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/download/1/C:rustdesk.exe",
            "https://user@github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe",
            "https://github.com:443/rustdesk/rustdesk/releases/download/1/rustdesk.exe",
            "https://github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe?download=1",
            "https://github.com/rustdesk/rustdesk/releases/download/1/rustdesk.exe#download",
            "not a url",
        ] {
            assert!(get_download_file_from_url(url).is_none(), "{url}");
        }
    }

    #[test]
    fn update_filename_rejects_non_plain_final_path_segments() {
        assert_eq!(
            update_filename_from_url("https://download.yan.life/stable/rustdesk.exe"),
            Some("rustdesk.exe".to_owned())
        );
        for url in [
            "https://download.yan.life/stable/",
            "https://download.yan.life/stable/C:rustdesk.exe",
            "not a url",
        ] {
            assert!(update_filename_from_url(url).is_none(), "{url}");
        }
    }

    #[test]
    fn update_download_staging_is_unique_and_cleans_up_on_drop() {
        let first = UpdateDownloadStaging::create("rustdesk.exe").expect("first staging dir");
        let second = UpdateDownloadStaging::create("rustdesk.exe").expect("second staging dir");
        let first_dir = first.dir.clone();
        let second_dir = second.dir.clone();

        assert_ne!(first_dir, second_dir);
        assert!(first_dir.is_dir());
        assert!(second_dir.is_dir());
        drop(first);
        drop(second);
        assert!(!first_dir.exists());
        assert!(!second_dir.exists());
    }

    #[test]
    fn update_download_file_creation_is_exclusive() {
        let staging = UpdateDownloadStaging::create("rustdesk.exe").expect("staging dir");
        let dir = staging.dir.clone();
        let file = staging.create_file().expect("new file");
        assert!(staging.create_file().is_err());
        drop(file);
        drop(staging);
        assert!(!dir.exists());
    }

    #[cfg(unix)]
    #[test]
    fn update_download_staging_has_private_permissions() {
        use std::os::unix::fs::PermissionsExt;

        let staging = UpdateDownloadStaging::create("rustdesk.dmg").expect("staging dir");
        let file = staging.create_file().expect("new file");
        let dir_mode = std::fs::metadata(&staging.dir)
            .expect("staging metadata")
            .permissions()
            .mode()
            & 0o777;
        let file_mode = file.metadata().expect("file metadata").permissions().mode() & 0o777;

        assert_eq!(dir_mode, 0o700);
        assert_eq!(file_mode, 0o600);
    }

    #[test]
    fn detached_update_signature_accepts_valid_bytes_and_rejects_tampering() {
        let (public_key, secret_key) = hbb_common::sodiumoxide::crypto::sign::gen_keypair();
        let data = b"rustdesk-yan update fixture";
        let signature = hbb_common::sodiumoxide::crypto::sign::sign_detached(data, &secret_key);
        assert!(verify_detached_signature(data, signature.as_ref(), public_key.as_ref()).is_ok());
        assert!(
            verify_detached_signature(b"tampered", signature.as_ref(), public_key.as_ref())
                .is_err()
        );
        let (other_public_key, _) = hbb_common::sodiumoxide::crypto::sign::gen_keypair();
        assert!(
            verify_detached_signature(data, signature.as_ref(), other_public_key.as_ref()).is_err()
        );
    }
}
