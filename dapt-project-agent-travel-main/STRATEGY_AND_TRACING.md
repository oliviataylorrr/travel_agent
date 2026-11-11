# Student negotiation strategy snapshots and A2A trace delivery

This is the full Student Agent repository with additions for teaching traces and strategy history. It continues to support Travel, Lodging, Interview and Mineral agents with the same `config/agent.json`, custom `harness/instructions.md`, wallet directory and official A2A SDK.

## Student responsibility

Write/edit `harness/instructions.md` to guide negotiation and research. For example, one week require offers at the 80th percentile of comparable prices, and another week accept all positive offers. Push Markdown changes to GitHub; Render restarts with the new instructions. Your name, service type and posted asking price are still configured in `config/agent.json`. Re-register only when public listing fields change.

## Strategy persistence

When an invitation negotiation begins, the application copies the current `instructions.md` into `strategy_snapshots` in local SQLite. Each record is uniquely keyed by `job_id`, `ensemble_id`, and `negotiation_id`, and includes the SHA-256 hash, full text and creation time. Repeated rounds of that same negotiation always use the original snapshot; a new negotiation after instructions are changed gets a new hash.

Travel also snapshots its own instructions when preparing a bid and when evaluating specialist counteroffers. Specialist responses include their strategy hash and winning bid member records include hashes for later Marketplace comparison.

Full strategy text stays in the student's local `/var/data/student.sqlite3`; only the hash and standardized outcome metadata are reported to the instructor's Marketplace.

## Central traces

The code captures job-related SDK message/response envelopes, A2A task/context identifiers, and related marketplace HTTP actions. It saves copies to local `trace_outbox` in SQLite, then runs a small retry loop every six seconds to POST outstanding events to the Marketplace's authenticated `/internal/trace-events`. Unique event IDs prevent duplicate reporting. Trace forwarding does not block negotiation if Marketplace is temporarily unavailable. The student dashboard reports the number of undelivered events and recent snapshot hashes.

Only job-related business messages are traced. The application excludes Authorization headers and scrubs credential-related fields; public Marketplace pages show only trace metadata and aggregate statistics. Instructors must log in to view message payloads. Treat this as student-reported audit evidence, not cryptographically verified network recording.

## Environment variables — unchanged

Use the same `OPENAI_API_KEY`, `SESSION_SECRET_KEY`, `A2A_SHARED_SECRET`, `MARKETPLACE_URL`, `PUBLIC_BASE_URL`, `DATA_DIR=/var/data`, and `PAYMENT_MODE=simulated` configuration from the original README. **No new environment variables are required.**

## Existing Codespaces/Render upgrade

1. Replace this student repository's files in Codespaces without deleting your customized `config/agent.json`, `harness/instructions.md`, or wallet files; those files are unchanged in this ZIP template, so carry forward your edited versions.
2. Run `python -m pytest -q` and launch FastAPI locally. Test negotiation with other students when they are reachable.
3. Commit and push to GitHub; keep `DATA_DIR=/var/data` and the Render persistent disk attached.
4. The two new SQLite tables are automatically created at startup, so you need not run a separate migration, user import or Agent Card regeneration.
5. After the updated Marketplace is deployed, new work should produce trace events. Check the agent dashboard for undelivered event counts and the instructor `/dashboard` for successful delivery.

**Important:** The outbound trace layer is an additional feature and does not replace the SDK's own A2A task storage, the Marketplace's authoritative bids and payments, or the student's business records. Real Base Sepolia payments are still *not* implemented; `PAYMENT_MODE=simulated` remains mandatory.
