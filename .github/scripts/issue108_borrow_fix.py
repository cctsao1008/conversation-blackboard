from pathlib import Path

path = Path('src/authorization_admin.rs')
text = path.read_text()
old = '''        stmt.query_map(
            params![
                &request.principal_provider,
                &request.principal_subject,
                &request.participant_id,
                &request.capability,
                request.resource.as_deref(),
            ],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };'''
new = '''        let rows = stmt
            .query_map(
                params![
                    &request.principal_provider,
                    &request.principal_subject,
                    &request.participant_id,
                    &request.capability,
                    request.resource.as_deref(),
                ],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };'''
assert text.count(old) == 1, text.count(old)
path.write_text(text.replace(old, new, 1))
