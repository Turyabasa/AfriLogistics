import pytest

pytestmark = pytest.mark.asyncio


async def test_provisioning_requires_bootstrap_key(client):
    response = await client.post('/admin/tenants', json={'name': 'Blocked Tenant', 'admin_phone': '+256700000001'})
    assert response.status_code == 403


async def test_end_to_end_dispatch_driver_sync_and_conflict(client, helpers):
    provision = helpers['provision']
    login = helpers['login']
    tenant = await provision(client, 'North Star Logistics', '+256700000010')
    dispatcher_token = await login(client, '+256700000010')
    dispatcher = {'Authorization': f'Bearer {dispatcher_token}'}

    created = await client.post('/shipments', headers=dispatcher, json={
        'reference': 'UG-1001', 'recipient_name': 'Amina', 'recipient_phone': '+256700000011',
        'address': 'Ntinda, Kampala', 'landmark': 'Opposite the Total Station'
    })
    assert created.status_code == 201, created.text
    shipment = created.json()

    assigned = await client.post(f"/shipments/{shipment['id']}/assign", headers=dispatcher, json={'driver_phone': '+256700000012'})
    assert assigned.status_code == 200, assigned.text
    driver_token = await login(client, '+256700000012')
    driver = {'Authorization': f'Bearer {driver_token}'}

    listed = await client.get('/shipments', headers=driver)
    assert [item['id'] for item in listed.json()['items']] == [shipment['id']]

    op = {'operation_id': 'operation-0001', 'shipment_id': shipment['id'], 'status': 'in_transit', 'base_version': assigned.json()['version']}
    first = await client.post('/sync/batch', headers=driver, json={'operations': [op]})
    assert first.status_code == 200, first.text
    assert first.json()['results'][0]['ok'] is True
    assert first.json()['results'][0]['shipment']['version'] == op['base_version'] + 1

    # Exact retries return the stored receipt and do not increment the version again.
    retry = await client.post('/sync/batch', headers=driver, json={'operations': [op]})
    assert retry.json()['results'][0]['shipment']['version'] == op['base_version'] + 1

    # Same idempotency key with a different payload is rejected.
    reused = {**op, 'status': 'delayed'}
    replay = await client.post('/sync/batch', headers=driver, json={'operations': [reused]})
    assert replay.json()['results'][0]['error'] == 'idempotency_key_reused'

    # A new operation based on a stale version receives canonical server state.
    stale = {'operation_id': 'operation-0002', 'shipment_id': shipment['id'], 'status': 'delayed', 'base_version': op['base_version']}
    conflict = await client.post('/sync/batch', headers=driver, json={'operations': [stale]})
    result = conflict.json()['results'][0]
    assert result['error'] == 'version_conflict'
    assert result['server']['version'] == op['base_version'] + 1

    fulfilled = {'operation_id': 'operation-0003', 'shipment_id': shipment['id'], 'status': 'fulfilled', 'base_version': op['base_version'] + 1}
    blocked = await client.post('/sync/batch', headers=driver, json={'operations': [fulfilled]})
    assert blocked.json()['results'][0]['error'] == 'pod_required'


async def test_tenant_data_isolation(client, helpers):
    provision, login = helpers['provision'], helpers['login']
    await provision(client, 'Tenant One', '+256700000020')
    await provision(client, 'Tenant Two', '+256700000021')
    token_one = await login(client, '+256700000020')
    token_two = await login(client, '+256700000021')
    created = await client.post('/shipments', headers={'Authorization': f'Bearer {token_one}'}, json={
        'reference': 'PRIVATE-1', 'recipient_name': 'Customer', 'recipient_phone': '+256700000022', 'address': 'Kampala'
    })
    assert created.status_code == 201
    other = await client.get('/shipments', headers={'Authorization': f'Bearer {token_two}'})
    assert other.status_code == 200
    assert other.json()['items'] == []


async def test_driver_cannot_create_shipments(client, helpers):
    await helpers['provision'](client, 'Driver Tenant', '+256700000030')
    dispatcher_token = await helpers['login'](client, '+256700000030')
    created = await client.post('/shipments', headers={'Authorization': f'Bearer {dispatcher_token}'}, json={
        'reference': 'DRIVER-1', 'recipient_name': 'Customer', 'recipient_phone': '+256700000031', 'address': 'Kampala'
    })
    await client.post(f"/shipments/{created.json()['id']}/assign", headers={'Authorization': f'Bearer {dispatcher_token}'}, json={'driver_phone': '+256700000032'})
    driver_token = await helpers['login'](client, '+256700000032')
    denied = await client.post('/shipments', headers={'Authorization': f'Bearer {driver_token}'}, json={
        'reference': 'DRIVER-2', 'recipient_name': 'Customer', 'recipient_phone': '+256700000033', 'address': 'Kampala'
    })
    assert denied.status_code == 403
