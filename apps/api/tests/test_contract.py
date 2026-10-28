import ast
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app, normalize_phone, Operation, SyncBatch
from fastapi import HTTPException
import pytest

def test_health():
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status':'ok'}

def test_phone_e164():
    assert normalize_phone('+256700000000') == '+256700000000'
    with pytest.raises(HTTPException): normalize_phone('not-a-number')

def test_sync_requires_version():
    with pytest.raises(Exception):
        Operation(operation_id='abcdefgh',shipment_id='00000000-0000-0000-0000-000000000001',status='in_transit')

def test_batch_limit():
    op={'operation_id':'abcdefgh','shipment_id':'00000000-0000-0000-0000-000000000001','status':'in_transit','base_version':1}
    with pytest.raises(Exception): SyncBatch(operations=[op]*101)

def test_provisioning_rejects_missing_key():
    with TestClient(app) as client:
        response=client.post('/admin/tenants',json={'name':'Acme Fleet','admin_phone':'+256700000000'})
        assert response.status_code == 403
