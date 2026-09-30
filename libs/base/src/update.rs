use hbb_common::{is_newer_version, UpdateManifest, VersionCheckResponse};
use std::{fs, io, path::Path};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdateAction {
    Ignore,
    Notify,
    Download,
    AutoInstall,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdateSource {
    Primary,
    Mirror,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingUpdateEvent {
    pub transaction_id: String,
    pub from_version: String,
    pub from_build_seq: u64,
    pub version: String,
    pub build_seq: u64,
    pub source: UpdateSource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MacUpdateResult {
    pub status: String,
    pub event: PendingUpdateEvent,
}

impl MacUpdateResult {
    pub fn encode(&self) -> anyhow::Result<String> {
        anyhow::ensure!(
            self.event.transaction_id.len() == 32
                && self
                    .event
                    .transaction_id
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit()),
            "invalid update transaction id"
        );
        for value in [
            self.event.from_version.as_str(),
            self.event.version.as_str(),
        ] {
            anyhow::ensure!(
                !value.is_empty()
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric()
                            || matches!(byte, b'.' | b'_' | b'-')),
                "invalid update result version"
            );
        }
        anyhow::ensure!(
            matches!(
                self.status.as_str(),
                "pending" | "installed" | "rolled_back" | "rollback_failed"
            ),
            "invalid update result status"
        );
        Ok(format!(
            "transaction_id={}\nstatus={}\nfrom_version={}\nfrom_build_seq={}\nto_version={}\nto_build_seq={}\nsource={}\n",
            self.event.transaction_id,
            self.status,
            self.event.from_version,
            self.event.from_build_seq,
            self.event.version,
            self.event.build_seq,
            self.event.source.as_str(),
        ))
    }

    pub fn decode(value: &str) -> Option<Self> {
        let fields = value
            .lines()
            .map(|line| line.split_once('='))
            .collect::<Option<std::collections::HashMap<_, _>>>()?;
        if fields.len() != 7 {
            return None;
        }
        let transaction_id = *fields.get("transaction_id")?;
        if transaction_id.len() != 32
            || !transaction_id.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return None;
        }
        let status = *fields.get("status")?;
        if !matches!(
            status,
            "pending" | "installed" | "rolled_back" | "rollback_failed"
        ) {
            return None;
        }
        let source = match *fields.get("source")? {
            "primary" => UpdateSource::Primary,
            "mirror" => UpdateSource::Mirror,
            _ => return None,
        };
        let from_version = (*fields.get("from_version")?).to_owned();
        let version = (*fields.get("to_version")?).to_owned();
        if from_version.is_empty() || version.is_empty() {
            return None;
        }
        Some(Self {
            status: status.to_owned(),
            event: PendingUpdateEvent {
                transaction_id: transaction_id.to_owned(),
                from_version,
                from_build_seq: fields.get("from_build_seq")?.parse().ok()?,
                version,
                build_seq: fields.get("to_build_seq")?.parse().ok()?,
                source,
            },
        })
    }
}

pub fn classify_update_preinstall_failure(message: &str) -> &'static str {
    if message.to_ascii_lowercase().contains("active session")
        || message.to_ascii_lowercase().contains("deferring")
    {
        "deferred"
    } else {
        "failed"
    }
}

impl PendingUpdateEvent {
    const ARG: &'static str = "--update-event";

    pub fn cli_args(&self) -> Vec<String> {
        vec![
            Self::ARG.to_owned(),
            self.transaction_id.clone(),
            self.from_version.clone(),
            self.from_build_seq.to_string(),
            self.version.clone(),
            self.build_seq.to_string(),
            self.source.as_str().to_owned(),
        ]
    }

    pub fn from_cli_args(args: &[String]) -> Option<Self> {
        let index = args.iter().position(|arg| arg == Self::ARG)?;
        let transaction_id = args.get(index + 1)?.trim();
        let from_version = args.get(index + 2)?.trim();
        let from_build_seq = args.get(index + 3)?.parse().ok()?;
        let version = args.get(index + 4)?.trim();
        let build_seq = args.get(index + 5)?.parse().ok()?;
        let source = match args.get(index + 6)?.as_str() {
            "primary" => UpdateSource::Primary,
            "mirror" => UpdateSource::Mirror,
            _ => return None,
        };
        if transaction_id.len() != 32
            || !transaction_id.bytes().all(|byte| byte.is_ascii_hexdigit())
            || from_version.is_empty()
            || version.is_empty()
        {
            return None;
        }
        Some(Self {
            transaction_id: transaction_id.to_owned(),
            from_version: from_version.to_owned(),
            from_build_seq,
            version: version.to_owned(),
            build_seq,
            source,
        })
    }
}

impl UpdateSource {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Primary => "primary",
            Self::Mirror => "mirror",
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct UpdateSourceFailure<E> {
    pub source: UpdateSource,
    pub url: String,
    pub error: E,
}

pub fn decide_update_action(
    response: &VersionCheckResponse,
    local_auto_update: bool,
) -> UpdateAction {
    if !response.update_available || response.mode == "disabled" {
        return UpdateAction::Ignore;
    }
    match response.mode.as_str() {
        "notify" => UpdateAction::Notify,
        "auto_install" if response.auto_install && local_auto_update => UpdateAction::AutoInstall,
        "download" | "auto_install" => UpdateAction::Download,
        _ => UpdateAction::Notify,
    }
}

pub fn update_target_key(platform: &str, arch: &str, kind: &str, edition: &str) -> String {
    format!("{platform}-{arch}-{kind}-{edition}")
}

pub fn update_signature_payload(
    manifest: &UpdateManifest,
    target_key: &str,
    target: &hbb_common::UpdateTarget,
) -> anyhow::Result<Vec<u8>> {
    for (name, value) in [
        ("product", manifest.product.as_str()),
        ("edition", manifest.edition.as_str()),
        ("channel", manifest.channel.as_str()),
        ("version", manifest.version.as_str()),
        ("source_commit", manifest.source_commit.as_str()),
        ("target_key", target_key),
        ("sha256", target.sha256.as_str()),
    ] {
        anyhow::ensure!(
            !value.contains(['\r', '\n']),
            "update signature field {name} contains a line break"
        );
    }
    Ok(format!(
        concat!(
            "rustdesk-update-v1\n",
            "product={}\n",
            "edition={}\n",
            "channel={}\n",
            "version={}\n",
            "build_seq={}\n",
            "source_commit={}\n",
            "target_key={}\n",
            "size={}\n",
            "sha256={}\n",
        ),
        manifest.product,
        manifest.edition,
        manifest.channel,
        manifest.version,
        manifest.build_seq,
        manifest.source_commit,
        target_key,
        target.size,
        target.sha256.to_ascii_lowercase(),
    )
    .into_bytes())
}

pub fn validate_target_metadata(
    target: &hbb_common::UpdateTarget,
    trusted_signature_key_id: &str,
) -> anyhow::Result<()> {
    anyhow::ensure!(
        target.primary.starts_with("https://"),
        "update primary URL must use HTTPS"
    );
    anyhow::ensure!(
        target.mirrors.iter().all(|url| url.starts_with("https://")),
        "update mirror URL must use HTTPS"
    );
    anyhow::ensure!(target.size > 0, "update size is missing");
    anyhow::ensure!(
        target.sha256.len() == 64 && target.sha256.bytes().all(|byte| byte.is_ascii_hexdigit()),
        "update sha256 is invalid"
    );
    anyhow::ensure!(!target.signature.is_empty(), "update signature is missing");
    anyhow::ensure!(
        target.signature_key_id == trusted_signature_key_id,
        "update signing key is not trusted"
    );
    Ok(())
}

pub fn try_update_sources<T, E>(
    target: &hbb_common::UpdateTarget,
    mut attempt: impl FnMut(&str) -> Result<T, E>,
) -> Result<(T, UpdateSource), Vec<UpdateSourceFailure<E>>> {
    let mut failures = Vec::new();
    for (url, source) in std::iter::once((&target.primary, UpdateSource::Primary))
        .chain(target.mirrors.iter().map(|url| (url, UpdateSource::Mirror)))
    {
        if url.is_empty() {
            continue;
        }
        match attempt(url) {
            Ok(value) => return Ok((value, source)),
            Err(error) => failures.push(UpdateSourceFailure {
                source,
                url: url.clone(),
                error,
            }),
        }
    }
    Err(failures)
}

pub fn replace_file_transaction(
    current: &Path,
    downloaded: &Path,
    validate: impl FnOnce(&Path) -> io::Result<()>,
) -> io::Result<()> {
    let backup = current.with_extension("old");
    match fs::remove_file(&backup) {
        Ok(()) => {}
        Err(err) if err.kind() == io::ErrorKind::NotFound => {}
        Err(err) => return Err(err),
    }
    fs::rename(current, &backup)?;
    if let Err(install_error) = fs::rename(downloaded, current) {
        return match fs::rename(&backup, current) {
            Ok(()) => Err(install_error),
            Err(rollback_error) => Err(io::Error::new(
                rollback_error.kind(),
                format!("install failed: {install_error}; rollback failed: {rollback_error}"),
            )),
        };
    }
    if let Err(validation_error) = validate(current) {
        let _ = fs::remove_file(current);
        return match fs::rename(&backup, current) {
            Ok(()) => Err(validation_error),
            Err(rollback_error) => Err(io::Error::new(
                rollback_error.kind(),
                format!("validation failed: {validation_error}; rollback failed: {rollback_error}"),
            )),
        };
    }
    fs::remove_file(backup)?;
    Ok(())
}

pub fn validate_manifest_contract(
    response: &VersionCheckResponse,
    manifest: &UpdateManifest,
    current_version: &str,
    current_build_seq: u64,
    product: &str,
    edition: &str,
    channel: &str,
    target_key: &str,
) -> anyhow::Result<()> {
    anyhow::ensure!(
        response.update_available,
        "response does not offer an update"
    );
    anyhow::ensure!(
        manifest.version == response.target_version,
        "target version mismatch"
    );
    anyhow::ensure!(
        manifest.build_seq == response.target_build_seq,
        "target build mismatch"
    );
    anyhow::ensure!(manifest.product == product, "product mismatch");
    anyhow::ensure!(
        manifest.edition == edition || manifest.edition == "multi",
        "edition mismatch"
    );
    anyhow::ensure!(manifest.channel == channel, "channel mismatch");
    anyhow::ensure!(
        is_newer_version(
            &manifest.version,
            manifest.build_seq,
            current_version,
            current_build_seq
        ),
        "manifest is not newer"
    );
    anyhow::ensure!(
        manifest.targets.contains_key(target_key),
        "target is missing"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use hbb_common::{UpdateTarget, VersionCheckResponse};
    use std::{collections::HashMap, fs, io, path::PathBuf};

    fn temp_test_dir(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "rustdesk-update-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock should be after Unix epoch")
                .as_nanos()
        ));
        fs::create_dir_all(&path).expect("test directory should be created");
        path
    }

    fn response(mode: &str, auto_install: bool) -> VersionCheckResponse {
        VersionCheckResponse {
            update_available: true,
            mode: mode.to_owned(),
            auto_install,
            target_version: "1.5.0".to_owned(),
            target_build_seq: 2026093005,
            ..Default::default()
        }
    }

    #[test]
    fn update_modes_have_distinct_actions() {
        assert_eq!(
            decide_update_action(&response("disabled", false), true),
            UpdateAction::Ignore
        );
        assert_eq!(
            decide_update_action(&response("notify", false), true),
            UpdateAction::Notify
        );
        assert_eq!(
            decide_update_action(&response("download", false), true),
            UpdateAction::Download
        );
        assert_eq!(
            decide_update_action(&response("auto_install", true), true),
            UpdateAction::AutoInstall
        );
        assert_eq!(
            decide_update_action(&response("auto_install", false), true),
            UpdateAction::Download
        );
        assert_eq!(
            decide_update_action(&response("auto_install", true), false),
            UpdateAction::Download
        );
    }

    #[test]
    fn pending_install_event_round_trips_through_cli_args() {
        let event = PendingUpdateEvent {
            transaction_id: "0123456789abcdef0123456789abcdef".to_owned(),
            from_version: "1.5.0".to_owned(),
            from_build_seq: 2026093004,
            version: "1.5.0".to_owned(),
            build_seq: 2026093005,
            source: UpdateSource::Mirror,
        };
        let mut args = vec!["--update".to_owned()];
        args.extend(event.cli_args());

        assert_eq!(PendingUpdateEvent::from_cli_args(&args), Some(event));
    }

    #[test]
    fn pending_install_event_rejects_incomplete_or_untrusted_values() {
        for args in [
            vec![
                "--update-event",
                "0123456789abcdef0123456789abcdef",
                "",
                "2026093004",
                "1.5.0",
                "2026093005",
                "primary",
            ],
            vec![
                "--update-event",
                "0123456789abcdef0123456789abcdef",
                "1.5.0",
                "invalid",
                "1.5.0",
                "2026093005",
                "primary",
            ],
            vec![
                "--update-event",
                "0123456789abcdef0123456789abcdef",
                "1.5.0",
                "2026093004",
                "1.5.0",
                "invalid",
                "primary",
            ],
            vec![
                "--update-event",
                "0123456789abcdef0123456789abcdef",
                "1.5.0",
                "2026093004",
                "1.5.0",
                "2026093005",
                "unknown",
            ],
            vec![
                "--update-event",
                "0123456789abcdef0123456789abcdef",
                "1.5.0",
                "2026093004",
                "1.5.0",
                "2026093005",
            ],
        ] {
            let args = args.into_iter().map(str::to_owned).collect::<Vec<_>>();
            assert_eq!(PendingUpdateEvent::from_cli_args(&args), None);
        }
    }

    #[test]
    fn mac_update_result_round_trips_and_rejects_invalid_state() {
        let result = MacUpdateResult {
            status: "rolled_back".to_owned(),
            event: PendingUpdateEvent {
                transaction_id: "fedcba9876543210fedcba9876543210".to_owned(),
                from_version: "1.5.0".to_owned(),
                from_build_seq: 2026093004,
                version: "1.5.0".to_owned(),
                build_seq: 2026093005,
                source: UpdateSource::Mirror,
            },
        };
        let encoded = result.encode().expect("result should encode");
        assert_eq!(MacUpdateResult::decode(&encoded), Some(result.clone()));
        assert!(MacUpdateResult::decode(&encoded.replace("rolled_back", "failed")).is_none());

        let injected = MacUpdateResult {
            status: "installed".to_owned(),
            event: PendingUpdateEvent {
                transaction_id: "fedcba9876543210fedcba9876543210".to_owned(),
                from_version: "1.5.0\nstatus=installed".to_owned(),
                from_build_seq: 1,
                version: "1.5.1".to_owned(),
                build_seq: 2,
                source: UpdateSource::Primary,
            },
        };
        assert!(injected.encode().is_err());

        let mut invalid_transaction = result;
        invalid_transaction.event.transaction_id = "bad/transaction".to_owned();
        assert!(invalid_transaction.encode().is_err());
    }

    #[test]
    fn preinstall_failure_never_claims_rollback() {
        assert_eq!(
            classify_update_preinstall_failure("Active session detected, deferring update"),
            "deferred"
        );
        assert_eq!(
            classify_update_preinstall_failure("failed to extract DMG"),
            "failed"
        );
    }

    #[test]
    fn signature_payload_binds_release_and_target_identity() {
        let manifest = UpdateManifest {
            version: "1.5.0".to_owned(),
            build_seq: 2026093005,
            product: "rustdesk-yan".to_owned(),
            edition: "multi".to_owned(),
            channel: "stable".to_owned(),
            source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
            ..Default::default()
        };
        let target = UpdateTarget {
            size: 42,
            sha256: "AB".repeat(32),
            ..Default::default()
        };

        let payload = update_signature_payload(&manifest, "windows-x86_64-exe-standard", &target)
            .expect("metadata should serialize");

        assert_eq!(
            String::from_utf8(payload).expect("payload should be UTF-8"),
            concat!(
                "rustdesk-update-v1\n",
                "product=rustdesk-yan\n",
                "edition=multi\n",
                "channel=stable\n",
                "version=1.5.0\n",
                "build_seq=2026093005\n",
                "source_commit=0123456789abcdef0123456789abcdef01234567\n",
                "target_key=windows-x86_64-exe-standard\n",
                "size=42\n",
                "sha256=abababababababababababababababababababababababababababababababab\n",
            )
        );

        let mut changed = manifest.clone();
        changed.edition = "sos".to_owned();
        assert_ne!(
            update_signature_payload(&changed, "windows-x86_64-exe-standard", &target)
                .expect("metadata should serialize"),
            update_signature_payload(&manifest, "windows-x86_64-exe-standard", &target)
                .expect("metadata should serialize")
        );
    }

    #[test]
    fn signature_payload_rejects_line_break_injection() {
        let manifest = UpdateManifest {
            product: "rustdesk-yan\nchannel=beta".to_owned(),
            ..Default::default()
        };
        assert!(update_signature_payload(&manifest, "target", &UpdateTarget::default()).is_err());
    }

    #[test]
    fn manifest_identity_and_target_must_match_response() {
        let key = update_target_key("windows", "x86_64", "exe", "standard");
        let mut targets = HashMap::new();
        targets.insert(key.clone(), UpdateTarget::default());
        let manifest = UpdateManifest {
            version: "1.5.0".to_owned(),
            build_seq: 2026093005,
            product: "rustdesk-yan".to_owned(),
            edition: "standard".to_owned(),
            channel: "stable".to_owned(),
            targets,
            ..Default::default()
        };
        let offered = response("download", false);
        assert!(validate_manifest_contract(
            &offered,
            &manifest,
            "1.5.0",
            2026093004,
            "rustdesk-yan",
            "standard",
            "stable",
            &key
        )
        .is_ok());
        assert!(validate_manifest_contract(
            &offered,
            &manifest,
            "1.5.0",
            2026093004,
            "rustdesk-yan",
            "sos",
            "stable",
            &key
        )
        .is_err());
        assert!(validate_manifest_contract(
            &offered,
            &manifest,
            "1.5.0",
            2026093005,
            "rustdesk-yan",
            "standard",
            "stable",
            &key
        )
        .is_err());
    }

    #[test]
    fn target_metadata_is_fail_closed() {
        let valid = UpdateTarget {
            primary: "https://download.yan.life/rustdesk/stable/v1/rustdesk.exe".to_owned(),
            mirrors: vec![
                "https://github.com/jandoucn/rustdesk/releases/download/v1/rustdesk.exe".to_owned(),
            ],
            size: 42,
            sha256: "a".repeat(64),
            signature: "c2lnbmF0dXJl".to_owned(),
            signature_key_id: "yan-release-2026".to_owned(),
        };
        assert!(validate_target_metadata(&valid, "yan-release-2026").is_ok());

        for invalid in [
            UpdateTarget {
                size: 0,
                ..valid.clone()
            },
            UpdateTarget {
                sha256: String::new(),
                ..valid.clone()
            },
            UpdateTarget {
                sha256: "z".repeat(64),
                ..valid.clone()
            },
            UpdateTarget {
                signature: String::new(),
                ..valid.clone()
            },
            UpdateTarget {
                signature_key_id: "other-key".to_owned(),
                ..valid.clone()
            },
            UpdateTarget {
                primary: "http://download.yan.life/rustdesk.exe".to_owned(),
                ..valid.clone()
            },
        ] {
            assert!(validate_target_metadata(&invalid, "yan-release-2026").is_err());
        }
    }

    #[test]
    fn update_source_falls_back_to_mirror() {
        let target = UpdateTarget {
            primary: "https://primary.invalid/update".to_owned(),
            mirrors: vec!["https://mirror.example/update".to_owned()],
            ..Default::default()
        };
        let mut attempted = Vec::new();
        let (value, source) = try_update_sources(&target, |url| {
            attempted.push(url.to_owned());
            if url.contains("primary") {
                Err("primary failed")
            } else {
                Ok("verified package")
            }
        })
        .expect("mirror should succeed");

        assert_eq!(value, "verified package");
        assert_eq!(source, UpdateSource::Mirror);
        assert_eq!(
            attempted,
            vec![target.primary.clone(), target.mirrors[0].clone()]
        );
    }

    #[test]
    fn update_source_reports_every_failure() {
        let target = UpdateTarget {
            primary: "https://primary.invalid/update".to_owned(),
            mirrors: vec!["https://mirror.example/update".to_owned()],
            ..Default::default()
        };
        let failures = try_update_sources(&target, |url| Err::<(), _>(format!("{url} failed")))
            .expect_err("all sources should fail");

        assert_eq!(failures.len(), 2);
        assert_eq!(failures[0].source, UpdateSource::Primary);
        assert_eq!(failures[1].source, UpdateSource::Mirror);
        assert!(failures[1].error.contains("mirror.example"));
    }

    #[test]
    fn failed_replacement_restores_the_previous_binary() {
        let dir = temp_test_dir("rollback");
        let current = dir.join("rustdesk.AppImage");
        let downloaded = dir.join("downloaded.AppImage");
        fs::write(&current, b"old working version").expect("old version should be written");
        fs::write(&downloaded, b"new broken version").expect("new version should be written");

        let result = replace_file_transaction(&current, &downloaded, |_| {
            Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "smoke check failed",
            ))
        });

        assert!(result.is_err());
        assert_eq!(
            fs::read(&current).expect("old version should remain"),
            b"old working version"
        );
        assert!(!downloaded.exists());
        assert!(!current.with_extension("old").exists());
        fs::remove_dir_all(dir).expect("test directory should be removed");
    }
}
