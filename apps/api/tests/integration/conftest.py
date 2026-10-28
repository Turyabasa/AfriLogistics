import os
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# Environment is set by the Docker integration-test service before app import.
os.environ.setdefault('JWT_SECRET', 'integration-test-secret-at-least-32-chars')
os.environ.setdefault('TENANT_BOOTSTRAP_KEY', 'integration-bootstrap-key')
os.environ.setdefault('OTP_MOCK_CODE', '123456')

from app.main import app, Session, cache  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def clean_database():
    async with Session() as session:
        await session.execute(text(
            'TRUNCATE TABLE sync_receipts, shipments, users, tenants RESTART IDENTITY CASCADE'
        ))
        await session.commit()
    await cache.flushdb()
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        yield c


async def provision(client: AsyncClient, name: str, phone: str):
    r = await client.post('/admin/tenants', headers={'X-Bootstrap-Key': os.environ['TENANT_BOOTSTRAP_KEY']}, json={'name': name, 'admin_phone': phone})
    assert r.status_code == 201, r.text
    return r.json()


async def login(client: AsyncClient, phone: str):
    r = await client.post('/auth/request-otp', json={'phone': phone})
    assert r.status_code == 200, r.text
    r = await client.post('/auth/verify-otp', json={'phone': phone, 'code': os.environ['OTP_MOCK_CODE']})
    assert r.status_code == 200, r.text
    return r.json()['access_token']


@pytest.fixture
def helpers():
    return {'provision': provision, 'login': login}
