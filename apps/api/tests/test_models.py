from app.main import Shipment, SyncReceipt

def test_shipment_version_column():
    assert 'version' in Shipment.__table__.columns

def test_receipt_hash_column():
    assert 'request_hash' in SyncReceipt.__table__.columns
