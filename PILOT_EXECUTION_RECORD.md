# Pilot CI and Device Test Execution Record

## Current disposition

**NO-GO for real-driver onboarding.** This record intentionally distinguishes checks run in the current execution environment from CI and device checks that require external infrastructure.

## Execution metadata

- GitHub Actions run URL: **Not available** — this source bundle is not connected to a GitHub repository and no GitHub Actions runner was triggered from this environment.
- Commit SHA: **Not available** — the supplied source directory does not contain `.git` metadata. Record the SHA after committing and pushing this release.
- Physical Android device details: **Not tested** — no Android Debug Bridge (`adb`) or connected device is available in this environment.
- Docker integration test result: **Not run** — Docker CLI/daemon is unavailable in this environment.
- Real mobile money: **Disabled / not implemented in this release**. Do not add live provider credentials or enable real payouts until the required gates pass.

## Checks actually run

| Check | Result | Evidence / limitation |
|---|---|---|
| Python `compileall` on API and tests | PASS | Local source syntax compilation completed. |
| API unit-test collection/execution | BLOCKED | `pytest` collection failed because `phonenumbers` is not installed in the execution environment. CI dependency setup was corrected so `requirements-dev.txt` installs `requirements.txt` first. |
| Driver TypeScript check | BLOCKED | Driver dependencies are not installed; Expo base tsconfig and React Native type declarations are therefore unavailable. |
| Docker/PostGIS/Redis integration suite | NOT RUN | Docker executable is unavailable. |
| Android offline/reconnect/conflict tests | NOT RUN | `adb` and a physical Android device are unavailable. |

## Required GitHub Actions steps

1. Commit this release and push it to the intended repository.
2. Confirm `.github/workflows/ci.yml` is present on the pushed branch.
3. Run the `CI` workflow from **Actions → CI → Run workflow**, or open a pull request.
4. Record the workflow URL, commit SHA, job results, and any failure logs here. Do not mark the release approved while any job is red or skipped unexpectedly.

## Physical-device matrix (must be completed on hardware)

For each device, record manufacturer/model, Android version, app build/commit, SIM/operator, date, and tester. Use a test environment and non-production data.

- [ ] Online shipment list loads; then disable mobile data and Wi-Fi.
- [ ] Create queued status updates offline and confirm they survive force-close/relaunch.
- [ ] Restore connectivity; confirm queued operations sync once and server state converges.
- [ ] Replay the same operation ID/payload; confirm no duplicate state change.
- [ ] Replay the same operation ID with a different payload; confirm rejection and visible conflict.
- [ ] Change shipment on the server while the device is offline; confirm stale version is surfaced as a conflict.
- [ ] Resolve conflict using the current canonical server version; confirm a new operation ID is used.
- [ ] Repeat with throttled high-latency network and packet loss (e.g. Android emulator network controls or a controlled network proxy); capture logs.
- [ ] Verify no operation silently disappears after app process death, battery saver, or network handover.
- [ ] Verify the UI never claims a server-side update succeeded before acknowledgement.

## Mandatory payment safety gates

Keep all live payouts disabled until automated integration tests cover all of the following in a sandbox: 

- [ ] PoD photo upload validation, customer PIN verification, authorization, and replay prevention.
- [ ] Double-entry ledger invariants, balanced postings, and transaction rollback behavior.
- [ ] Collection/payout reconciliation for success, failure, timeout, duplicate callback, and out-of-order callback cases.
- [ ] Provider webhook signature validation, idempotency, and invalid-signature rejection.
- [ ] Retry and recovery behavior for provider timeouts without double payment.
- [ ] End-to-end sandbox flow with auditable correlation IDs and no real funds.

## Approval

- CI owner: ____________________  Date: __________
- Android test owner: ___________  Date: __________
- Security/finance reviewer: _____  Date: __________
- Pilot decision: **NO-GO until all required checks are evidenced and signed off.**
