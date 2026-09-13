# Issue #71 — Phase 1 Verification

Phase 1 of the intent-bound authority architecture is implemented on `main`.

Implementation commit:

```text
c82492b7db13f43308df09573f7155901d5f67e9
```

The one-shot integration workflow verified the final source changes before committing them:

```text
cargo fmt
cargo test --locked
cargo clippy --locked -- -D warnings
```

All steps passed. The test suite included the existing GitHub webhook authorization/idempotency coverage and the new execution-model tests.

Phase-1 behavior now includes:

- protocol-neutral `Principal`, `AuthorityContext`, `IntentEnvelope`, `IngressProvenance`, and `ExecutionReceipt` models;
- optional explicit `intent_id` for GitHub mailbox writes;
- backward-compatible default intent identity equal to the legacy deterministic GitHub nonce;
- separation of semantic `intent_id` from transport `delivery_id`;
- GitHub authority normalized from the verified numeric GitHub user identity;
- `ingress_provenance` storage separate from message `conversation_ref` provenance;
- `execution_receipts` correlating committed messages with semantic intent identity/hash;
- existing participant ownership authorization preserved;
- existing HMAC, bearer, TOTP/session, MCP, REST, and GitHub authentication surfaces left intact.

This file exists to trigger and record the repository's normal CI against the committed Phase-1 implementation, independent of the temporary one-shot implementation workflow.
