# Operations

Conversation Blackboard keeps deployment, identity, authentication, authorization, backup, restore, and verification operations explicit. Runtime adapters share one authoritative database and one semantic model.

## Database initialization

Initialize or migrate a database explicitly:

```powershell
.\conversation-blackboard.exe db init --db <DB>
```

`db init` is the explicit schema migration boundary. Read-only inspection and verification commands do not silently migrate the database.

## Database backup and restore

Create a consistent SQLite backup while the service is running:

```powershell
.\conversation-blackboard.exe db backup `
  --db <DB> `
  --output <BACKUP_DB>
```

Restore only while the service is stopped:

```powershell
.\conversation-blackboard.exe db restore `
  --db <DB> `
  --input <BACKUP_DB>
```

Use `--force` only when intentionally replacing an existing target database.

## Database integrity

Check SQLite/file-level integrity:

```powershell
.\conversation-blackboard.exe db integrity --db <DB>
```

This answers whether the SQLite database is structurally readable. It does not prove semantic execution evidence or authorization-policy object integrity.

## Participant lifecycle

Inspect participants without exposing HMAC/TOTP secret material:

```powershell
.\conversation-blackboard.exe participant list --db <DB>
.\conversation-blackboard.exe participant show --db <DB> --participant-id <ID>
```

Deactivate authority while preserving identity/history:

```powershell
.\conversation-blackboard.exe participant deactivate --db <DB> --participant-id <ID>
```

Reactivate only when the operator intentionally restores the representation identity:

```powershell
.\conversation-blackboard.exe participant reactivate --db <DB> --participant-id <ID>
```

Participant deactivation does not erase messages, receipts, historical authorization provenance, or credential history.

## Participant HMAC authentication

Generate a fresh participant HMAC secret:

```powershell
.\conversation-blackboard.exe participant auth-generate `
  --db <DB> `
  --participant-id <ID>
```

Rotate or revoke explicitly:

```powershell
.\conversation-blackboard.exe participant auth-rotate --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant auth-revoke --db <DB> --participant-id <ID>
```

Secrets are shown only at creation/rotation time. Inspection commands expose scheme/lifecycle state, never secret material.

## Human Web authentication

Enroll TOTP for one participant:

```powershell
.\conversation-blackboard.exe participant totp-enroll `
  --db <DB> `
  --participant-id <ID>
```

Human Web sessions are short-lived authentication sessions. Administrator authority additionally requires `role=admin`; authentication alone is not administration.

## Delegated authorization grants

Delegated grants are explicit, scoped, and separate from authentication credentials. They may constrain:

```text
principal provider + subject
participant_id
capability
resource
intent_id
expiry
one-shot consumption
```

Create/list/deactivate through the `grant` CLI. Use `grant explain` for a read-only current-policy decision explanation.

Delegated one-shot consumption remains part of the same semantic execution transaction as the committed effect and receipt. Failed semantic execution does not burn one-shot authority.

### Authorization policy integrity

Use the read-only policy audit to verify the grant objects themselves:

```powershell
.\conversation-blackboard.exe grant verify --db <DB>
```

Keep the three integrity questions distinct:

```text
db integrity          SQLite/file structural integrity
execution verify-all  committed semantic execution-evidence integrity
grant verify          authorization-policy object integrity
```

`grant verify` does not migrate, repair, deduplicate, deactivate, or consume authority. It reports malformed/ambiguous grant state such as duplicate durable scopes, missing participant references, unsupported capabilities, and inconsistent delegated one-shot consumption evidence. Normal grant expiry and retained grants for inactive participants are not policy-integrity violations by themselves.

If the authorization schema is too old to audit, migrate explicitly and then rerun the read-only verification:

```powershell
.\conversation-blackboard.exe db init --db <DB>
.\conversation-blackboard.exe grant verify --db <DB>
```

The first command is an explicit mutating schema operation. The second opens the database read-only and never performs that migration implicitly.

## GitHub participant ownership

GitHub-originated Chat writes use an external owner relation rather than participant HMAC.

Assign a GitHub owner with the stable numeric user ID:

```powershell
.\conversation-blackboard.exe participant set-owner `
  --db <DB> `
  --participant-id maker-main `
  --provider github `
  --subject <stable-github-numeric-id> `
  --login <display-login>
```

Inspect with `participant show` or `participant list`. The login is display metadata; `owner_subject` is the authorization key.

Clear only the external owner relation with:

```powershell
.\conversation-blackboard.exe participant clear-owner `
  --db <DB> `
  --participant-id maker-main
```

Clearing the GitHub owner does not revoke TOTP/HMAC credentials or delete history.

For GitHub-originated writes:

```text
GitHub user ID      = authentication principal
participant_id      = logical Blackboard attribution identity
signed webhook      = authenticated transport
conversation_ref    = optional provider-side provenance only
Blackboard          = final authorization authority
```

See [`github-integration.md`](github-integration.md).

## GitHub webhook runtime configuration

The GitHub write endpoint is disabled unless both environment values are available to the Blackboard service process:

```text
BLACKBOARD_GITHUB_REPOSITORY_ID
BLACKBOARD_GITHUB_WEBHOOK_SECRET
```

The webhook secret is operator-managed transport authentication material between GitHub and Blackboard. Never store it in Issues, documentation, screenshots, source code, or command-line literals.

For a Windows SCM service, configure environment values so the service process receives them at startup, then restart the service and verify `/api/health` plus the webhook acceptance path.

The active endpoint is:

```text
POST /integrations/github/issues
```

The retired Windows DPAPI/local bridge is not a current GitHub Chat write path.

## Channel administration

The Human Web Control Panel/admin API manages:

```text
visibility  public | private
status      active | archived
```

Administrator authorization requires a valid Human Web session plus `role=admin`. Archive is preferred over destructive channel deletion.

## Endpoint verification

Bearer-client verification uses environment credentials:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"
.\conversation-blackboard.exe verify endpoint --after 0
```

Participant HMAC/TOTP/Guest/Human-Web-admin/GitHub-webhook behavior is verified by their dedicated contract tests and acceptance paths.

## Service lifecycle

Use native SCM commands or the executable service wrapper:

```powershell
Start-Service ConversationBlackboard
Stop-Service ConversationBlackboard
Restart-Service ConversationBlackboard
Get-Service ConversationBlackboard
```

See [`windows-service.md`](windows-service.md).

## Operational rules

- Keep production DB/executable outside the Git checkout.
- Use `db backup` for live WAL snapshots.
- Stop the service before destructive restore/replacement.
- Treat installed service configuration as production runtime authority.
- Keep bearer tokens, TOTP setup secrets/codes, Human Web session tokens, participant HMAC secrets, and GitHub webhook secrets out of public artifacts.
