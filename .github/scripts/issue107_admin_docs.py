from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
anchor = """Snapshot reads do not migrate, repair, normalize, reactivate, deactivate, or consume authority. Inactive durable grants plus expired or consumed delegated grants remain visible as inventory history, and credential material is never part of the snapshot contract.
"""
replacement = anchor + """
Authorization administration is a separate local operator boundary. Durable/delegated create, durable reactivation, and durable/delegated deactivation pass through one canonical administration service and commit non-secret administration provenance atomically with effective policy changes. `grant history` reads that provenance observationally. Administration is not currently exposed as a REST or MCP mutation surface.
"""
readme = replace_once(readme, anchor, replacement, "README administration boundary")
readme_path.write_text(readme, encoding="utf-8")


auth_path = Path("docs/authorization-policy.md")
auth = auth_path.read_text(encoding="utf-8")
anchor = """Decision/explain visibility is a third independent observational surface. Only Human Web admin self receives implicit global explain authority; participant-HMAC self, GitHub-owner compatibility authority, and OIDC/Bearer identity require an explicit `read_authorization_decision` grant. Explain authority does not imply policy snapshot, policy integrity, execution audit/sweep, or channel administration, and those capabilities do not imply explain visibility.

## Verification boundary
"""
replacement = """Decision/explain visibility is a third independent observational surface. Only Human Web admin self receives implicit global explain authority; participant-HMAC self, GitHub-owner compatibility authority, and OIDC/Bearer identity require an explicit `read_authorization_decision` grant. Explain authority does not imply policy snapshot, policy integrity, execution audit/sweep, or channel administration, and those capabilities do not imply explain visibility.

## Authorization administration

Authorization mutation is owned by `authorization_admin`, not by CLI, REST, MCP, or the read-side policy evaluator. The supported mutation path is:

```text
local operator CLI
        ↓
authorization_admin canonical service
        ↓
validated durable/delegated lifecycle mutation
        +
append-only authorization_admin_events provenance
        ↓
one SQLite transaction
```

The canonical service preserves existing lifecycle behavior: an exact active durable scope is idempotent, an exact inactive durable scope reactivates the same row, durable deactivation closes every active row in the same exact legacy scope, delegated create remains append-only, and delegated deactivation is idempotent. Execution-time one-shot consumption is not an administration mutation and remains owned by the semantic execution transaction.

Only an **effective policy state change** emits an administration event. A durable create that resolves to an already-active exact grant and a repeated deactivation that changes zero rows do not create misleading mutation records.

Administration provenance contains only non-secret actor and policy metadata: administration surface, optional authenticated `Principal`, optional actor participant attribution, target principal, participant, capability/resource/intent scope, lifecycle transition, and commit time. Local CLI administration is represented explicitly as `local-cli` with no fabricated remote principal. Authentication credentials are never written to administration evidence.

Schema ownership is explicit. `db init` owns administration-schema creation/migration. Mutation services require a current writable schema and fail with migration-required semantics rather than repairing storage themselves. Read-side commands likewise remain observational: `grant list`, `grant durable list`, `grant explain`, `grant verify`, and `grant history` open/read current state without calling grant-schema migration.

`authorization_admin::read_administration_events(conn, ...)` is the canonical local provenance reader. `conversation-blackboard grant history` is its operator projection. REST and MCP have no grant mutation endpoint/tool; source regressions protect that boundary so remote adapters cannot silently become a second administration implementation.

## Verification boundary
"""
auth = replace_once(auth, anchor, replacement, "authorization policy administration section")
auth_path.write_text(auth, encoding="utf-8")


ops_path = Path("docs/operations.md")
ops = ops_path.read_text(encoding="utf-8")
anchor = """Delegated one-shot consumption remains part of the same semantic execution transaction as the committed effect and receipt. Failed semantic execution does not burn one-shot authority.

### Authorization policy integrity
"""
replacement = """Delegated one-shot consumption remains part of the same semantic execution transaction as the committed effect and receipt. Failed semantic execution does not burn one-shot authority.

### Authorization administration history

Grant lifecycle mutations are audited locally. Effective durable/delegated create, durable reactivation, and durable/delegated deactivation commit an append-only non-secret administration event in the same SQLite transaction as the policy change.

Inspect that evidence with:

```powershell
.\\conversation-blackboard.exe grant history --db <DB>
.\\conversation-blackboard.exe grant history --db <DB> --participant-id <ID>
```

`grant history` is read-only. Local CLI mutations are recorded with the explicit `local-cli` administration surface and no invented remote identity. When a future trusted administration surface supplies an authenticated principal, only normalized principal/scope metadata may be recorded; bearer tokens, JWTs, HMAC secrets, TOTP material, session credentials, and passwords are never administration evidence.

Grant administration schema changes are explicit database operations. If `grant history`, `grant list`, or `grant durable list` reports that the authorization schema requires migration, run:

```powershell
.\\conversation-blackboard.exe db init --db <DB>
```

and then rerun the read command. Read commands do not perform that migration themselves.

There is currently no REST or MCP grant-mutation operation. Remote policy snapshot, integrity, and decision/explain surfaces remain privileged read projections only.

### Authorization policy integrity
"""
ops = replace_once(ops, anchor, replacement, "operations administration history")
ops_path.write_text(ops, encoding="utf-8")
