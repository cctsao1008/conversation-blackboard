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

replacements = [
    (
        '''    pub consumed_intent_id: Option<String>,\n    pub status: String,\n    pub created_at: i64,\n    pub updated_at: i64,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationPolicySnapshot''',
        '''    pub consumed_intent_id: Option<String>,\n    pub status: String,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationPolicySnapshot''',
        "delegated snapshot fields",
    ),
    (
        '''            "consumed_intent_id",\n            "status",\n            "created_at",\n            "updated_at",\n        ],\n    )?)''',
        '''            "consumed_intent_id",\n            "status",\n        ],\n    )?)''',
        "delegated snapshot schema columns",
    ),
    (
        '''                    resource, intent_id, expires_at, one_shot, consumed_at,\n                    consumed_intent_id, status, created_at, updated_at\n             FROM delegated_grants''',
        '''                    resource, intent_id, expires_at, one_shot, consumed_at,\n                    consumed_intent_id, status\n             FROM delegated_grants''',
        "delegated snapshot select columns",
    ),
    (
        '''        let delegated_has_updated_at = table_has_columns(&conn, "delegated_grants", &["updated_at"])\n            .unwrap();\n        assert!(!durable_has_created_at);\n        assert!(!delegated_has_updated_at);''',
        '''        assert!(!durable_has_created_at);''',
        "legacy schema assertion",
    ),
]

for old, new, label in replacements:
    if old not in s:
        raise SystemExit(f"missing canonical alignment anchor: {label}")
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
