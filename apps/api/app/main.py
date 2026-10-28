import asyncio
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
import phonenumbers
import redis.asyncio as redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, select, text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql+asyncpg://logistics:logistics@localhost:5432/logistics')
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
JWT_SECRET = os.getenv('JWT_SECRET', 'local-dev-only-change-me')
OTP_CODE = os.getenv('OTP_MOCK_CODE', '123456')
OTP_TTL = int(os.getenv('OTP_TTL_SECONDS', '300'))
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)
cache = redis.from_url(REDIS_URL, decode_responses=True)

class Base(DeclarativeBase):
    pass

class Tenant(Base):
    __tablename__ = 'tenants'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('tenants.id'), index=True)
    phone: Mapped[str] = mapped_column(String(25))
    role: Mapped[str] = mapped_column(String(20), default='dispatcher')
    __table_args__ = (UniqueConstraint('tenant_id', 'phone'),)

class Shipment(Base):
    __tablename__ = 'shipments'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('tenants.id'), index=True)
    reference: Mapped[str] = mapped_column(String(80))
    recipient_name: Mapped[str] = mapped_column(String(120))
    recipient_phone: Mapped[str] = mapped_column(String(25))
    address: Mapped[str] = mapped_column(Text)
    landmark: Mapped[str] = mapped_column(Text, default='')
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    assigned_driver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint('tenant_id', 'reference'),)

class SyncReceipt(Base):
    __tablename__ = 'sync_receipts'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('tenants.id'), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    operation_id: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    result: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('tenant_id', 'operation_id'),)

class TenantProvision(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    admin_phone: str

class OTPRequest(BaseModel):
    phone: str

class OTPVerify(BaseModel):
    phone: str
    code: str

class ShipmentIn(BaseModel):
    reference: str = Field(min_length=1, max_length=80)
    recipient_name: str
    recipient_phone: str
    address: str
    landmark: str = ''
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

class Assignment(BaseModel):
    driver_phone: str

class Operation(BaseModel):
    operation_id: str = Field(min_length=8, max_length=80)
    shipment_id: uuid.UUID
    status: Literal['in_transit', 'delayed', 'fulfilled']
    base_version: int = Field(ge=1)

class SyncBatch(BaseModel):
    operations: list[Operation] = Field(max_length=100)

app = FastAPI(title='AfriLogistics Phase 1 API', version='0.1.0')
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(','), allow_credentials=False, allow_methods=['*'], allow_headers=['*'])

@app.middleware('http')
async def compressed_json(request: Request, call_next):
    response = await call_next(request)
    # Compression of JSON responses for low-bandwidth clients; request bodies can also be gzip encoded.
    if 'gzip' not in request.headers.get('accept-encoding', '') or 'application/json' not in response.headers.get('content-type', ''):
        return response
    if getattr(response, 'body_iterator', None) is None:
        return response
    chunks = [chunk async for chunk in response.body_iterator]
    raw = b''.join(chunks)
    if len(raw) < 400:
        return Response(content=raw, status_code=response.status_code, media_type='application/json', headers={k:v for k,v in response.headers.items() if k.lower() != 'content-length'})
    headers = {k:v for k,v in response.headers.items() if k.lower() not in ('content-length', 'content-encoding')}
    headers['Vary'] = 'Accept-Encoding'
    headers['Content-Encoding'] = 'gzip'
    return Response(content=gzip.compress(raw, compresslevel=5), status_code=response.status_code, headers=headers, media_type='application/json')

@app.on_event('startup')
async def startup():
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS postgis'))
        # Schema is owned by Alembic. Run migrations before starting the API.
        pass

@app.on_event('shutdown')
async def shutdown():
    await cache.aclose()
    await engine.dispose()

async def db():
    async with Session() as session:
        yield session

def normalize_phone(value: str) -> str:
    try:
        parsed = phonenumbers.parse(value, None)
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError('invalid phone')
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        raise HTTPException(422, 'Use a valid E.164 phone number, e.g. +256700000000')

async def demo_tenant(session: AsyncSession) -> uuid.UUID:
    return (await session.execute(select(Tenant.id).where(Tenant.name == 'Demo Logistics'))).scalar_one()

async def current_user(authorization: str = Header(default=''), session: AsyncSession = Depends(db)) -> User:
    if not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Bearer token required')
    try:
        payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=['HS256'], options={'require': ['exp', 'sub', 'tenant_id']})
        user = await session.get(User, uuid.UUID(payload['sub']))
        if not user or str(user.tenant_id) != payload['tenant_id']:
            raise ValueError('unknown user')
        return user
    except Exception:
        raise HTTPException(401, 'Invalid or expired token')

def shipment_dict(s: Shipment):
    return {'id': str(s.id), 'reference': s.reference, 'recipient_name': s.recipient_name, 'recipient_phone': s.recipient_phone, 'address': s.address, 'landmark': s.landmark, 'latitude': s.latitude, 'longitude': s.longitude, 'status': s.status, 'version': s.version, 'assigned_driver_id': str(s.assigned_driver_id) if s.assigned_driver_id else None, 'updated_at': s.updated_at.isoformat() if s.updated_at else None}

@app.get('/health')
async def health():
    return {'status': 'ok'}

@app.post('/auth/request-otp')
async def request_otp(body: OTPRequest, session: AsyncSession = Depends(db)):
    phone = normalize_phone(body.phone)
    key = 'otp:' + hashlib.sha256(phone.encode()).hexdigest()
    throttle = 'otp-throttle:' + hashlib.sha256(phone.encode()).hexdigest()
    if not await cache.set(throttle, '1', nx=True, ex=60):
        raise HTTPException(429, 'Wait 60 seconds before requesting another OTP')
    await cache.set(key, hashlib.sha256(OTP_CODE.encode()).hexdigest(), ex=OTP_TTL)
    # Development-only mock: code is in .env, never returned by API.
    return {'message': 'Mock OTP issued. Use OTP_MOCK_CODE from your local .env.', 'expires_in': OTP_TTL}

@app.post('/auth/verify-otp')
async def verify_otp(body: OTPVerify, session: AsyncSession = Depends(db)):
    phone = normalize_phone(body.phone)
    key = 'otp:' + hashlib.sha256(phone.encode()).hexdigest()
    attempts_key = 'otp-attempts:' + hashlib.sha256(phone.encode()).hexdigest()
    if int(await cache.get(attempts_key) or '0') >= 5:
        raise HTTPException(429, 'Too many OTP attempts')
    expected = await cache.get(key)
    if not expected or not hmac.compare_digest(expected, hashlib.sha256(body.code.encode()).hexdigest()):
        await cache.incr(attempts_key)
        await cache.expire(attempts_key, OTP_TTL)
        raise HTTPException(401, 'Invalid OTP')
    await cache.delete(key, attempts_key)
    # Only pre-provisioned users may authenticate. No open tenant/user registration.
    users = (await session.execute(select(User).where(User.phone == phone))).scalars().all()
    if len(users) != 1:
        raise HTTPException(403, 'Account not provisioned or phone belongs to multiple tenants; contact your administrator')
    user = users[0]
    tenant_id = user.tenant_id
    token = jwt.encode({'sub': str(user.id), 'tenant_id': str(tenant_id), 'exp': datetime.now(timezone.utc) + timedelta(hours=12)}, JWT_SECRET, algorithm='HS256')
    return {'access_token': token, 'token_type': 'bearer', 'role': user.role, 'user_id': str(user.id)}

@app.get('/me')
async def me(user: User = Depends(current_user)):
    return {'id': str(user.id), 'phone': user.phone, 'role': user.role, 'tenant_id': str(user.tenant_id)}

@app.get('/shipments')
async def list_shipments(user: User = Depends(current_user), session: AsyncSession = Depends(db)):
    query = select(Shipment).where(Shipment.tenant_id == user.tenant_id)
    if user.role == 'driver':
        query = query.where(Shipment.assigned_driver_id == user.id)
    shipments = (await session.execute(query.order_by(Shipment.updated_at.desc()).limit(500))).scalars().all()
    return {'items': [shipment_dict(s) for s in shipments]}

@app.post('/shipments', status_code=201)
async def create_shipment(body: ShipmentIn, user: User = Depends(current_user), session: AsyncSession = Depends(db)):
    if user.role != 'dispatcher':
        raise HTTPException(403, 'Dispatcher only')
    existing = (await session.execute(select(Shipment.id).where(Shipment.tenant_id == user.tenant_id, Shipment.reference == body.reference))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, 'Reference already exists')
    s = Shipment(tenant_id=user.tenant_id, **body.model_dump())
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return shipment_dict(s)

@app.post('/shipments/{shipment_id}/assign')
async def assign(shipment_id: uuid.UUID, body: Assignment, user: User = Depends(current_user), session: AsyncSession = Depends(db)):
    if user.role != 'dispatcher':
        raise HTTPException(403, 'Dispatcher only')
    s = (await session.execute(select(Shipment).where(Shipment.id == shipment_id, Shipment.tenant_id == user.tenant_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, 'Shipment not found')
    phone = normalize_phone(body.driver_phone)
    driver = (await session.execute(select(User).where(User.tenant_id == user.tenant_id, User.phone == phone))).scalar_one_or_none()
    if not driver:
        driver = User(tenant_id=user.tenant_id, phone=phone, role='driver')
        session.add(driver)
        await session.flush()
    elif driver.role != 'driver':
        raise HTTPException(409, 'Phone belongs to dispatcher; use another phone')
    s.assigned_driver_id = driver.id
    s.version += 1
    await session.commit()
    return shipment_dict(s)

@app.post('/admin/tenants', status_code=201)
async def provision_tenant(body: TenantProvision, x_bootstrap_key: str = Header(default=''), session: AsyncSession = Depends(db)):
    bootstrap_key = os.getenv('TENANT_BOOTSTRAP_KEY', '')
    if not bootstrap_key or not hmac.compare_digest(x_bootstrap_key, bootstrap_key):
        raise HTTPException(403, 'Provisioning key required')
    phone = normalize_phone(body.admin_phone)
    tenant = Tenant(name=body.name.strip())
    session.add(tenant)
    try:
        await session.flush()
        user = User(tenant_id=tenant.id, phone=phone, role='dispatcher')
        session.add(user)
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(409, 'Tenant already exists or provisioning conflict')
    return {'tenant_id': str(tenant.id), 'admin_user_id': str(user.id), 'admin_phone': phone}

@app.post('/sync/batch')
async def sync_batch(batch: SyncBatch, user: User = Depends(current_user), session: AsyncSession = Depends(db)):
    if user.role != 'driver':
        raise HTTPException(403, 'Driver only')
    results = []
    for op in batch.operations:
        fingerprint = hashlib.sha256(op.model_dump_json().encode()).hexdigest()
        try:
            # Serialize concurrent submissions for a given operation, including missing receipts.
            if session.in_transaction():
                await session.rollback()
            async with session.begin():
                await session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': str(user.tenant_id) + ':' + op.operation_id})
                receipt = (await session.execute(select(SyncReceipt).where(SyncReceipt.tenant_id == user.tenant_id, SyncReceipt.operation_id == op.operation_id))).scalar_one_or_none()
                if receipt:
                    if receipt.user_id != user.id or receipt.request_hash != fingerprint:
                        result = {'operation_id': op.operation_id, 'ok': False, 'error': 'idempotency_key_reused'}
                    else:
                        result = json.loads(receipt.result)
                else:
                    shipment = (await session.execute(select(Shipment).where(Shipment.id == op.shipment_id, Shipment.tenant_id == user.tenant_id).with_for_update())).scalar_one_or_none()
                    if not shipment or shipment.assigned_driver_id != user.id:
                        result = {'operation_id': op.operation_id, 'ok': False, 'error': 'not_found_or_unassigned'}
                    elif shipment.version != op.base_version:
                        result = {'operation_id': op.operation_id, 'ok': False, 'error': 'version_conflict', 'server': shipment_dict(shipment)}
                    elif op.status == 'fulfilled':
                        # Completion requires separate secure PoD + PIN verification (future phase).
                        result = {'operation_id': op.operation_id, 'ok': False, 'error': 'pod_required', 'server': shipment_dict(shipment)}
                    elif shipment.status not in ('pending', 'in_transit', 'delayed'):
                        result = {'operation_id': op.operation_id, 'ok': False, 'error': 'terminal_state', 'server': shipment_dict(shipment)}
                    elif shipment.status == 'pending' and op.status == 'delayed':
                        result = {'operation_id': op.operation_id, 'ok': False, 'error': 'invalid_transition', 'server': shipment_dict(shipment)}
                    else:
                        shipment.status = op.status
                        shipment.version += 1
                        shipment.updated_at = datetime.now(timezone.utc)
                        await session.flush()
                        result = {'operation_id': op.operation_id, 'ok': True, 'shipment': shipment_dict(shipment)}
                    session.add(SyncReceipt(tenant_id=user.tenant_id, user_id=user.id, operation_id=op.operation_id, request_hash=fingerprint, result=json.dumps(result)))
            results.append(result)
        except Exception:
            await session.rollback()
            raise
    return {'results': results}
