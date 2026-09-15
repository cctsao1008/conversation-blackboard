from pathlib import Path

p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")

old_durable = '''        stmt.query_map([], |row| {
            Ok(DurableGrantSnapshot {
                id: row.get(0)?,
                principal_provider: row.get(1)?,
                principal_subject: row.get(2)?,
                participant_id: row.get(3)?,
                capability: row.get(4)?,
                resource: row.get(5)?,
                status: row.get(6)?,
                created_at: row.get(7)?,
                updated_at: row.get(8)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };'''
new_durable = '''        let rows = stmt
            .query_map([], |row| {
                Ok(DurableGrantSnapshot {
                    id: row.get(0)?,
                    principal_provider: row.get(1)?,
                    principal_subject: row.get(2)?,
                    participant_id: row.get(3)?,
                    capability: row.get(4)?,
                    resource: row.get(5)?,
                    status: row.get(6)?,
                    created_at: row.get(7)?,
                    updated_at: row.get(8)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };'''

old_delegated = '''        stmt.query_map([], |row| {
            Ok(DelegatedGrantSnapshot {
                id: row.get(0)?,
                principal_provider: row.get(1)?,
                principal_subject: row.get(2)?,
                participant_id: row.get(3)?,
                capability: row.get(4)?,
                resource: row.get(5)?,
                intent_id: row.get(6)?,
                expires_at: row.get(7)?,
                one_shot: row.get::<_, i64>(8)? != 0,
                consumed_at: row.get(9)?,
                consumed_intent_id: row.get(10)?,
                status: row.get(11)?,
                created_at: row.get(12)?,
                updated_at: row.get(13)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };'''
new_delegated = '''        let rows = stmt
            .query_map([], |row| {
                Ok(DelegatedGrantSnapshot {
                    id: row.get(0)?,
                    principal_provider: row.get(1)?,
                    principal_subject: row.get(2)?,
                    participant_id: row.get(3)?,
                    capability: row.get(4)?,
                    resource: row.get(5)?,
                    intent_id: row.get(6)?,
                    expires_at: row.get(7)?,
                    one_shot: row.get::<_, i64>(8)? != 0,
                    consumed_at: row.get(9)?,
                    consumed_intent_id: row.get(10)?,
                    status: row.get(11)?,
                    created_at: row.get(12)?,
                    updated_at: row.get(13)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };'''

for old, new, label in [
    (old_durable, new_durable, "durable snapshot iterator"),
    (old_delegated, new_delegated, "delegated snapshot iterator"),
]:
    if old not in s:
        raise SystemExit(f"missing repair anchor: {label}")
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
