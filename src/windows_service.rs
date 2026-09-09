#![cfg(windows)]

use std::{
    ffi::OsString,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use clap::Subcommand;
use windows_service::{
    define_windows_service,
    service::{
        ServiceAccess, ServiceAction, ServiceActionType, ServiceControl, ServiceControlAccept,
        ServiceErrorControl, ServiceExitCode, ServiceFailureActions, ServiceFailureResetPeriod,
        ServiceInfo, ServiceStartType, ServiceState, ServiceStatus, ServiceType,
    },
    service_control_handler::{self, ServiceControlHandlerResult},
    service_dispatcher,
    service_manager::{ServiceManager, ServiceManagerAccess},
};

use crate::runtime::{run_server, RuntimeConfig};

pub const SERVICE_NAME: &str = "ConversationBlackboard";
pub const SERVICE_DISPLAY_NAME: &str = "Conversation Blackboard";
const SERVICE_DESCRIPTION: &str =
    "Persistent blackboard for communication across independent conversations.";
const STATE_TIMEOUT: Duration = Duration::from_secs(20);

static SERVICE_CONFIG: OnceLock<RuntimeConfig> = OnceLock::new();

define_windows_service!(ffi_service_main, service_main);

#[derive(Debug, Subcommand)]
pub enum ServiceCommand {
    /// Install the Windows service. Run from an elevated terminal.
    Install {
        #[arg(long)]
        db: PathBuf,
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        #[arg(long, default_value_t = 8766)]
        port: u16,
    },
    /// Remove the Windows service. Run from an elevated terminal.
    Uninstall,
    /// Start the installed service.
    Start,
    /// Stop the installed service.
    Stop,
    /// Restart the installed service.
    Restart,
    /// Print the current SCM state.
    Status,
    /// Internal SCM entry point. Do not invoke directly.
    #[command(hide = true)]
    Run {
        #[arg(long)]
        db: PathBuf,
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        #[arg(long, default_value_t = 8766)]
        port: u16,
    },
}

pub fn dispatch(command: ServiceCommand) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match command {
        ServiceCommand::Install { db, host, port } => install(db, host, port),
        ServiceCommand::Uninstall => uninstall(),
        ServiceCommand::Start => start(),
        ServiceCommand::Stop => stop(),
        ServiceCommand::Restart => restart(),
        ServiceCommand::Status => status(),
        ServiceCommand::Run { db, host, port } => run_dispatcher(RuntimeConfig::from_env_with_overrides(
            Some(host),
            Some(port),
            Some(db),
        )),
    }
}

fn install(
    db: PathBuf,
    host: String,
    port: u16,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let executable_path = std::env::current_exe()?;
    let manager = ServiceManager::local_computer(
        None::<&str>,
        ServiceManagerAccess::CONNECT | ServiceManagerAccess::CREATE_SERVICE,
    )?;

    let launch_arguments = vec![
        OsString::from("service"),
        OsString::from("run"),
        OsString::from("--db"),
        db.into_os_string(),
        OsString::from("--host"),
        OsString::from(host),
        OsString::from("--port"),
        OsString::from(port.to_string()),
    ];

    let info = ServiceInfo {
        name: OsString::from(SERVICE_NAME),
        display_name: OsString::from(SERVICE_DISPLAY_NAME),
        service_type: ServiceType::OWN_PROCESS,
        start_type: ServiceStartType::AutoStart,
        error_control: ServiceErrorControl::Normal,
        executable_path,
        launch_arguments,
        dependencies: vec![],
        account_name: None,
        account_password: None,
    };

    let access = ServiceAccess::QUERY_STATUS
        | ServiceAccess::START
        | ServiceAccess::STOP
        | ServiceAccess::DELETE
        | ServiceAccess::CHANGE_CONFIG;
    let service = manager.create_service(&info, access)?;
    service.set_description(SERVICE_DESCRIPTION)?;

    service.update_failure_actions(ServiceFailureActions {
        reset_period: ServiceFailureResetPeriod::After(Duration::from_secs(24 * 60 * 60)),
        reboot_msg: None,
        command: None,
        actions: Some(vec![
            ServiceAction {
                action_type: ServiceActionType::Restart,
                delay: Duration::from_secs(5),
            },
            ServiceAction {
                action_type: ServiceActionType::Restart,
                delay: Duration::from_secs(15),
            },
            ServiceAction {
                action_type: ServiceActionType::Restart,
                delay: Duration::from_secs(30),
            },
        ]),
    })?;
    service.set_failure_actions_on_non_crash_failures(true)?;

    println!("Installed {SERVICE_NAME} (automatic start).");
    println!("Database: {}", info.launch_arguments[3].to_string_lossy());
    println!("Origin: http://{}:{}", info.launch_arguments[5].to_string_lossy(), port);
    println!("Registration remains disabled unless BLACKBOARD_REGISTRATION_KEY is available to the service process.");
    Ok(())
}

fn uninstall() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)?;
    let service = manager.open_service(
        SERVICE_NAME,
        ServiceAccess::QUERY_STATUS | ServiceAccess::STOP | ServiceAccess::DELETE,
    )?;

    if service.query_status()?.current_state != ServiceState::Stopped {
        let _ = service.stop()?;
        wait_for_state(&service, ServiceState::Stopped, STATE_TIMEOUT)?;
    }
    service.delete()?;
    println!("Uninstalled {SERVICE_NAME}.");
    Ok(())
}

fn start() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)?;
    let service = manager.open_service(
        SERVICE_NAME,
        ServiceAccess::QUERY_STATUS | ServiceAccess::START,
    )?;
    let state = service.query_status()?.current_state;
    if state == ServiceState::Running {
        println!("{SERVICE_NAME} is already running.");
        return Ok(());
    }
    service.start(&[] as &[OsString])?;
    wait_for_state(&service, ServiceState::Running, STATE_TIMEOUT)?;
    println!("Started {SERVICE_NAME}.");
    Ok(())
}

fn stop() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)?;
    let service = manager.open_service(
        SERVICE_NAME,
        ServiceAccess::QUERY_STATUS | ServiceAccess::STOP,
    )?;
    if service.query_status()?.current_state == ServiceState::Stopped {
        println!("{SERVICE_NAME} is already stopped.");
        return Ok(());
    }
    let _ = service.stop()?;
    wait_for_state(&service, ServiceState::Stopped, STATE_TIMEOUT)?;
    println!("Stopped {SERVICE_NAME}.");
    Ok(())
}

fn restart() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)?;
    let service = manager.open_service(
        SERVICE_NAME,
        ServiceAccess::QUERY_STATUS | ServiceAccess::START | ServiceAccess::STOP,
    )?;
    if service.query_status()?.current_state != ServiceState::Stopped {
        let _ = service.stop()?;
        wait_for_state(&service, ServiceState::Stopped, STATE_TIMEOUT)?;
    }
    service.start(&[] as &[OsString])?;
    wait_for_state(&service, ServiceState::Running, STATE_TIMEOUT)?;
    println!("Restarted {SERVICE_NAME}.");
    Ok(())
}

fn status() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)?;
    let service = manager.open_service(SERVICE_NAME, ServiceAccess::QUERY_STATUS)?;
    let status = service.query_status()?;
    println!("{SERVICE_NAME}: {:?}", status.current_state);
    if let Some(pid) = status.process_id {
        println!("PID: {pid}");
    }
    Ok(())
}

fn wait_for_state(
    service: &windows_service::service::Service,
    target: ServiceState,
    timeout: Duration,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let start = Instant::now();
    loop {
        let current = service.query_status()?.current_state;
        if current == target {
            return Ok(());
        }
        if start.elapsed() >= timeout {
            return Err(format!("timed out waiting for {SERVICE_NAME} to become {target:?}; current state is {current:?}").into());
        }
        thread::sleep(Duration::from_millis(250));
    }
}

fn run_dispatcher(
    config: RuntimeConfig,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    SERVICE_CONFIG
        .set(config)
        .map_err(|_| "service runtime configuration already initialized")?;
    service_dispatcher::start(SERVICE_NAME, ffi_service_main)?;
    Ok(())
}

fn service_main(_arguments: Vec<OsString>) {
    if let Err(error) = run_service() {
        if let Some(config) = SERVICE_CONFIG.get() {
            append_service_log(&config.db_path, &format!("service error: {error}"));
        }
    }
}

fn run_service() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = SERVICE_CONFIG
        .get()
        .cloned()
        .ok_or("service runtime configuration missing")?;
    append_service_log(&config.db_path, "service starting");

    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let stop_tx = Arc::new(Mutex::new(Some(stop_tx)));
    let control_tx = Arc::clone(&stop_tx);

    let event_handler = move |control_event| -> ServiceControlHandlerResult {
        match control_event {
            ServiceControl::Stop | ServiceControl::Shutdown => {
                if let Ok(mut sender) = control_tx.lock() {
                    if let Some(sender) = sender.take() {
                        let _ = sender.send(());
                    }
                }
                ServiceControlHandlerResult::NoError
            }
            ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
            _ => ServiceControlHandlerResult::NotImplemented,
        }
    };

    let status_handle = service_control_handler::register(SERVICE_NAME, event_handler)?;
    status_handle.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: ServiceState::Running,
        controls_accepted: ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN,
        exit_code: ServiceExitCode::NO_ERROR,
        checkpoint: 0,
        wait_hint: Duration::default(),
        process_id: None,
    })?;
    append_service_log(&config.db_path, "service running");

    let db_path = config.db_path.clone();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(run_server(config, async move {
        let _ = stop_rx.await;
    }));

    let exit_code = if result.is_ok() {
        append_service_log(&db_path, "service stopped cleanly");
        ServiceExitCode::NO_ERROR
    } else {
        append_service_log(&db_path, "service runtime exited with error");
        ServiceExitCode::ServiceSpecific(1)
    };
    status_handle.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: ServiceState::Stopped,
        controls_accepted: ServiceControlAccept::empty(),
        exit_code,
        checkpoint: 0,
        wait_hint: Duration::default(),
        process_id: None,
    })?;

    result
}

fn append_service_log(db_path: &Path, message: &str) {
    let log_path = db_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("conversation-blackboard.log");
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0);
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_path) {
        let _ = writeln!(file, "{timestamp} {message}");
    }
}
