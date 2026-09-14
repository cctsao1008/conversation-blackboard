# Execution Audit Read-Only Migration Boundary

## Purpose

Conversation Blackboard separates **database evolution** from **historical evidence observation**.

The execution receipt, audit, and integrity surfaces inspect committed evidence. They must not silently repair that evidence, upgrade its schema, reconstruct missing historical context, or change SQLite operating state merely because an operator or remote caller asked to read it.

Core rule:

> **Read and verification paths may observe historical evidence, but must not migrate, repair, backfill, or rewrite it.**

This boundary applies to the canonical Rust read helpers and therefore to the REST, MCP, and CLI projections that consume them.

---

## Lifecycle boundary

The supported lifecycle is:

```text
fresh database / legacy database
        |
        | explicit initialization boundary
        v
db::initialize(...)
        |
        +--> canonical schema
        +--> ordered migrations
        +--> conservative deterministic backfills
        |
        v
current database
        |
        +--> normal service / MCP runtime
        +--> semantic execution writes
        +--> receipt / audit / integrity reads
```

It is intentionally **not**:

```text
audit read
   |
   +--> CREATE / ALTER
   +--> historical backfill
   +--> journal-mode change
   v
return result
```

Normal HTTP server startup and local MCP stdio startup already initialize the database before serving requests. Explicit operator migration is available through:

```text
conversation-blackboard db init --db <path>
```

---

## Migration authority

`db::initialize()` owns execution schema migration.

The execution migration is exposed as the explicit internal operation:

```rust
execution::migrate_execution_schema(&conn)
```

It may perform schema evolution and only the historical recovery that can be proven from already committed evidence.

The migration currently owns:

- execution receipt and ingress-provenance table/index creation where needed;
- legacy `ingress_provenance.participant_id` addition;
- participant-scoped ingress index creation;
- historical authorization-provenance column additions;
- deterministic authorization capability recovery from the matching execution receipt;
- conservative ingress participant recovery when one intent maps to exactly one distinct receipt participant.

Migration is an initialization concern, not a read concern.

---

## Fresh schema versus legacy migration ordering

`schema.sql` describes the current fresh-install table shape, but schema statements must remain safe when executed against legacy databases whose existing tables may predate newer columns.

A critical example is ingress provenance.

Fresh databases include:

```text
ingress_provenance.participant_id
```

but a pre-#87 database can already contain `ingress_provenance` without that column. Because `CREATE TABLE IF NOT EXISTS` does not replace an existing table, a base-schema statement such as:

```sql
CREATE INDEX ... ON ingress_provenance(participant_id, intent_id)
```

would fail before migration had a chance to add `participant_id`.

Therefore the participant-scoped composite index is **migration-owned**. The ordered upgrade is:

```text
inspect legacy ingress schema
        |
        +--> participant_id missing?
        |       yes -> ALTER TABLE ... ADD COLUMN participant_id
        |
        +--> CREATE INDEX (participant_id, intent_id)
        |
        +--> conservative participant backfill
```

Fresh initialization still ends at the same current schema because `db::initialize()` always runs the execution migration after applying the base schema.

---

## Canonical reads are observational

The following helpers no longer own schema migration:

```text
get_execution_receipt(...)
get_authorization_provenance(...)
get_execution_audit_bundle(...)
verify_execution_audit_integrity(...)
```

They read the database state they were given.

They do not:

- create execution tables;
- add columns;
- create migration indexes;
- backfill authorization capability;
- bind legacy ingress rows to participants;
- reconstruct principal/resource metadata from current policy;
- rewrite incomplete historical evidence.

This preserves the distinction:

```text
Current policy                 Historical evidence
-------------                  -------------------
who may read/act now           why a past execution was allowed then
```

The integrity verifier reports incomplete or inconsistent evidence; it does not repair it.

---

## CLI audit is physically read-only

The execution audit commands use a dedicated SQLite read-only connection:

```text
conversation-blackboard execution audit ...
conversation-blackboard execution verify-audit ...
```

The connection is opened with SQLite read-only flags and `PRAGMA query_only = ON`.

This is intentionally separate from the normal runtime connection helper, which configures writable runtime behavior such as WAL mode.

Consequently, auditing an outdated database cannot silently change its journal mode before reporting that migration is required.

The CLI first checks whether the execution schema is current. If it is not, the operator receives an explicit migration instruction rather than an implicit upgrade:

```text
execution schema requires migration:
run `conversation-blackboard db init --db <path>` before audit
```

A process-level regression verifies that a legacy-shaped database in `DELETE` journal mode remains byte-for-byte unchanged after a rejected audit request and retains the same journal mode. The same fixture is then explicitly upgraded with `db init`, after which the audit command progresses to normal semantic lookup.

---

## Historical backfill rules

The explicit migration preserves the conservative rules established by the execution-audit lineage.

### Authorization capability

Capability may be recovered when it is directly determined by the committed execution receipt for the same `(participant_id, intent_id)`.

```text
execution receipt capability
        |
        +--> deterministic historical recovery
```

### Ingress participant binding

A legacy ingress row may receive `participant_id` only when its `intent_id` maps to exactly one distinct receipt participant.

```text
one distinct participant -> safe backfill
multiple participants     -> remain unresolved
```

Ambiguous evidence is never guessed.

### Principal and resource

Historical principal and resource values are not reconstructed from current authorization policy.

```text
current policy != historical evidence
```

If the committed evidence is insufficient, those fields remain unresolved and integrity verification can report the condition.

---

## Projection behavior

REST and MCP remain thin projections over the canonical execution read models.

They do not contain adapter-local migration or repair logic.

Their normal runtimes initialize the database before serving requests, so the remote path is:

```text
runtime startup
    -> explicit db initialization/migration
    -> serve requests
    -> authorization gate for current access
    -> canonical historical read
```

The CLI differs intentionally: it may be pointed at an arbitrary existing database for forensic inspection, so it opens that database read-only and refuses to migrate it implicitly.

---

## Relationship to the audit lineage

This boundary completes a sequence of execution-audit guarantees:

```text
#82  authorization decision snapshot committed atomically
#83  canonical audit read model
#84  authorized REST/MCP historical audit projection
#85  audit integrity verification
#86  remote integrity projection
#87  ingress provenance bound to participant + intent
#90  receipt hash bound to persisted semantic effect
#93  complete historical authorization context
#94  first-class MCP server projection
#95  migration separated from read-only audit observation
```

The resulting invariant is stronger than “the verifier does not intentionally edit audit rows”:

> **Reading or verifying committed execution evidence does not change the evidence, schema, or database operating mode being inspected.**

---

## Security and forensic invariants

1. Schema migration happens only at explicit initialization/startup boundaries.
2. Audit reads do not perform DDL or historical backfill.
3. Integrity verification reports defects rather than repairing them.
4. CLI forensic reads use a physically read-only SQLite connection.
5. Current authorization still gates current remote access to historical evidence.
6. Historical principal/resource values are never guessed from current policy.
7. Ambiguous legacy ingress ownership remains unresolved.
8. `participant_id` and `intent_id` remain the semantic execution identity pair.
9. `delivery_id` remains transport provenance only.
10. No credential material is introduced into migration, receipt, audit, or integrity evidence.
