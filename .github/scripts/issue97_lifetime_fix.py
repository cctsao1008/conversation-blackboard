from pathlib import Path

p = Path("src/execution.rs")
s = p.read_text(encoding="utf-8")
old = '''        stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };'''
new = '''        let rows = stmt
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };'''
if old not in s:
    raise SystemExit("missing identities query marker")
p.write_text(s.replace(old, new, 1), encoding="utf-8")
