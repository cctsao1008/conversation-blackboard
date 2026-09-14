import json
from pathlib import Path

path = Path("integrations/utcp.json")
data = json.loads(path.read_text(encoding="utf-8"))
removed = data.pop("contract_projection", None)
if removed is None:
    raise SystemExit("contract_projection marker not present")
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
