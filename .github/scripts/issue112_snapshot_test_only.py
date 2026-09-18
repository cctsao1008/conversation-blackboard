from pathlib import Path

path = Path('src/authorization.rs')
text = path.read_text()
old = '''#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationPolicySnapshot {'''
new = '''#[cfg(test)]
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationPolicySnapshot {'''
if text.count(old) != 1:
    raise RuntimeError('snapshot type anchor mismatch')
text = text.replace(old, new, 1)
old = '''pub fn read_authorization_policy_snapshot(
    conn: &Connection,
) -> rusqlite::Result<AuthorizationPolicySnapshot> {'''
new = '''#[cfg(test)]
pub fn read_authorization_policy_snapshot(
    conn: &Connection,
) -> rusqlite::Result<AuthorizationPolicySnapshot> {'''
if text.count(old) != 1:
    raise RuntimeError('snapshot reader anchor mismatch')
text = text.replace(old, new, 1)
path.write_text(text)
