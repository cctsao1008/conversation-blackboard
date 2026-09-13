from pathlib import Path

# Patch execution.rs
p = Path('src/execution.rs')
s = p.read_text(encoding='utf-8')

s = s.replace(
    '#[cfg(test)]\n#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]\npub struct AuthorizationProvenance {',
    '#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]\npub struct AuthorizationProvenance {',
    1,
)

needle = '''pub struct AuthorizationProvenance {\n    pub participant_id: String,\n    pub intent_id: String,\n    pub source: String,\n    pub reason: String,\n    pub grant_id: Option<i64>,\n}\n'''
insert = needle + '''\n#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]\npub struct IngressAuditRecord {\n    pub delivery_id: String,\n    pub intent_id: String,\n    pub transport: String,\n    pub external_ref: String,\n    pub principal: Principal,\n}\n\n#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]\npub struct ExecutionAuditBundle {\n    pub receipt: ExecutionReceipt,\n    pub authorization: Option<AuthorizationProvenance>,\n    pub ingress: Vec<IngressAuditRecord>,\n}\n'''
if needle not in s:
    raise SystemExit('AuthorizationProvenance block not found')
s = s.replace(needle, insert, 1)

s = s.replace('#[cfg(test)]\npub fn get_authorization_provenance(', 'pub fn get_authorization_provenance(', 1)

needle = '''pub fn execute_message_intent(\n'''
helper = '''pub fn get_execution_audit_bundle(\n    conn: &Connection,\n    participant_id: &str,\n    intent_id: &str,\n) -> rusqlite::Result<Option<ExecutionAuditBundle>> {\n    let Some(receipt) = get_execution_receipt(conn, participant_id, intent_id)? else {\n        return Ok(None);\n    };\n    let authorization = get_authorization_provenance(conn, participant_id, intent_id)?;\n    let mut stmt = conn.prepare(\n        "SELECT delivery_id, intent_id, transport, external_ref, principal_provider, principal_subject\n         FROM ingress_provenance\n         WHERE intent_id = ?1\n         ORDER BY created_at, delivery_id",\n    )?;\n    let ingress = stmt\n        .query_map([intent_id], |row| {\n            Ok(IngressAuditRecord {\n                delivery_id: row.get(0)?,\n                intent_id: row.get(1)?,\n                transport: row.get(2)?,\n                external_ref: row.get(3)?,\n                principal: Principal {\n                    provider: row.get(4)?,\n                    subject: row.get(5)?,\n                },\n            })\n        })?\n        .collect::<rusqlite::Result<Vec<_>>>()?;\n    Ok(Some(ExecutionAuditBundle {\n        receipt,\n        authorization,\n        ingress,\n    }))\n}\n\npub fn execute_message_intent(\n'''
if needle not in s:
    raise SystemExit('execute marker not found')
s = s.replace(needle, helper, 1)

needle = '''        assert_eq!(provenance_count, 1);\n    }\n'''
insert = '''        assert_eq!(provenance_count, 1);\n        let audit = get_execution_audit_bundle(&conn, "maker-main", "semantic-intent-1")\n            .unwrap()\n            .unwrap();\n        assert_eq!(audit.receipt.message_id, first_id);\n        assert_eq!(audit.ingress.len(), 2);\n        assert_eq!(\n            audit.authorization.as_ref().map(|v| v.reason.as_str()),\n            Some("implicit_participant_hmac")\n        );\n    }\n'''
if needle not in s:
    raise SystemExit('shared execution test tail not found')
s = s.replace(needle, insert, 1)

p.write_text(s, encoding='utf-8')

# Add execution_audit.rs
Path('src/execution_audit.rs').write_text(r'''use std::{error::Error, path::{Path, PathBuf}};

use clap::Subcommand;

use crate::{db, execution, identity};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum ExecutionCommand {
    /// Inspect immutable committed execution evidence without re-evaluating policy.
    Audit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
}

pub fn dispatch(command: ExecutionCommand) -> DynResult {
    match command {
        ExecutionCommand::Audit { db: path, participant_id, intent_id } => {
            require_database(&path)?;
            let participant_id = identity::validate_participant_id(&participant_id)
                .ok_or("invalid participant_id")?;
            let intent_id = execution::normalize_intent_id(&intent_id)
                .map_err(|_| "invalid intent_id")?;
            let conn = db::connect(&path)?;
            let Some(audit) = execution::get_execution_audit_bundle(
                &conn,
                &participant_id,
                &intent_id,
            )? else {
                return Err(format!("execution not found: {participant_id}/{intent_id}").into());
            };

            println!("EXECUTION AUDIT");
            println!("participant_id : {}", audit.receipt.participant_id);
            println!("intent_id      : {}", audit.receipt.intent_id);
            println!("capability     : {}", audit.receipt.capability);
            println!("message_id     : {}", audit.receipt.message_id);
            println!("status         : {}", audit.receipt.status);
            println!("intent_hash    : {}", audit.receipt.intent_hash);
            if let Some(auth) = audit.authorization {
                println!("auth_source    : {}", auth.source);
                println!("auth_reason    : {}", auth.reason);
                println!("auth_grant_id  : {}", auth.grant_id.map(|v| v.to_string()).unwrap_or_else(|| "-".to_owned()));
            } else {
                println!("auth_source    : -");
                println!("auth_reason    : -");
                println!("auth_grant_id  : -");
            }
            println!("deliveries     : {}", audit.ingress.len());
            for delivery in audit.ingress {
                println!(
                    "delivery        : {}\t{}\t{}\t{}:{}\t{}",
                    delivery.delivery_id,
                    delivery.transport,
                    delivery.external_ref,
                    delivery.principal.provider,
                    delivery.principal.subject,
                    delivery.intent_id,
                );
            }
            Ok(())
        }
    }
}

fn require_database(path: &Path) -> DynResult {
    if !path.is_file() {
        return Err(format!("database does not exist: {}", path.display()).into());
    }
    Ok(())
}
''', encoding='utf-8')

# Patch main.rs
p = Path('src/main.rs')
s = p.read_text(encoding='utf-8')
s = s.replace('mod execution;\n', 'mod execution;\nmod execution_audit;\n', 1)
needle = '''    /// Create, inspect, or revoke delegated authorization grants.\n    Grant {\n        #[command(subcommand)]\n        command: grant_admin::GrantCommand,\n    },\n'''
insert = needle + '''    /// Inspect immutable committed semantic execution evidence.\n    Execution {\n        #[command(subcommand)]\n        command: execution_audit::ExecutionCommand,\n    },\n'''
if needle not in s:
    raise SystemExit('Grant command block not found')
s = s.replace(needle, insert, 1)
s = s.replace(
    '        Some(Command::Grant { command }) => grant_admin::dispatch(command),\n',
    '        Some(Command::Grant { command }) => grant_admin::dispatch(command),\n        Some(Command::Execution { command }) => execution_audit::dispatch(command),\n',
    1,
)
needle = '''    #[test]\n    fn delegated_grant_list_and_deactivate_cli_parse() {\n'''
test = '''    #[test]\n    fn execution_audit_cli_parses() {\n        let cli = Cli::try_parse_from([\n            "conversation-blackboard",\n            "execution",\n            "audit",\n            "--db",\n            "board.db",\n            "--participant-id",\n            "maker-main",\n            "--intent-id",\n            "intent-83",\n        ])\n        .unwrap();\n        assert!(matches!(\n            cli.command,\n            Some(Command::Execution {\n                command: execution_audit::ExecutionCommand::Audit { .. }\n            })\n        ));\n    }\n\n'''
if needle not in s:
    raise SystemExit('test insertion marker not found')
s = s.replace(needle, test + needle, 1)
p.write_text(s, encoding='utf-8')
