from pathlib import Path

p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")

replacements = [
    (
        '''        stmt.query_map([], |row| row.get::<_, String>(0))?\n            .collect::<rusqlite::Result<HashSet<_>>>()?\n    };''',
        '''        let rows = stmt\n            .query_map([], |row| row.get::<_, String>(0))?\n            .collect::<rusqlite::Result<HashSet<_>>>()?;\n        rows\n    };''',
        "participant collection lifetime",
    ),
    (
        '''        stmt.query_map([], |row| {\n            Ok((\n                row.get::<_, i64>(0)?,\n                row.get::<_, String>(1)?,\n                row.get::<_, String>(2)?,\n                row.get::<_, String>(3)?,\n                row.get::<_, String>(4)?,\n                row.get::<_, Option<String>>(5)?,\n                row.get::<_, String>(6)?,\n            ))\n        })?\n        .collect::<rusqlite::Result<Vec<_>>>()?\n    };''',
        '''        let rows = stmt\n            .query_map([], |row| {\n                Ok((\n                    row.get::<_, i64>(0)?,\n                    row.get::<_, String>(1)?,\n                    row.get::<_, String>(2)?,\n                    row.get::<_, String>(3)?,\n                    row.get::<_, String>(4)?,\n                    row.get::<_, Option<String>>(5)?,\n                    row.get::<_, String>(6)?,\n                ))\n            })?\n            .collect::<rusqlite::Result<Vec<_>>>()?;\n        rows\n    };''',
        "durable collection lifetime",
    ),
    (
        '''        stmt.query_map([], |row| {\n            Ok((\n                row.get::<_, i64>(0)?,\n                row.get::<_, String>(1)?,\n                row.get::<_, String>(2)?,\n                row.get::<_, String>(3)?,\n                row.get::<_, String>(4)?,\n                row.get::<_, Option<String>>(5)?,\n                row.get::<_, Option<String>>(6)?,\n                row.get::<_, Option<i64>>(7)?,\n                row.get::<_, i64>(8)?,\n                row.get::<_, Option<i64>>(9)?,\n                row.get::<_, Option<String>>(10)?,\n                row.get::<_, String>(11)?,\n            ))\n        })?\n        .collect::<rusqlite::Result<Vec<_>>>()?\n    };''',
        '''        let rows = stmt\n            .query_map([], |row| {\n                Ok((\n                    row.get::<_, i64>(0)?,\n                    row.get::<_, String>(1)?,\n                    row.get::<_, String>(2)?,\n                    row.get::<_, String>(3)?,\n                    row.get::<_, String>(4)?,\n                    row.get::<_, Option<String>>(5)?,\n                    row.get::<_, Option<String>>(6)?,\n                    row.get::<_, Option<i64>>(7)?,\n                    row.get::<_, i64>(8)?,\n                    row.get::<_, Option<i64>>(9)?,\n                    row.get::<_, Option<String>>(10)?,\n                    row.get::<_, String>(11)?,\n                ))\n            })?\n            .collect::<rusqlite::Result<Vec<_>>>()?;\n        rows\n    };''',
        "delegated collection lifetime",
    ),
    (
        '''    let mut durable_scopes: HashMap<\n        (String, String, String, String, Option<String>),\n        Vec<i64>,\n    > = HashMap::new();''',
        '''    type DurableScopeKey = (String, String, String, String, Option<String>);\n    let mut durable_scopes: HashMap<DurableScopeKey, Vec<i64>> = HashMap::new();''',
        "durable scope key type",
    ),
]

for old, new, label in replacements:
    if old not in s:
        raise SystemExit(f"missing repair anchor: {label}")
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")

# db init is the explicit schema migration boundary for grant verification.
p = Path("schema.sql")
s = p.read_text(encoding="utf-8")
anchor = '''CREATE INDEX IF NOT EXISTS idx_principal_grants_lookup
ON principal_grants(
    principal_provider,
    principal_subject,
    participant_id,
    capability,
    status
);
'''
insert = anchor + '''
CREATE TABLE IF NOT EXISTS delegated_grants (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_provider  TEXT NOT NULL,
    principal_subject   TEXT NOT NULL,
    participant_id      TEXT NOT NULL,
    capability          TEXT NOT NULL,
    resource            TEXT,
    intent_id           TEXT,
    expires_at          INTEGER,
    one_shot            INTEGER NOT NULL DEFAULT 0 CHECK (one_shot IN (0, 1)),
    consumed_at         INTEGER,
    consumed_intent_id  TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive')),
    created_at          INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at          INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_delegated_grants_lookup
ON delegated_grants(
    principal_provider,
    principal_subject,
    participant_id,
    capability,
    status
);
'''
if anchor not in s:
    raise SystemExit("missing repair anchor: delegated grant schema")
if "CREATE TABLE IF NOT EXISTS delegated_grants" in s:
    raise SystemExit("delegated grant schema already present")
s = s.replace(anchor, insert, 1)
p.write_text(s, encoding="utf-8")
