from pathlib import Path

p = Path('src/execution.rs')
s = p.read_text(encoding='utf-8')
old = '''    let authorization_columns = {
        let mut stmt = conn.prepare("PRAGMA table_info(execution_authorization_provenance)")?;
        stmt.query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?
    };
'''
new = '''    let authorization_columns = {
        let mut stmt = conn.prepare("PRAGMA table_info(execution_authorization_provenance)")?;
        let columns = stmt
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        columns
    };
'''
if old not in s:
    raise SystemExit('issue93 borrow-fix target not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
