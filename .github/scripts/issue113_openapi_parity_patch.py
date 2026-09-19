from pathlib import Path

api = Path('integrations/openapi.yaml')
text = api.read_text()
old = '''  /api/admin/channels:\n    get:\n      operationId: blackboardAdminListChannels\n      summary: List all channel metadata for the Human Web Control Panel.\n      security:\n        - webSessionAuth: []\n      responses:\n        '200':\n          description: All channel summaries.\n          content:\n            application/json:\n              schema:\n                type: object\n                required: [channels]\n                properties:\n                  channels:\n                    type: array\n                    items:\n                      $ref: '#/components/schemas/ChannelSummary'\n        '401':\n          $ref: '#/components/responses/Unauthorized'\n        '403':\n          $ref: '#/components/responses/Forbidden'\n'''
new = '''  /api/admin/channels:\n    get:\n      operationId: blackboardAdminListChannels\n      summary: List a bounded window of channel metadata for the Human Web Control Panel.\n      security:\n        - webSessionAuth: []\n      parameters:\n        - name: after_name\n          in: query\n          required: false\n          description: Exclusive channel-name cursor for stable ascending directory traversal.\n          schema:\n            type: string\n        - name: limit\n          in: query\n          required: false\n          schema:\n            type: integer\n            minimum: 1\n            maximum: 200\n            default: 20\n      responses:\n        '200':\n          description: Bounded channel directory window ordered by stable channel name.\n          content:\n            application/json:\n              schema:\n                type: object\n                required: [channels, has_more]\n                properties:\n                  channels:\n                    type: array\n                    items:\n                      $ref: '#/components/schemas/ChannelSummary'\n                  has_more:\n                    type: boolean\n        '400':\n          description: Invalid channel cursor or window limit.\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/Error'\n        '401':\n          $ref: '#/components/responses/Unauthorized'\n        '403':\n          $ref: '#/components/responses/Forbidden'\n'''
if old not in text:
    raise RuntimeError('admin channel OpenAPI anchor not found')
api.write_text(text.replace(old, new, 1))

contract = Path('src/contract_parity_tests.rs')
c = contract.read_text()
c += r'''

#[test]
fn channel_directory_openapi_projects_bounded_public_and_admin_windows() {
    let api = yaml_json();
    for path in ["/api/channels", "/api/admin/channels"] {
        let get = &api["paths"][path]["get"];
        let parameters = get["parameters"].as_array().expect("channel directory parameters");
        let parameter_names = parameters
            .iter()
            .filter_map(|parameter| parameter["name"].as_str())
            .collect::<BTreeSet<_>>();
        assert!(parameter_names.contains("after_name"), "{path} must expose the stable name cursor");
        assert!(parameter_names.contains("limit"), "{path} must expose the bounded limit");
        assert_eq!(
            get["responses"]["200"]["content"]["application/json"]["schema"]["required"],
            serde_json::json!(["channels", "has_more"]),
            "{path} must advertise the bounded directory envelope"
        );
        assert_eq!(
            get["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["has_more"]["type"],
            "boolean",
            "{path} must project has_more"
        );
        assert!(get["responses"].get("400").is_some(), "{path} must document invalid cursor/limit rejection");
    }
}
'''
contract.write_text(c)
