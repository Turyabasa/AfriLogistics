# Controlled Pilot Readiness Gate

## Release decision

**NO-GO for onboarding real drivers until all mandatory gates below pass.** This is a checklist and execution record, not a claim that unavailable integration or device tests passed.

## Validation attempted in the build environment

- `python -m compileall -q app tests`: passed.
- `python -m pytest -q tests/test_models.py`: blocked because the environment does not have the `phonenumbers` runtime dependency installed.
- `python -m pip install -r requirements-dev.txt`: blocked because package index DNS/network access is unavailable.
- Docker integration workflow: not run; Docker/Compose is not installed in the environment.
- React Native typecheck: not run; `node_modules` is absent and npm registry access is unavailable.
- Physical Android offline tests: not run; no Android device, `adb`, or emulator is attached.

Do not interpret source compilation or archive integrity as proof of application correctness.

## Required CI gates

Run on a GitHub Actions runner with Docker available. The PR must be green for all jobs in `.github/workflows/ci.yml`:

1. API dependency installation, compile check, and unit/model tests.
2. Integration tests using the isolated PostGIS and Redis services from the `integration` Compose profile.
3. React Native TypeScript check.

Save the workflow URL, commit SHA, and test logs in the release record. Do not waive a failed integration test for a driver pilot.

## Physical Android test matrix

Use at least two physical Android devices with different OS versions if available. Use synthetic shipments and a non-production tenant only. No customer-sensitive data or real payments.

| Scenario | Procedure | Required result |
|---|---|---|
| Baseline online sync | Sign in, sync assigned shipment, mark `in_transit` | Server and local state agree; version increments once |
| Airplane mode | Disable all connectivity, queue a permitted status update, force-close and reopen app | Outbox survives restart; UI shows pending sync clearly |
| Weak/intermittent network | Use a controlled network shaper or carrier test SIM to introduce latency, loss, and reconnects | Retries do not duplicate server-side changes; UI remains responsive |
| Reconnect | Restore connectivity and trigger sync | Operation is acknowledged; outbox entry is retired only after server acknowledgement |
| Duplicate submission | Replay the same operation ID and payload | Same stored result; no second version increment |
| Idempotency key reuse | Reuse operation ID with a changed payload | Server rejects the request and app preserves a visible conflict |
| Concurrent update | Change shipment on dispatcher side while driver's old update is queued | Server returns canonical state; no silent overwrite |
| Conflict: keep server | Select the server version in the conflict panel | Local cached state adopts canonical server state; conflict is resolved |
| Conflict: retry intent | Retry only after reviewing current server state | A new operation ID is used with the current base version; server validates transition |
| Tenant isolation | Attempt to use another tenant's token/record | No cross-tenant shipment or sync data is exposed |
| Battery/background | Run a representative route with screen off and app backgrounded | Measure battery and record sync limitations; do not assume JS timers are reliable in background |
| Storage recovery | Reboot device while operations are queued | Queue and conflict metadata persist and recover safely |

Record device model, Android version, app build SHA, network profile, steps, expected/actual result, logs, and pass/fail for every test. Repeat critical conflict and retry cases at least 20 times under packet loss.

## Security and operations gates

- Replace mock OTP with the selected provider and implement rate limits, abuse monitoring, and OTP expiry tests before production use.
- Use HTTPS for all non-local traffic; protect JWTs and local sensitive data; review Android backup behavior and SQLite encryption needs.
- Verify tenant-scoped authorization for every shipment, assignment, receipt, and administrative action.
- Configure backups and prove a restore; enable structured logs, error reporting, and alerting without logging OTPs, tokens, customer PINs, or sensitive payloads.
- Verify migration upgrade and rollback/recovery procedures on a disposable staging database.
- Define a support procedure for stuck sync operations and lost devices.

## Payments hard stop

The Phase 1.2 repository does not implement a payment-provider client or payment/payout endpoint. Keep live money movement unavailable. Do not add production provider credentials or enable a payment integration until all of the following are implemented and tested:

1. Proof-of-delivery photo validation and server-side customer PIN verification, including attempt limits, expiry, replay prevention, and authorization tests.
2. An immutable double-entry ledger with atomic posting, balance invariants, idempotency, reversals, and reconciliation tests.
3. Provider webhook signature verification, replay protection, duplicate/out-of-order callback handling, timeout and retry behavior, and sandbox integration tests.
4. End-to-end tests for payout initiation, pending/failed/success states, reconciliation, and recovery after worker or network failure.
5. A reviewed operational kill switch that fails closed, plus finance/security sign-off and provider sandbox evidence.

No shipment status by itself is sufficient authorization to disburse funds. The current API intentionally rejects `fulfilled` sync updates with `pod_required`.

## Sign-off

Pilot owner: ____________________

Engineering / CI run URL and commit SHA: ____________________

Android device test report: ____________________

Security review: ____________________

Decision/date: ____________________
