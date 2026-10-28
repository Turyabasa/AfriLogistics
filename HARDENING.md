# Phase 1.1 Hardening

## Start (fresh database)
`cp .env.example .env` then replace JWT_SECRET and TENANT_BOOTSTRAP_KEY with strong secrets.
`docker compose up --build`
The `migrate` service runs Alembic before API startup.

## Provision a tenant
`curl -X POST http://localhost:8000/admin/tenants -H 'Content-Type: application/json' -H 'X-Bootstrap-Key: <key from .env>' -d '{"name":"Acme Logistics","admin_phone":"+256700000000"}'`
Then request and verify OTP using that phone. Unprovisioned phones are rejected.

## Run tests
`docker compose exec api pip install -r requirements-dev.txt`
`docker compose exec api pytest -q`

## Sync contract
Send `operation_id`, `shipment_id`, `status`, `base_version`. Server serializes operations by idempotency key, locks shipment rows, checks tenant/assignment, rejects stale versions, and returns canonical server shipment for conflicts. Receipts are stored atomically. Mobile stores conflicts in SQLite and does not silently retry rejected operations. Driver may only have one queued operation per shipment. Fulfillment is blocked until PoD/PIN exists.

## Limitations and next hardening tasks
- Initial Alembic migration is designed for **fresh installs**. Existing Phase 1 databases require a carefully reviewed baseline migration/backfill; do not run the initial migration against populated legacy schemas.
- OTP mock is development-only. The bootstrap key must be kept secret and admin provisioning should move behind a privileged authenticated control plane with audit logs.
- Current mobile conflict records are retained locally but do not yet offer an interactive resolution screen. Build a conflict inbox and explicit retry/rebase flow.
- Sync currently uses foreground polling; Android background jobs, device-scoped encrypted token storage, and production PoD are not yet implemented.
- The initial test suite covers contracts and basic API behavior; add isolated PostGIS/Redis integration tests and CI before production.
- Phone numbers present in multiple tenants require an explicit tenant selection or membership challenge workflow.
