from pathlib import Path

p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")

broken = '''            {\n                "name": "blackboard_execution_receipt",\n                "title": "Read Blackboard Execution Receipt",\n                "description": "Read back the durable semantic execution receipt for one authenticated participant intent.",\n                "inputSchema": {\n                    "type": "object",\n                    "properties": {\n                        "participant_id": contract_schema::participant_id_schema(),\n                        "intent_id": contract_schema::intent_id_schema(),\n                        "auth": auth_schema()\n                    },\n                    "required": ["participant_id", "intent_id", "auth"],\n                    "additionalProperties": false\n                },\n                "outputSchema": contract_schema::execution_receipt_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            ,\n            {\n'''

fixed = '''            {\n                "name": "blackboard_execution_receipt",\n                "title": "Read Blackboard Execution Receipt",\n                "description": "Read back the durable semantic execution receipt for one authenticated participant intent.",\n                "inputSchema": {\n                    "type": "object",\n                    "properties": {\n                        "participant_id": contract_schema::participant_id_schema(),\n                        "intent_id": contract_schema::intent_id_schema(),\n                        "auth": auth_schema()\n                    },\n                    "required": ["participant_id", "intent_id", "auth"],\n                    "additionalProperties": false\n                },\n                "outputSchema": contract_schema::execution_receipt_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            },\n            {\n'''

if broken not in s:
    raise SystemExit("expected malformed MCP receipt/audit separator not found")

p.write_text(s.replace(broken, fixed, 1), encoding="utf-8")
