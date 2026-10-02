use crate::{
    common::{
        do_check_software_update, do_check_software_update_with_context,
        do_check_software_update_with_context_result,
    },
    hbbs_http::create_http_client_with_url_strict,
};
#[cfg(target_os = "linux")]
use base::update::replace_file_transaction;
use base::{
    config::keys,
    update::{
        classify_update_preinstall_failure, decide_update_action,
        normalize_scheduled_update_interval_hours, parse_update_stream_sse_event,
        policy_resume_revision, should_run_scheduled_update, should_run_startup_update,
        try_update_sources, update_client_identity, update_option_enabled,
        update_policy_stream_url, update_signature_payload, update_target_key,
        update_target_release, validate_manifest_contract, validate_target_metadata,
        PendingUpdateEvent, PolicyDecision, ProcessedUpdateCommands, UpdateAction, UpdateCommand,
        UpdateCommandAction, UpdateCommandDecision, UpdateCommandState, UpdatePolicy, UpdateSource,
        UpdateStreamEvent, DEFAULT_SCHEDULED_UPDATE_INTERVAL_HOURS,
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
    io::{BufRead, BufReader, Write},
    path::{Component, Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc::{channel, Receiver, Sender},
        Mutex,
    },
    time::{Duration, Instant},
};

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};

#[cfg(target_os = "linux")]
use std::os::unix::fs::PermissionsExt;

#[cfg(target_os = "macos")]
use std::os::unix::fs::MetadataExt;

enum UpdateMsg {
    CheckUpdate,
    ConnectivityRestored,
    ScheduleChanged,
    Command(UpdateCommand),
    Exit,
}

lazy_static::lazy_static! {
    static ref TX_MSG : Mutex<Sender<UpdateMsg>> = Mutex::new(start_auto_update_check());
}

#[cfg(target_os = "macos")]
lazy_static::lazy_static! {
    static ref MAC_SCHEDULER_WAKE: Mutex<Option<Sender<()>>> = Mutex::new(None);
}

static CONTROLLING_SESSION_COUNT: AtomicUsize = AtomicUsize::new(0);
static POLICY_STREAM_STARTED: AtomicBool = AtomicBool::new(false);

/// Initial wait after startup before the first update check (30 seconds).
pub const INITIAL_CHECK_DELAY: Duration = Duration::from_secs(30);

/// Legacy macOS root updater interval.
pub const DUR_ONE_DAY: Duration = Duration::from_secs(60 * 60 * 24);

/// Minimum interval between consecutive update checks (10 minutes).
pub const MIN_INTERVAL: Duration = Duration::from_secs(60 * 10);

/// Retry interval when an update check fails or a session is active (30 minutes).
pub const RETRY_INTERVAL: Duration = Duration::from_secs(60 * 30);
pub const POLICY_EOF_RECONNECT_DELAY: Duration = Duration::from_secs(1);
pub const POLICY_ERROR_RECONNECT_DELAY: Duration = Duration::from_secs(2);
const UPDATE_COMMAND_RETRY_INTERVAL: Duration = Duration::from_secs(30);
const DEFERRED_UPDATE_RETRY_INTERVAL: Duration = Duration::from_secs(30);

pub(crate) struct UpdateDeviceAuthHeaders {
    pub device_id: String,
    pub public_key: String,
    pub timestamp: String,
    pub nonce: String,
    pub signature: String,
}

pub(crate) fn update_device_auth_headers(
    method: &str,
    url: &str,
    identity: &base::update::UpdateClientIdentity,
    command_id: &str,
    body: &[u8],
) -> ResultType<UpdateDeviceAuthHeaders> {
    let path = url::Url::parse(url)?.path().to_owned();
    let timestamp = chrono::Utc::now().timestamp().to_string();
    let nonce = hex::encode(hbb_common::sodiumoxide::randombytes::randombytes(16));
    let body_sha256 = hex::encode(Sha256::digest(body));
    let canonical = base::update::update_device_auth_payload(
        method,
        &path,
        &identity.client_id,
        &identity.client_uuid,
        timestamp.parse()?,
        &nonce,
        command_id,
        &body_sha256,
    );
    let (secret_key, public_key) = config::Config::get_key_pair();
    let secret_key = hbb_common::sodiumoxide::crypto::sign::SecretKey::from_slice(&secret_key)
        .ok_or_else(|| hbb_common::anyhow::anyhow!("invalid device signing key"))?;
    let signature = hbb_common::sodiumoxide::crypto::sign::sign_detached(&canonical, &secret_key);
    Ok(UpdateDeviceAuthHeaders {
        device_id: config::Config::get_id(),
        public_key: STANDARD.encode(public_key),
        timestamp,
        nonce,
        signature: STANDARD.encode(signature.to_bytes()),
    })
}

pub fn update_controlling_session_count(count: usize) {
    CONTROLLING_SESSION_COUNT.store(count, Ordering::SeqCst);
}

#[allow(dead_code)]
pub fn start_auto_update() {
    start_update_policy_stream();
    let _sender = TX_MSG.lock().unwrap();
}

pub fn update_schedule_changed() {
    let sender = TX_MSG.lock().unwrap();
    let _ = sender.send(UpdateMsg::ScheduleChanged);
    #[cfg(target_os = "macos")]
    if let Some(sender) = MAC_SCHEDULER_WAKE.lock().unwrap().as_ref() {
        let _ = sender.send(());
    }
}

pub fn start_update_policy_stream() {
    if POLICY_STREAM_STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    if let Err(err) = std::thread::Builder::new()
        .name("rustdesk-update-policy".to_owned())
        .spawn(update_policy_stream_loop)
    {
        POLICY_STREAM_STARTED.store(false, Ordering::SeqCst);
        log::warn!("Failed to start update policy stream: {err}");
    }
}

fn applied_policy_revision(identity: &base::update::UpdateClientIdentity) -> Option<u64> {
    let stored_uuid = config::Config::get_option(keys::OPTION_UPDATE_POLICY_CLIENT_UUID);
    let revision = config::Config::get_option(keys::OPTION_UPDATE_POLICY_REVISION)
        .parse()
        .ok();
    let revision = policy_resume_revision(&stored_uuid, &identity.client_uuid, revision);
    if revision.is_none() && stored_uuid != identity.client_uuid {
        config::Config::set_option(
            keys::OPTION_UPDATE_POLICY_REVISION.to_owned(),
            String::new(),
        );
        config::Config::set_option(
            keys::OPTION_UPDATE_POLICY_CLIENT_UUID.to_owned(),
            identity.client_uuid.clone(),
        );
    }
    revision
}

fn current_update_client_identity() -> base::update::UpdateClientIdentity {
    update_client_identity(
        &hbb_common::config::Config::get_id(),
        &crate::encode64(hbb_common::get_uuid()),
    )
}

fn apply_update_policy(policy: UpdatePolicy) {
    let identity = current_update_client_identity();
    if policy.decision(applied_policy_revision(&identity)) == PolicyDecision::IgnoreStale {
        return;
    }
    let check_value = if policy.check_on_startup { "Y" } else { "N" };
    let auto_value = if policy.auto_update { "Y" } else { "N" };
    let scheduled_value = if policy.scheduled_update { "Y" } else { "N" };
    let scheduled_hours =
        normalize_scheduled_update_interval_hours(policy.scheduled_update_interval_hours);
    config::LocalConfig::set_option(
        keys::OPTION_ENABLE_CHECK_UPDATE.to_owned(),
        check_value.to_owned(),
    );
    config::Config::set_option(
        keys::OPTION_ALLOW_AUTO_UPDATE.to_owned(),
        auto_value.to_owned(),
    );
    config::Config::set_option(
        keys::OPTION_ENABLE_SCHEDULED_UPDATE.to_owned(),
        scheduled_value.to_owned(),
    );
    config::Config::set_option(
        keys::OPTION_SCHEDULED_UPDATE_INTERVAL_HOURS.to_owned(),
        scheduled_hours.to_string(),
    );
    config::Config::set_option(
        keys::OPTION_UPDATE_POLICY_REVISION.to_owned(),
        policy.revision.to_string(),
    );
    config::Config::set_option(
        keys::OPTION_UPDATE_POLICY_CLIENT_UUID.to_owned(),
        policy.client_uuid.clone(),
    );
    config::Status::set("sysinfo_hash", String::new());
    crate::ui_interface::refresh_options();
    #[cfg(feature = "flutter")]
    {
        let event = serde_json::json!({
            "name": "update_policy_changed",
            "enable_check_update": policy.check_on_startup,
            "allow_auto_update": policy.auto_update,
            "enable_scheduled_update": policy.scheduled_update,
            "scheduled_update_interval_hours": scheduled_hours,
            "policy_revision": policy.revision,
        });
        let _ = crate::flutter::push_global_event(crate::flutter::APP_TYPE_MAIN, event.to_string());
    }
    update_schedule_changed();
}

fn notify_update_connectivity_restored() {
    let sender = TX_MSG.lock().unwrap();
    let _ = sender.send(UpdateMsg::ConnectivityRestored);
}

fn update_policy_stream_loop() {
    const POLICY_BASE_URL: &str = "https://rdapi.yan.life";
    let mut connection_seen = false;
    loop {
        let identity = current_update_client_identity();
        let revision = applied_policy_revision(&identity);
        let notify_on_connect = connection_seen;
        let result = read_update_policy_stream_once(
            POLICY_BASE_URL,
            &identity,
            revision,
            || {
                if notify_on_connect {
                    notify_update_connectivity_restored();
                }
            },
            apply_update_policy,
            enqueue_update_command,
        );
        if result.is_ok() {
            connection_seen = true;
        }
        let reconnect_delay = match result {
            Ok(()) => POLICY_EOF_RECONNECT_DELAY,
            Err(err) => {
                log::trace!("Update policy stream disconnected: {err}");
                POLICY_ERROR_RECONNECT_DELAY
            }
        };
        std::thread::sleep(reconnect_delay);
    }
}

fn read_update_policy_stream_once(
    base_url: &str,
    identity: &base::update::UpdateClientIdentity,
    revision: Option<u64>,
    mut on_connected: impl FnMut(),
    mut on_policy: impl FnMut(UpdatePolicy),
    mut on_command: impl FnMut(UpdateCommand),
) -> ResultType<()> {
    let url = update_policy_stream_url(base_url, identity, revision);
    let client = if url.starts_with("https://") {
        create_http_client_with_url_strict(&url)?
    } else {
        #[cfg(test)]
        {
            reqwest::blocking::Client::new()
        }
        #[cfg(not(test))]
        {
            bail!("update policy stream requires HTTPS")
        }
    };
    let send_stream = |authenticated: bool| -> ResultType<reqwest::blocking::Response> {
        let mut request = client.get(&url).header("Accept", "text/event-stream");
        if authenticated {
            let auth = update_device_auth_headers("GET", &url, identity, "", &[])?;
            request = request
                .header("X-RustDesk-Device-ID", auth.device_id)
                .header("X-RustDesk-Device-Public-Key", auth.public_key)
                .header("X-RustDesk-Device-Timestamp", auth.timestamp)
                .header("X-RustDesk-Device-Nonce", auth.nonce)
                .header("X-RustDesk-Device-Signature", auth.signature);
        }
        if let Some(revision) = revision {
            request = request.header("Last-Event-ID", revision.to_string());
        }
        Ok(request.send()?)
    };
    let response = send_stream(true)?;
    let (response, authenticated) = if response.status() == reqwest::StatusCode::UNAUTHORIZED {
        (send_stream(false)?, false)
    } else {
        (response, true)
    };
    let response = response.error_for_status()?;
    on_connected();
    let mut reader = BufReader::new(response);
    let mut event = String::new();
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        event.push_str(&line);
        if line == "\n" || line == "\r\n" {
            match parse_update_stream_sse_event(&event)? {
                Some(UpdateStreamEvent::Policy(policy)) => {
                    if policy.matches_identity(identity) {
                        on_policy(policy);
                    } else {
                        log::warn!("Ignored update policy for a different client identity");
                    }
                }
                Some(UpdateStreamEvent::Command(command)) if authenticated => on_command(command),
                Some(UpdateStreamEvent::Command(_)) => {
                    log::warn!("Ignored update command from unauthenticated policy stream");
                }
                None => {}
            }
            event.clear();
        }
    }
    Ok(())
}

fn update_command_should_defer(action: UpdateCommandAction, has_active_session: bool) -> bool {
    action == UpdateCommandAction::Install && has_active_session
}

fn update_command_matches_target(
    command_version: &str,
    command_build_seq: u64,
    manifest_version: &str,
    manifest_build_seq: u64,
) -> bool {
    command_version == manifest_version && command_build_seq == manifest_build_seq
}

#[derive(Debug, PartialEq, Eq)]
enum UpdateCommandRunOutcome {
    NoUpdate,
    CheckCompleted,
    Deferred,
    AwaitingInstallResult,
    Installed,
}

fn load_pending_update_commands() -> Vec<UpdateCommandState> {
    let value = config::Config::get_option(keys::OPTION_PENDING_UPDATE_COMMANDS);
    if value.is_empty() {
        return Vec::new();
    }
    match serde_json::from_str(&value) {
        Ok(commands) => commands,
        Err(err) => match serde_json::from_str::<Vec<UpdateCommand>>(&value) {
            Ok(commands) => commands
                .into_iter()
                .map(UpdateCommandState::pending)
                .collect(),
            Err(_) => {
                log::warn!("Failed to load pending update commands: {err}");
                Vec::new()
            }
        },
    }
}

fn store_pending_update_commands(commands: &[UpdateCommandState]) -> ResultType<()> {
    let value = serde_json::to_string(commands)?;
    config::Config::set_option(keys::OPTION_PENDING_UPDATE_COMMANDS.to_owned(), value);
    Ok(())
}

fn load_processed_update_commands() -> ProcessedUpdateCommands {
    let value = config::Config::get_option(keys::OPTION_PROCESSED_UPDATE_COMMANDS);
    if value.is_empty() {
        return ProcessedUpdateCommands::default();
    }
    serde_json::from_str(&value).unwrap_or_else(|_| {
        let mut history = ProcessedUpdateCommands::default();
        for command_id in value.split(',').filter(|value| !value.is_empty()) {
            history.remember(command_id.to_owned());
        }
        history
    })
}

fn store_processed_update_commands(history: &ProcessedUpdateCommands) -> ResultType<()> {
    config::Config::set_option(
        keys::OPTION_PROCESSED_UPDATE_COMMANDS.to_owned(),
        serde_json::to_string(history)?,
    );
    Ok(())
}

fn enqueue_update_command(command: UpdateCommand) {
    let sender = TX_MSG.lock().unwrap().clone();
    let identity = current_update_client_identity();
    let history = load_processed_update_commands();
    let mut pending = load_pending_update_commands();
    let already_seen = history.contains(&command.command_id)
        || pending
            .iter()
            .any(|value| value.command.command_id == command.command_id);
    match command.decision(&identity, chrono::Utc::now().timestamp(), already_seen) {
        UpdateCommandDecision::Execute => {
            pending.push(UpdateCommandState::pending(command.clone()));
            if let Err(err) = store_pending_update_commands(&pending) {
                log::error!("Failed to persist update command: {err}");
                report_update_command_event(&command, "failed", "persistence_failed");
                return;
            }
            report_update_command_event(&command, "accepted", "");
            if let Err(err) = sender.send(UpdateMsg::Command(command)) {
                log::warn!("Failed to enqueue update command: {err}");
            }
        }
        UpdateCommandDecision::Expired => {
            report_update_command_event(&command, "expired", "expired");
        }
        UpdateCommandDecision::IdentityMismatch => {
            log::warn!("Ignored update command for a different client identity");
        }
        UpdateCommandDecision::Duplicate => {
            if pending.iter().any(|state| {
                state.command.command_id == command.command_id && state.terminal.is_some()
            }) {
                retry_update_command_terminal(&command.command_id);
            }
        }
    }
}

fn complete_update_command(command: &UpdateCommand) {
    let mut history = load_processed_update_commands();
    history.remember(command.command_id.clone());
    if let Err(err) = store_processed_update_commands(&history) {
        log::error!("Failed to persist processed update command: {err}");
        return;
    }
    let mut pending = load_pending_update_commands();
    pending.retain(|value| value.command.command_id != command.command_id);
    if let Err(err) = store_pending_update_commands(&pending) {
        log::error!("Failed to persist completed update command: {err}");
    }
}

fn persist_and_report_update_command_terminal(
    command: &UpdateCommand,
    status: &str,
    error_code: &str,
) -> bool {
    let mut pending = load_pending_update_commands();
    let Some(state) = pending
        .iter_mut()
        .find(|state| state.command.command_id == command.command_id)
    else {
        log::warn!("Missing pending update command {}", command.command_id);
        return false;
    };
    state.set_terminal(status, error_code);
    if let Err(err) = store_pending_update_commands(&pending) {
        log::error!("Failed to persist update command terminal state: {err}");
        return false;
    }
    if report_update_command_event(command, status, error_code) {
        complete_update_command(command);
        true
    } else {
        schedule_update_command_terminal_retry(command.command_id.clone());
        false
    }
}

fn retry_update_command_terminal(command_id: &str) {
    let Some(state) = load_pending_update_commands()
        .into_iter()
        .find(|state| state.command.command_id == command_id)
    else {
        return;
    };
    if let Some(terminal) = state.terminal {
        if report_update_command_event(&state.command, &terminal.status, &terminal.error_code) {
            complete_update_command(&state.command);
        } else {
            schedule_update_command_terminal_retry(state.command.command_id);
        }
    }
}

fn report_update_command_deferred_once(command: &UpdateCommand) {
    let mut pending = load_pending_update_commands();
    let Some(state) = pending
        .iter_mut()
        .find(|state| state.command.command_id == command.command_id)
    else {
        return;
    };
    if !state.mark_deferred_reported() {
        return;
    }
    if let Err(err) = store_pending_update_commands(&pending) {
        log::error!("Failed to persist deferred update command state: {err}");
        return;
    }
    report_update_command_event(command, "deferred", "active_session");
}

fn schedule_update_command_terminal_retry(command_id: String) {
    std::thread::spawn(move || {
        std::thread::sleep(UPDATE_COMMAND_RETRY_INTERVAL);
        retry_update_command_terminal(&command_id);
    });
}

#[cfg(test)]
mod policy_stream_tests {
    use super::*;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::{
            atomic::{AtomicBool, Ordering},
            mpsc, Arc,
        },
        thread,
    };

    #[test]
    fn policy_stream_sends_resume_state_and_delivers_real_sse() {
        assert!(POLICY_EOF_RECONNECT_DELAY <= Duration::from_secs(1));
        assert!(POLICY_ERROR_RECONNECT_DELAY <= Duration::from_secs(2));
        let listener = TcpListener::bind("127.0.0.1:0").expect("fixture should bind");
        let address = listener.local_addr().expect("fixture should have address");
        let fixture = thread::spawn(move || {
            let (mut socket, _) = listener.accept().expect("client should connect");
            let mut request = [0_u8; 4096];
            let size = socket.read(&mut request).expect("request should read");
            let request = String::from_utf8_lossy(&request[..size]).into_owned();
            let body = concat!(
                "event: update-policy\n",
                "id: 4\n",
                "data: {\"client_id\":\"83077683\",\"client_uuid\":\"01ab\",",
                "\"policy_revision\":4,\"enable_check_update\":true,",
                "\"allow_auto_update\":false}\n\n"
            );
            write!(
                socket,
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .expect("response should write");
            request
        });
        let identity = update_client_identity("83077683", "01ab");
        let (tx, rx) = mpsc::channel();

        read_update_policy_stream_once(
            &format!("http://{address}"),
            &identity,
            Some(3),
            || {},
            |policy| tx.send(policy).expect("policy should send"),
            |_| {},
        )
        .expect("stream should complete");

        let request = fixture.join().expect("fixture should finish");
        assert!(request.contains("GET /rd/update/v1/policy/stream?client_id=83077683&client_uuid=01ab&after_revision=3 HTTP/1.1"));
        assert!(request.to_ascii_lowercase().contains("last-event-id: 3"));
        assert_eq!(rx.recv().expect("policy should arrive").revision, 4);
    }

    #[test]
    fn fresh_policy_stream_omits_resume_query_and_header() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("fixture should bind");
        let address = listener.local_addr().expect("fixture should have address");
        let fixture = thread::spawn(move || {
            let (mut socket, _) = listener.accept().expect("client should connect");
            let mut request = [0_u8; 4096];
            let size = socket.read(&mut request).expect("request should read");
            let request = String::from_utf8_lossy(&request[..size]).into_owned();
            let body = ": heartbeat\n\n";
            write!(
                socket,
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .expect("response should write");
            request
        });
        let identity = update_client_identity("83077683", "01ab");
        let connected = Arc::new(AtomicBool::new(false));
        let connected_for_callback = Arc::clone(&connected);

        read_update_policy_stream_once(
            &format!("http://{address}"),
            &identity,
            None,
            move || connected_for_callback.store(true, Ordering::SeqCst),
            |_| {},
            |_| {},
        )
        .expect("stream should complete");

        let request = fixture.join().expect("fixture should finish");
        assert!(request.contains(
            "GET /rd/update/v1/policy/stream?client_id=83077683&client_uuid=01ab HTTP/1.1"
        ));
        assert!(!request.to_ascii_lowercase().contains("last-event-id:"));
        assert!(!request.contains("after_revision="));
        assert!(connected.load(Ordering::SeqCst));
    }

    #[test]
    fn policy_stream_retries_unsigned_when_device_key_is_not_registered() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("fixture should bind");
        let address = listener.local_addr().expect("fixture should have address");
        let fixture = thread::spawn(move || {
            let mut requests = Vec::new();
            for status in [401, 200] {
                let (mut socket, _) = listener.accept().expect("client should connect");
                let mut request = [0_u8; 4096];
                let size = socket.read(&mut request).expect("request should read");
                requests.push(String::from_utf8_lossy(&request[..size]).into_owned());
                let body = if status == 200 {
                    concat!(
                        "event: update-policy\n",
                        "id: 1\n",
                        "data: {\"client_id\":\"83077683\",\"client_uuid\":\"01ab\",",
                        "\"policy_revision\":1,\"enable_check_update\":true,",
                        "\"allow_auto_update\":false}\n\n",
                        "event: update-command\n",
                        "id: unsigned-command\n",
                        "data: {\"command_id\":\"unsigned-command\",\"action\":\"check\",",
                        "\"client_id\":\"83077683\",\"client_uuid\":\"01ab\",",
                        "\"expires_at\":4102444800}\n\n"
                    )
                } else {
                    ""
                };
                write!(
                    socket,
                    "HTTP/1.1 {status} {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    if status == 200 { "OK" } else { "Unauthorized" },
                    body.len(),
                    body
                )
                .expect("response should write");
            }
            requests
        });
        let identity = update_client_identity("83077683", "01ab");
        let (tx, rx) = mpsc::channel();
        let (command_tx, command_rx) = mpsc::channel();

        read_update_policy_stream_once(
            &format!("http://{address}"),
            &identity,
            None,
            || {},
            |policy| tx.send(policy).expect("policy should send"),
            |command| command_tx.send(command).expect("command should send"),
        )
        .expect("unsigned fallback should complete");

        let requests = fixture.join().expect("fixture should finish");
        assert!(requests[0]
            .to_ascii_lowercase()
            .contains("x-rustdesk-device-signature:"));
        assert!(!requests[1]
            .to_ascii_lowercase()
            .contains("x-rustdesk-device-signature:"));
        assert_eq!(rx.recv().expect("policy should arrive").revision, 1);
        assert!(command_rx.try_recv().is_err());
    }

    #[test]
    fn policy_stream_delivers_real_update_command_sse() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("fixture should bind");
        let address = listener.local_addr().expect("fixture should have address");
        let fixture = thread::spawn(move || {
            let (mut socket, _) = listener.accept().expect("client should connect");
            let mut request = [0_u8; 4096];
            let _ = socket.read(&mut request).expect("request should read");
            let body = concat!(
                "event: update-command\n",
                "id: cmd-install-1\n",
                "data: {\"command_id\":\"cmd-install-1\",\"action\":\"install\",",
                "\"client_id\":\"83077683\",\"client_uuid\":\"01ab\",",
                "\"target_version\":\"1.5.0\",\"target_build_seq\":50,",
                "\"expires_at\":4102444800}\n\n"
            );
            write!(
                socket,
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .expect("response should write");
        });
        let identity = update_client_identity("83077683", "01ab");
        let (tx, rx) = mpsc::channel();

        read_update_policy_stream_once(
            &format!("http://{address}"),
            &identity,
            Some(3),
            || {},
            |_| {},
            |command| tx.send(command).expect("command should send"),
        )
        .expect("stream should complete");

        fixture.join().expect("fixture should finish");
        let command = rx.recv().expect("command should arrive");
        assert_eq!(command.command_id, "cmd-install-1");
        assert_eq!(command.action, UpdateCommandAction::Install);
    }

    #[test]
    fn command_history_persists_all_ids_and_deduplicates_reconnects() {
        let mut history = ProcessedUpdateCommands::default();
        for index in 0..40 {
            history.remember(format!("cmd-{index}"));
        }

        assert!(history.contains("cmd-0"));
        assert!(history.contains("cmd-39"));
        assert_eq!(history.ids.len(), 40);
        history.remember("cmd-39".to_owned());
        assert_eq!(history.ids.len(), 40);
    }

    #[test]
    fn only_install_commands_defer_for_active_sessions() {
        assert!(DEFERRED_UPDATE_RETRY_INTERVAL <= Duration::from_secs(30));
        assert!(!update_command_should_defer(
            UpdateCommandAction::Check,
            true
        ));
        assert!(update_command_should_defer(
            UpdateCommandAction::Install,
            true
        ));
        assert!(!update_command_should_defer(
            UpdateCommandAction::Install,
            false
        ));
    }

    #[test]
    fn command_target_requires_exact_manifest_version_and_build() {
        assert!(update_command_matches_target("1.5.0", 50, "1.5.0", 50));
        assert!(!update_command_matches_target("1.5.0", 50, "1.5.1", 50));
        assert!(!update_command_matches_target("1.5.0", 50, "1.5.0", 51));
    }

    #[test]
    fn command_outcomes_distinguish_check_install_and_async_completion() {
        assert_ne!(
            UpdateCommandRunOutcome::NoUpdate,
            UpdateCommandRunOutcome::CheckCompleted
        );
        assert_ne!(
            UpdateCommandRunOutcome::Deferred,
            UpdateCommandRunOutcome::AwaitingInstallResult
        );
        assert_ne!(
            UpdateCommandRunOutcome::AwaitingInstallResult,
            UpdateCommandRunOutcome::Installed
        );
    }

    #[test]
    fn device_auth_headers_sign_the_exact_request_body() {
        let identity = base::update::UpdateClientIdentity {
            client_id: "83077683".to_owned(),
            client_uuid: "01ab".to_owned(),
        };
        let body = br#"{"command_id":"cmd-1"}"#;
        let headers = update_device_auth_headers(
            "POST",
            "https://rdapi.yan.life/rd/update/v1/check?ignored=true",
            &identity,
            "cmd-1",
            body,
        )
        .expect("headers should be signed");
        let canonical = base::update::update_device_auth_payload(
            "POST",
            "/rd/update/v1/check",
            &identity.client_id,
            &identity.client_uuid,
            headers.timestamp.parse().expect("timestamp should parse"),
            &headers.nonce,
            "cmd-1",
            &hex::encode(Sha256::digest(body)),
        );
        let public_key = hbb_common::sodiumoxide::crypto::sign::PublicKey::from_slice(
            &STANDARD
                .decode(headers.public_key)
                .expect("public key should decode"),
        )
        .expect("public key should be valid");
        let signature = hbb_common::sodiumoxide::crypto::sign::Signature::from_bytes(
            &STANDARD
                .decode(headers.signature)
                .expect("signature should decode"),
        )
        .expect("signature should be valid");

        assert!(!headers.device_id.is_empty());
        assert!(hbb_common::sodiumoxide::crypto::sign::verify_detached(
            &signature,
            &canonical,
            &public_key,
        ));
    }
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
    let pending = load_pending_update_commands();
    let startup_tx = tx.clone();
    std::thread::spawn(move || start_auto_update_check_(rx));
    for state in pending {
        if state.terminal.is_some() {
            retry_update_command_terminal(&state.command.command_id);
        } else {
            let _ = startup_tx.send(UpdateMsg::Command(state.command));
        }
    }
    return tx;
}

fn start_auto_update_check_(rx_msg: Receiver<UpdateMsg>) {
    let started_at = Instant::now();
    let mut startup_check_pending = true;
    let mut last_check_time = None;
    let mut next_scheduled_check = scheduled_update_interval().map(|value| started_at + value);
    loop {
        let now = Instant::now();
        let startup_deadline = startup_check_pending.then_some(started_at + INITIAL_CHECK_DELAY);
        let next_deadline = [startup_deadline, next_scheduled_check]
            .into_iter()
            .flatten()
            .min();
        let wait = next_deadline
            .map(|deadline| deadline.saturating_duration_since(now))
            .unwrap_or(Duration::from_secs(60 * 60 * 24));
        match rx_msg.recv_timeout(wait) {
            Ok(UpdateMsg::CheckUpdate) => {
                run_update_check(
                    true,
                    "manual",
                    &mut last_check_time,
                    &mut next_scheduled_check,
                );
            }
            Ok(UpdateMsg::ConnectivityRestored) => {
                if last_check_time
                    .map(|value: Instant| value.elapsed() >= MIN_INTERVAL)
                    .unwrap_or(true)
                {
                    run_update_check(
                        false,
                        "system",
                        &mut last_check_time,
                        &mut next_scheduled_check,
                    );
                }
            }
            Ok(UpdateMsg::ScheduleChanged) => {
                next_scheduled_check =
                    scheduled_update_interval().map(|value| Instant::now() + value);
            }
            Ok(UpdateMsg::Command(command)) => {
                let identity = current_update_client_identity();
                let history = load_processed_update_commands();
                match command.accepted_decision(&identity, history.contains(&command.command_id)) {
                    UpdateCommandDecision::IdentityMismatch => complete_update_command(&command),
                    UpdateCommandDecision::Duplicate => complete_update_command(&command),
                    UpdateCommandDecision::Expired | UpdateCommandDecision::Execute => {
                        if update_command_should_defer(command.action, !has_no_active_conns()) {
                            report_update_command_deferred_once(&command);
                            schedule_update_command_retry(command.clone());
                            continue;
                        }
                        report_update_command_event(&command, "started", "");
                        let result = check_update_for_command(&command);
                        match result {
                            Ok(UpdateCommandRunOutcome::NoUpdate) => {
                                persist_and_report_update_command_terminal(
                                    &command,
                                    "no_update",
                                    "",
                                );
                            }
                            Ok(UpdateCommandRunOutcome::CheckCompleted) => {
                                persist_and_report_update_command_terminal(
                                    &command,
                                    "completed",
                                    "",
                                );
                            }
                            Ok(UpdateCommandRunOutcome::Deferred) => {
                                report_update_command_deferred_once(&command);
                                schedule_update_command_retry(command.clone());
                            }
                            Ok(UpdateCommandRunOutcome::AwaitingInstallResult) => {}
                            Ok(UpdateCommandRunOutcome::Installed) => {
                                persist_and_report_update_command_terminal(
                                    &command,
                                    "completed",
                                    "",
                                );
                            }
                            Err(err) => {
                                log::error!("Update command {} failed: {err}", command.command_id);
                                persist_and_report_update_command_terminal(
                                    &command,
                                    "failed",
                                    "command_failed",
                                );
                            }
                        }
                    }
                }
            }
            Ok(UpdateMsg::Exit) => break,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                let now = Instant::now();
                let startup_due =
                    startup_check_pending && now.duration_since(started_at) >= INITIAL_CHECK_DELAY;
                if startup_due {
                    startup_check_pending = false;
                }
                let scheduled_due = next_scheduled_check
                    .map(|deadline| now >= deadline)
                    .unwrap_or(false);
                if startup_due && startup_update_enabled() {
                    run_update_check(
                        false,
                        "startup",
                        &mut last_check_time,
                        &mut next_scheduled_check,
                    );
                } else if scheduled_due {
                    run_update_check(
                        false,
                        "system",
                        &mut last_check_time,
                        &mut next_scheduled_check,
                    );
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
}

fn startup_update_enabled() -> bool {
    should_run_startup_update(update_option_enabled(&config::LocalConfig::get_option(
        keys::OPTION_ENABLE_CHECK_UPDATE,
    )))
}

fn scheduled_update_interval() -> Option<Duration> {
    let hours = config::Config::get_option(keys::OPTION_SCHEDULED_UPDATE_INTERVAL_HOURS)
        .parse()
        .unwrap_or(DEFAULT_SCHEDULED_UPDATE_INTERVAL_HOURS);
    scheduled_update_delay(
        update_option_enabled(&config::Config::get_option(
            keys::OPTION_ENABLE_SCHEDULED_UPDATE,
        )),
        hours,
    )
}

fn scheduled_update_delay(enabled: bool, hours: u64) -> Option<Duration> {
    if !should_run_scheduled_update(enabled) {
        return None;
    }
    Some(Duration::from_secs(
        normalize_scheduled_update_interval_hours(hours) * 60 * 60,
    ))
}

fn run_update_check(
    manually: bool,
    request_origin: &str,
    last_check_time: &mut Option<Instant>,
    next_scheduled_check: &mut Option<Instant>,
) {
    match check_update_request(manually, None, request_origin) {
        Ok(UpdateCommandRunOutcome::Deferred) => {
            *next_scheduled_check = Some(Instant::now() + DEFERRED_UPDATE_RETRY_INTERVAL);
            schedule_update_check_retry(manually, DEFERRED_UPDATE_RETRY_INTERVAL);
        }
        Ok(_) => {
            *last_check_time = Some(Instant::now());
            *next_scheduled_check = scheduled_update_interval().map(|value| Instant::now() + value);
        }
        Err(err) => {
            log::error!("Error checking for updates: {err}");
            *next_scheduled_check = Some(Instant::now() + RETRY_INTERVAL);
            schedule_update_check_retry(manually, RETRY_INTERVAL);
        }
    }
}

fn schedule_update_check_retry(manually: bool, delay: Duration) {
    std::thread::spawn(move || {
        std::thread::sleep(delay);
        let sender = TX_MSG.lock().unwrap();
        let message = if manually {
            UpdateMsg::CheckUpdate
        } else {
            UpdateMsg::ConnectivityRestored
        };
        let _ = sender.send(message);
    });
}

fn schedule_update_command_retry(command: UpdateCommand) {
    std::thread::spawn(move || {
        std::thread::sleep(UPDATE_COMMAND_RETRY_INTERVAL);
        let sender = TX_MSG.lock().unwrap();
        let _ = sender.send(UpdateMsg::Command(command));
    });
}

fn check_update_for_command(command: &UpdateCommand) -> ResultType<UpdateCommandRunOutcome> {
    check_update_request(false, Some(command), "command")
}

fn check_update_request(
    manually: bool,
    command: Option<&UpdateCommand>,
    request_origin: &str,
) -> ResultType<UpdateCommandRunOutcome> {
    // On macOS, auto-update is handled by check_update_as_root() in the service process.
    // The shared check_update() path is only used for manual update checks from the GUI.
    #[cfg(target_os = "macos")]
    if !manually && command.is_none() && request_origin != "startup" {
        return Ok(UpdateCommandRunOutcome::NoUpdate);
    }
    let command_result = if let Some(command) = command {
        let request_origin = if command.action == UpdateCommandAction::Install {
            "command_install"
        } else {
            "command"
        };
        Some(do_check_software_update_with_context_result(
            request_origin,
            &command.command_id,
        )?)
    } else {
        do_check_software_update_with_context(request_origin, "")?;
        None
    };

    let response = command_result
        .as_ref()
        .and_then(|result| result.response.clone())
        .or_else(|| {
            crate::common::SOFTWARE_UPDATE_RESPONSE
                .lock()
                .unwrap()
                .clone()
        });
    let Some(response) = response else {
        log::debug!("No update available.");
        return Ok(UpdateCommandRunOutcome::NoUpdate);
    };
    if !response.update_available || (response.mode == "disabled" && command.is_none()) {
        return Ok(UpdateCommandRunOutcome::NoUpdate);
    }
    let Some(ref manifest) = response.manifest else {
        bail!("update response is missing a signed manifest");
    };
    let target_key = command_result
        .as_ref()
        .map(|result| result.target_key.clone())
        .unwrap_or_else(|| {
            crate::common::SOFTWARE_UPDATE_TARGET_KEY
                .lock()
                .unwrap()
                .clone()
        });
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
    let target = manifest.targets.get(&target_key).cloned().ok_or_else(|| {
        hbb_common::anyhow::anyhow!("signed manifest is missing the selected update target")
    })?;
    let (target_version, target_build_seq, _) = update_target_release(manifest, &target)?;
    if let Some(command) = command.filter(|value| value.action == UpdateCommandAction::Install) {
        let command_version = command.target_version.as_deref().ok_or_else(|| {
            hbb_common::anyhow::anyhow!("install command target version is missing")
        })?;
        let command_build_seq = command.target_build_seq.ok_or_else(|| {
            hbb_common::anyhow::anyhow!("install command target build is missing")
        })?;
        if !update_command_matches_target(
            command_version,
            command_build_seq,
            target_version,
            target_build_seq,
        ) {
            bail!("update command target does not match signed manifest");
        }
    }
    let mut action = decide_update_action(
        &response,
        update_option_enabled(&config::Config::get_option(keys::OPTION_ALLOW_AUTO_UPDATE)),
    );
    if manually && response.update_available && response.mode != "disabled" {
        action = UpdateAction::AutoInstall;
    }
    if let Some(command) = command {
        action = match command.action {
            UpdateCommandAction::Check => UpdateAction::Notify,
            UpdateCommandAction::Install => UpdateAction::AutoInstall,
        };
    }
    if matches!(action, UpdateAction::Ignore | UpdateAction::Notify) {
        return Ok(if command.is_some() {
            UpdateCommandRunOutcome::CheckCompleted
        } else {
            UpdateCommandRunOutcome::NoUpdate
        });
    }
    let should_install = action == UpdateAction::AutoInstall;
    let version = target_version;
    report_update_event("started", version, target_build_seq, "none");
    let (file_path, source) = match download_verified_target(manifest, &target_key, &target) {
        Ok(downloaded) => downloaded,
        Err(err) => {
            report_update_event("failed", version, target_build_seq, "none");
            report_command_update_event(command, "failed", "all_sources_failed");
            return Err(err);
        }
    };
    let source = source.as_str();
    report_update_event("downloaded", version, target_build_seq, source);
    report_command_update_event(command, "downloaded", "");
    {
        #[cfg(target_os = "windows")]
        log::debug!("New version available: {}", version);
        // Recheck because a session can start while the verified asset is downloading.
        if should_install && has_no_active_conns() {
            report_update_event("installing", version, target_build_seq, source);
            report_command_update_event(command, "installing", "");
            #[cfg(target_os = "windows")]
            update_new_version(
                update_msi,
                version,
                target_build_seq,
                source,
                &file_path,
                command.map(|value| value.command_id.as_str()),
            )?;
            #[cfg(target_os = "windows")]
            if update_msi {
                report_command_update_event(command, "installed", "");
            }
            #[cfg(target_os = "linux")]
            if let Err(err) = install_linux_appimage(&file_path) {
                log::error!("Failed to install AppImage update: {}", err);
                report_update_event("rolled_back", version, target_build_seq, source);
                remove_download_artifact(&file_path);
                return Err(err);
            } else {
                report_update_event("installed", version, target_build_seq, source);
                remove_download_artifact(&file_path);
            }
            #[cfg(target_os = "macos")]
            if let Some(path) = file_path.to_str() {
                let event = PendingUpdateEvent {
                    transaction_id: new_update_transaction_id(),
                    from_version: crate::VERSION.to_owned(),
                    from_build_seq: crate::BUILD_SEQ,
                    version: version.to_owned(),
                    build_seq: target_build_seq,
                    source: if source == UpdateSource::Mirror.as_str() {
                        UpdateSource::Mirror
                    } else {
                        UpdateSource::Primary
                    },
                    command_id: command.map(|value| value.command_id.clone()),
                };
                if let Err(err) = crate::platform::request_update_from_dmg_as_root(path, &event) {
                    log::error!("Failed to install verified macOS update: {}", err);
                    report_update_event(
                        classify_update_preinstall_failure(&err.to_string()),
                        version,
                        target_build_seq,
                        source,
                    );
                    remove_download_artifact(&file_path);
                    return Err(err);
                }
                remove_download_artifact(&file_path);
            } else {
                remove_download_artifact(&file_path);
                bail!("downloaded update path is not valid UTF-8");
            }
        } else if should_install {
            report_update_event("deferred", version, target_build_seq, source);
            remove_download_artifact(&file_path);
            return Ok(UpdateCommandRunOutcome::Deferred);
        }
    }
    #[cfg(target_os = "windows")]
    if should_install && !update_msi {
        return Ok(UpdateCommandRunOutcome::AwaitingInstallResult);
    }
    #[cfg(target_os = "macos")]
    if should_install {
        return Ok(UpdateCommandRunOutcome::AwaitingInstallResult);
    }
    Ok(if should_install {
        UpdateCommandRunOutcome::Installed
    } else {
        UpdateCommandRunOutcome::CheckCompleted
    })
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

fn report_command_update_event(command: Option<&UpdateCommand>, status: &str, error_code: &str) {
    if let Some(command) = command {
        report_update_command_event(command, status, error_code);
    }
}

fn report_update_command_event(command: &UpdateCommand, status: &str, error_code: &str) -> bool {
    const EVENTS_URL: &str = "https://rdapi.yan.life/rd/update/v1/events";
    let Ok(client) = create_http_client_with_url_strict(EVENTS_URL) else {
        log::warn!("Failed to create update command event HTTP client");
        return false;
    };
    let identity = current_update_client_identity();
    let payload = serde_json::json!({
        "client_id": identity.client_id,
        "client_uuid": identity.client_uuid,
        "command_id": command.command_id,
        "command_action": command.action,
        "status": status,
        "from_version": crate::VERSION,
        "from_build_seq": crate::BUILD_SEQ,
        "product": crate::PRODUCT,
        "edition": crate::EDITION,
        "build_number": crate::BUILD_NUMBER,
        "channel": crate::CHANNEL,
        "source_commit": crate::SOURCE_COMMIT,
        "to_version": command.target_version,
        "to_build_seq": command.target_build_seq,
        "error_code": error_code,
        "source": "none",
    });
    let Ok(body) = serde_json::to_vec(&payload) else {
        log::warn!("Failed to serialize update command event");
        return false;
    };
    let Ok(auth) =
        update_device_auth_headers("POST", EVENTS_URL, &identity, &command.command_id, &body)
    else {
        log::warn!("Failed to sign update command event");
        return false;
    };
    match client
        .post(EVENTS_URL)
        .header("Content-Type", "application/json")
        .header("X-RustDesk-Device-ID", auth.device_id)
        .header("X-RustDesk-Device-Public-Key", auth.public_key)
        .header("X-RustDesk-Device-Timestamp", auth.timestamp)
        .header("X-RustDesk-Device-Nonce", auth.nonce)
        .header("X-RustDesk-Device-Signature", auth.signature)
        .body(body)
        .send()
    {
        Ok(response) if response.status().is_success() => true,
        Ok(response) => {
            log::warn!(
                "Update command event rejected with HTTP {}",
                response.status()
            );
            false
        }
        Err(err) => {
            log::warn!("Failed to report update command event: {err}");
            false
        }
    }
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
    let identity = current_update_client_identity();
    let payload = serde_json::json!({
        "client_id": identity.client_id,
        "client_uuid": identity.client_uuid,
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
    let Ok(body) = serde_json::to_vec(&payload) else {
        log::warn!("Failed to serialize update event");
        return false;
    };
    let Ok(auth) = update_device_auth_headers("POST", EVENTS_URL, &identity, "", &body) else {
        log::warn!("Failed to sign update event");
        return false;
    };
    match client
        .post(EVENTS_URL)
        .header("Content-Type", "application/json")
        .header("X-RustDesk-Device-ID", auth.device_id)
        .header("X-RustDesk-Device-Public-Key", auth.public_key)
        .header("X-RustDesk-Device-Timestamp", auth.timestamp)
        .header("X-RustDesk-Device-Nonce", auth.nonce)
        .header("X-RustDesk-Device-Signature", auth.signature)
        .body(body)
        .send()
    {
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

pub(crate) fn report_pending_update_terminal(status: &str, event: &PendingUpdateEvent) -> bool {
    let command_reported = if let Some(command_id) = &event.command_id {
        let state = load_pending_update_commands()
            .into_iter()
            .find(|state| state.command.command_id == *command_id);
        if state.is_none() && load_processed_update_commands().contains(command_id) {
            true
        } else if let Some(state) = state {
            let (terminal_status, error_code) = if status == "installed" {
                ("completed", "")
            } else {
                ("failed", status)
            };
            report_update_command_event(&state.command, status, error_code);
            persist_and_report_update_command_terminal(&state.command, terminal_status, error_code)
        } else {
            log::warn!("Missing pending update command {command_id}");
            return false;
        }
    } else {
        true
    };
    let update_reported = report_update_event_with_origin(
        status,
        &event.from_version,
        event.from_build_seq,
        &event.version,
        event.build_seq,
        event.source.as_str(),
    );
    update_reported && command_reported
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
    command_id: Option<&str>,
) -> ResultType<()> {
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
                        return Err(e.into());
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
                    command_id: command_id.map(str::to_owned),
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
                        return Err(e.into());
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
                    bail!("failed to launch the downloaded updater");
                }
            }
        } else {
            log::error!(
                "Failed to get the current process session id, Error {}",
                std::io::Error::last_os_error()
            );
            remove_download_artifact(&file_path);
            bail!("current process session is unavailable");
        }
    } else {
        // unreachable!()
        log::error!(
            "Failed to convert the file path to string: {}",
            file_path.display()
        );
        remove_download_artifact(file_path);
        bail!("downloaded update path is not valid UTF-8");
    }
    Ok(())
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
    start_update_policy_stream();
    let (schedule_tx, schedule_rx) = channel();
    *MAC_SCHEDULER_WAKE.lock().unwrap() = Some(schedule_tx);
    let spawn_result = std::thread::Builder::new()
        .name("rustdesk-auto-update".to_owned())
        .spawn(move || {
            log::info!("[root-update] Auto-update scheduler thread started.");
            consume_mac_update_result();
            std::thread::sleep(INITIAL_CHECK_DELAY);
            wait_for_failed_update_retry();
            loop {
                log::info!("[root-update] Running scheduled update check...");
                let no_active_conns = has_no_active_conns_ipc();
                let interval = if !no_active_conns {
                    log::info!("[root-update] Active session in progress, retrying in 10 min.");
                    Some(MIN_INTERVAL)
                } else {
                    match check_update_as_root() {
                        Ok(update_started) => {
                            if update_started {
                                // The replacement script is detached and may fail
                                // after this process returns. Always retry at the
                                // failure interval until the new daemon replaces us.
                                Some(RETRY_INTERVAL)
                            } else {
                                scheduled_update_interval()
                            }
                        }
                        Err(e) => {
                            log::error!("[root-update] Update check failed: {}", e);
                            Some(RETRY_INTERVAL)
                        }
                    }
                };
                if wait_for_mac_schedule_change(&schedule_rx, interval) {
                    log::info!("[root-update] Update policy changed; recalculating schedule.");
                }
            }
        });
    if let Err(err) = spawn_result {
        log::error!("[root-update] Failed to start scheduler thread: {}", err);
    }
}

#[cfg(target_os = "macos")]
fn wait_for_mac_schedule_change(rx: &Receiver<()>, interval: Option<Duration>) -> bool {
    match interval {
        Some(interval) => rx.recv_timeout(interval).is_ok(),
        None => rx.recv().is_ok(),
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
        if report_pending_update_terminal(&result.status, &result.event) {
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
    if !update_option_enabled(&config::Config::get_option(keys::OPTION_ALLOW_AUTO_UPDATE)) {
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
    let (target_version, target_build_seq, _) = update_target_release(manifest, &target)?;
    let action = decide_update_action(
        &response,
        update_option_enabled(&config::Config::get_option(keys::OPTION_ALLOW_AUTO_UPDATE)),
    );
    if action != UpdateAction::AutoInstall {
        return Ok(false);
    }
    let version = target_version.to_owned();
    report_update_event("started", &version, target_build_seq, "none");
    let (file_path, source) = match download_verified_target(manifest, &target_key, &target) {
        Ok(downloaded) => downloaded,
        Err(err) => {
            report_update_event("failed", &version, target_build_seq, "none");
            return Err(err);
        }
    };
    let _download_cleanup = DownloadArtifactCleanup(file_path.clone());
    let source = source.as_str();
    report_update_event("downloaded", &version, target_build_seq, source);
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
        report_update_event("deferred", &version, target_build_seq, source);
        bail!("[root-update] Active session started during download, deferring update.");
    }
    // Install silently as root
    report_update_event("installing", &version, target_build_seq, source);
    let event = PendingUpdateEvent {
        transaction_id: new_update_transaction_id(),
        from_version: crate::VERSION.to_owned(),
        from_build_seq: crate::BUILD_SEQ,
        version: version.clone(),
        build_seq: target_build_seq,
        source: if source == UpdateSource::Mirror.as_str() {
            UpdateSource::Mirror
        } else {
            UpdateSource::Primary
        },
        command_id: None,
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
                target_build_seq,
                source,
            );
            Err(err)
        }
    }
}

#[cfg(test)]
mod tests {
    #[cfg(target_os = "macos")]
    use super::wait_for_mac_schedule_change;
    use super::{
        get_download_file_from_url, scheduled_update_delay, update_filename_from_url,
        verify_detached_signature, UpdateDownloadStaging,
    };
    use std::time::Duration;

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

    #[test]
    fn scheduled_update_delay_honors_policy_and_bounds() {
        assert_eq!(scheduled_update_delay(false, 5), None);
        assert_eq!(
            scheduled_update_delay(true, 1),
            Some(Duration::from_secs(60 * 60))
        );
        assert_eq!(
            scheduled_update_delay(true, 5),
            Some(Duration::from_secs(5 * 60 * 60))
        );
        assert_eq!(
            scheduled_update_delay(true, 168),
            Some(Duration::from_secs(168 * 60 * 60))
        );
        assert_eq!(
            scheduled_update_delay(true, 0),
            Some(Duration::from_secs(60 * 60))
        );
        assert_eq!(
            scheduled_update_delay(true, 999),
            Some(Duration::from_secs(168 * 60 * 60))
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_scheduler_wakes_when_policy_changes() {
        let (tx, rx) = std::sync::mpsc::channel();
        tx.send(()).expect("policy wake should be delivered");
        assert!(wait_for_mac_schedule_change(
            &rx,
            Some(Duration::from_secs(60 * 60))
        ));
    }
}
