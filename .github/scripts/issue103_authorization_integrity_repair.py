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
]

for old, new, label in replacements:
    if old not in s:
        raise SystemExit(f"missing repair anchor: {label}")
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
