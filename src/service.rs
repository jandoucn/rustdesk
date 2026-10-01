use librustdesk::*;

#[cfg(not(target_os = "macos"))]
fn main() {}

#[cfg(target_os = "macos")]
fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() > 1 && args[1] == "--write-plists" {
        let bundle_app_name = args
            .get(2)
            .cloned()
            .unwrap_or_else(librustdesk::common::get_app_name);
        if let Err(e) = librustdesk::platform::write_plists_for_bundle(&bundle_app_name) {
            eprintln!("Failed to write plists: {}", e);
            std::process::exit(1);
        }
        std::process::exit(0);
    }
    crate::common::load_custom_client();
    hbb_common::init_log(false, "service");
    crate::start_os_service();
}
