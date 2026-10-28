# AfriLogistics — Phase 1 monorepo

Runnable **pilot starter**, not a production-ready financial or dispatch platform.

## Included
- FastAPI + async SQLAlchemy, PostgreSQL/PostGIS, Redis
- Single seeded demo tenant, phone OTP **mock**, JWT, dispatcher and driver roles
- Shipment creation/listing, driver assignment, tenant-scoped access
- Driver Android Expo app with SQLite shipment cache and durable outbox; sync on foreground/network polling (while app is running)
- Next.js dispatcher dashboard; Docker Compose for backend, dashboard, PostGIS and Redis
- Gzip compression for JSON responses larger than 400 bytes

## Requirements
Docker Compose; Node 22 and npm for Android app; Android emulator or device; Expo CLI via `npx`.

## Quick start
```bash
cp .env.example .env
# CHANGE JWT_SECRET before any non-local use
docker compose up --build
```
Open http://localhost:3000 (dashboard) and http://localhost:8000/docs (API). For mock OTP enter `123456` from `.env` (no SMS is sent).

### Android app
```bash
cd apps/driver
npm install
# Android emulator: API uses http://10.0.2.2:8000
npx expo start --android
```
For a **physical Android device**, set `EXPO_PUBLIC_API_URL=http://YOUR_LAN_IP:8000` in `apps/driver/.env`, ensure the API is reachable on Wi-Fi, and restart Expo. Production must use HTTPS. Android cleartext access may require a development build/network-security configuration on physical devices.

## Demo walkthrough
1. Sign in to dashboard with `+256700000000` and OTP `123456` (use a real valid test phone format).
2. Create shipment `ORD-001`.
3. Assign it to a **different** phone number such as `+256701000000` (must be a valid number).
4. Sign in to driver app using that driver phone and OTP.
5. Sync and tap `in transit`. Disable connectivity, mark `delayed`, then reconnect and tap **Sync now**. The SQLite outbox persists operations across app restarts.

## API
- `POST /auth/request-otp` `{ "phone": "+256700000000" }`
- `POST /auth/verify-otp` `{ "phone": "+256700000000", "code": "123456" }`
- `GET /me`, `GET /shipments` (Bearer token)
- `POST /shipments` (dispatcher)
- `POST /shipments/{id}/assign` `{ "driver_phone": "+256701000000" }` (dispatcher)
- `POST /sync/batch` `{ "operations": [{ "operation_id": "unique-id-123", "shipment_id": "uuid", "status": "in_transit" }] }`

## Important limitations / next steps
- **Not production safe**: mock OTP, shared demo tenant, automatic dispatcher registration, simplistic JWT storage and schema creation. Replace with organization provisioning, real OTP provider, encrypted token storage, migrations, robust rate limits, RBAC and audit logs.
- Offline sync runs on app foreground and 30-second polling while JS is active. **True Android background workers**, OS-managed scheduling and 60-second GPS tracking are NOT implemented. Android OS may suspend timers.
- Sync conflicts are retained in outbox for review, not silently discarded. Extend with explicit conflict resolution and retry backoff.
- The `fulfilled` status in this pilot is NOT verified PoD: camera compression, customer PIN, payment gateway and payout are not implemented. Do not use it for financial settlement.
- Route optimization, fleet maps, GPS, WebSockets, invoicing, real multi-tenant onboarding, payment integrations and actual mobile money escrow are later phases.
- SQLite local data is not encrypted; evaluate SQLCipher and sensitive-data minimization for deployment.
- APK <12 MB is a target, not verified; Expo development builds are generally much larger. Measure optimized release builds and consider native Android if size is hard-gated.
- For large datasets add pagination, migrations, PostGIS indexes, background processing and load tests.

## Phase 1.2: integration tests and CI

Run the full Docker-based API integration suite (uses isolated PostGIS and Redis containers):

```bash
docker compose --profile integration up --build --abort-on-container-exit --exit-code-from integration-tests integration-tests
docker compose --profile integration down -v
```

The suite runs Alembic migrations against a dedicated test database and exercises tenant provisioning, dispatcher shipment creation, driver assignment, tenant isolation, idempotent sync replay, idempotency-key reuse, stale-version conflict responses, and the secure-PoD gate. It never uses the development database volume.

GitHub Actions is configured in `.github/workflows/ci.yml` to run API model/unit checks, the Docker integration suite, and the React Native TypeScript check on pushes and pull requests.

The driver app now includes a local conflict-review panel. Drivers can accept the canonical server version or explicitly retry their previous status change against the latest server version. Retrying creates a new operation ID; conflicts are never silently overwritten. Legacy conflicts that lack enough saved metadata can be accepted, but cannot be retried automatically.

**Current validation boundary:** the integration suite and mobile runtime still need to be executed in Docker/Android CI. This local environment did not have Docker available, so no successful Docker integration run is claimed.
