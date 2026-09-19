from pathlib import Path

path = Path('web/app.js')
text = path.read_text()
old = '''  $("refresh").addEventListener("click", async () => {\n    await refreshChannelCounts();\n    if (state.historyTargetId) {\n'''
new = '''  $("refresh").addEventListener("click", async () => {\n    if (state.historyTargetId) {\n'''
if old not in text:
    raise RuntimeError('manual refresh directory coupling anchor not found')
path.write_text(text.replace(old, new, 1))
