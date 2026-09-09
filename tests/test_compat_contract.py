from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "compat" / "contract.json").read_text(encoding="utf-8"))


def test_schema_fingerprint_is_frozen() -> None:
    schema = (ROOT / "schema.sql").read_bytes()
    assert hashlib.sha256(schema).hexdigest() == CONTRACT["schema_sha256"]


def test_core_route_surface_is_frozen() -> None:
    routes = {(entry["method"], entry["path"]): entry for entry in CONTRACT["routes"]}
    expected = {
        ("GET", "/api/health"),
        ("GET", "/api/whoami"),
        ("GET", "/api/messages"),
        ("POST", "/api/messages"),
        ("GET", "/api/channels"),
        ("POST", "/api/register"),
    }
    assert set(routes) == expected
    assert routes[("GET", "/api/health")]["auth"] is False
    assert routes[("POST", "/api/register")]["auth"] == "registration-key"


def test_static_allowlist_is_frozen() -> None:
    assert CONTRACT["static_allowlist"] == ["/", "/app.js", "/style.css"]


def test_security_and_identity_contract_is_frozen() -> None:
    assert CONTRACT["identity"]["token_hash"] == "sha256-lower-hex"
    assert CONTRACT["defaults"]["host"] == "127.0.0.1"
    assert CONTRACT["defaults"]["port"] == 8766
    assert CONTRACT["defaults"]["request_body_max_bytes"] == 65536
    assert CONTRACT["defaults"]["message_limit_max"] == 200
    assert CONTRACT["security_headers"]["x-content-type-options"] == "nosniff"
