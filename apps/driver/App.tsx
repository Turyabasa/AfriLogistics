import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, AppState, FlatList, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import * as SQLite from 'expo-sqlite';
import * as Network from 'expo-network';

const API = process.env.EXPO_PUBLIC_API_URL || 'http://10.0.2.2:8000';
type Status = 'pending' | 'in_transit' | 'delayed' | 'fulfilled';
type Shipment = { id: string; reference: string; recipient_name: string; address: string; landmark: string; status: Status; version: number };
type Conflict = { id: string; reason: string; server_payload: string | null; shipment_id: string; desired_status: Exclude<Status, 'pending' | 'fulfilled'>; base_version: number };
const database = SQLite.openDatabaseAsync('driver.db');

async function db() {
  const d = await database;
  await d.execAsync(`PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS shipments(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,shipment_id TEXT NOT NULL,status TEXT NOT NULL,base_version INTEGER NOT NULL,created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY,reason TEXT NOT NULL,server_payload TEXT,shipment_id TEXT,desired_status TEXT,base_version INTEGER);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);`);
  // Upgrade databases created by the earlier Phase 1 build without losing conflict history.
  const cols = await d.getAllAsync<{ name: string }>('PRAGMA table_info(conflicts)');
  const names = new Set(cols.map(c => c.name));
  if (!names.has('shipment_id')) await d.execAsync('ALTER TABLE conflicts ADD COLUMN shipment_id TEXT');
  if (!names.has('desired_status')) await d.execAsync('ALTER TABLE conflicts ADD COLUMN desired_status TEXT');
  if (!names.has('base_version')) await d.execAsync('ALTER TABLE conflicts ADD COLUMN base_version INTEGER');
  return d;
}
async function saveSetting(key: string, value: string) { const d = await db(); await d.runAsync('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', key, value); }
async function getSetting(key: string) { const d = await db(); return (await d.getFirstAsync<{ value: string }>('SELECT value FROM settings WHERE key=?', key))?.value || ''; }
async function localShipments() { const d = await db(); const rows = await d.getAllAsync<{ payload: string }>('SELECT payload FROM shipments ORDER BY rowid DESC'); return rows.map(x => JSON.parse(x.payload) as Shipment); }
async function localConflicts() { const d = await db(); return d.getAllAsync<Conflict>('SELECT * FROM conflicts ORDER BY rowid DESC'); }
async function refreshShipments(token: string) {
  const r = await fetch(API + '/shipments', { headers: { Authorization: 'Bearer ' + token } });
  if (!r.ok) throw Error('Download failed: ' + r.status);
  const data = await r.json(); const d = await db();
  await d.withTransactionAsync(async () => {
    for (const s of data.items as Shipment[]) {
      const outstanding = await d.getFirstAsync('SELECT id FROM outbox WHERE shipment_id=? LIMIT 1', s.id);
      if (!outstanding) await d.runAsync('INSERT OR REPLACE INTO shipments(id,payload) VALUES(?,?)', s.id, JSON.stringify(s));
    }
  });
}
function operationId() { return `driver-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`; }
async function queueStatus(s: Shipment, status: 'in_transit' | 'delayed') {
  const d = await db(); const id = operationId();
  await d.withTransactionAsync(async () => {
    const pending = await d.getFirstAsync('SELECT id FROM outbox WHERE shipment_id=? LIMIT 1', s.id);
    if (pending) throw Error('Sync or resolve the existing update before adding another.');
    await d.runAsync('INSERT INTO outbox(id,shipment_id,status,base_version,created_at) VALUES(?,?,?,?,?)', id, s.id, status, s.version, Date.now());
    await d.runAsync('INSERT OR REPLACE INTO shipments(id,payload) VALUES(?,?)', s.id, JSON.stringify({ ...s, status }));
  });
}
async function sync(token: string) {
  const net = await Network.getNetworkStateAsync(); if (!net.isConnected || net.isInternetReachable === false) return;
  const d = await db();
  const ops = await d.getAllAsync<{ id: string; shipment_id: string; status: string; base_version: number }>('SELECT * FROM outbox ORDER BY created_at LIMIT 50');
  if (ops.length) {
    const r = await fetch(API + '/sync/batch', { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify({ operations: ops.map(x => ({ operation_id: x.id, shipment_id: x.shipment_id, status: x.status, base_version: x.base_version })) }) });
    if (!r.ok) throw Error('Sync failed: ' + r.status);
    const result = await r.json();
    for (const item of result.results) {
      const original = ops.find(x => x.id === item.operation_id);
      if (item.ok) {
        await d.runAsync('DELETE FROM outbox WHERE id=?', item.operation_id);
        await d.runAsync('INSERT OR REPLACE INTO shipments(id,payload) VALUES(?,?)', item.shipment.id, JSON.stringify(item.shipment));
      } else {
        await d.runAsync('INSERT OR REPLACE INTO conflicts(id,reason,server_payload,shipment_id,desired_status,base_version) VALUES(?,?,?,?,?,?)', item.operation_id, item.error, JSON.stringify(item.server || null), original?.shipment_id || item.server?.id || '', original?.status || 'in_transit', original?.base_version || 1);
        await d.runAsync('DELETE FROM outbox WHERE id=?', item.operation_id);
        if (item.server) await d.runAsync('INSERT OR REPLACE INTO shipments(id,payload) VALUES(?,?)', item.server.id, JSON.stringify(item.server));
      }
    }
  }
  await refreshShipments(token);
}

export default function App() {
  const [phone, setPhone] = useState('+256700000000'); const [code, setCode] = useState(''); const [token, setToken] = useState('');
  const [items, setItems] = useState<Shipment[]>([]); const [conflicts, setConflicts] = useState<Conflict[]>([]); const [pending, setPending] = useState(0);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  async function reload() { const d = await db(); setItems(await localShipments()); setConflicts(await localConflicts()); setPending((await d.getFirstAsync<{ count: number }>('SELECT COUNT(*) AS count FROM outbox'))?.count || 0); }
  async function run(f: () => Promise<void>) { setBusy(true); setMessage(''); try { await f(); await reload(); } catch (e) { setMessage(String(e)); } finally { setBusy(false); } }
  useEffect(() => {
    let alive = true; let timer: ReturnType<typeof setInterval> | undefined; let sub: { remove: () => void } | undefined;
    (async () => { const t = await getSetting('token'); if (alive) { setToken(t); await reload(); if (t) { try { await sync(t); await reload(); } catch { /* Keep local work when offline. */ } } } })();
    timer = setInterval(() => { if (token) run(async () => sync(token)); }, 30000);
    sub = AppState.addEventListener('change', state => { if (state === 'active' && token) run(async () => sync(token)); });
    return () => { alive = false; if (timer) clearInterval(timer); sub?.remove(); };
  }, [token]);
  async function request(path: string, body: unknown) { const r = await fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const data = await r.json(); if (!r.ok) throw Error(data.detail || 'Request failed'); return data; }
  async function acceptServer(c: Conflict) { const d = await db(); await d.runAsync('DELETE FROM conflicts WHERE id=?', c.id); setMessage('Conflict resolved: kept the server version.'); }
  async function retryMine(c: Conflict) {
    if (!c.server_payload || !c.shipment_id || !c.desired_status) throw Error('Cannot retry: server state or original change is unavailable.');
    const server = JSON.parse(c.server_payload) as Shipment; const d = await db(); const id = operationId();
    await d.withTransactionAsync(async () => {
      const queued = await d.getFirstAsync('SELECT id FROM outbox WHERE shipment_id=? LIMIT 1', c.shipment_id);
      if (queued) throw Error('A newer update is already queued for this shipment.');
      await d.runAsync('INSERT INTO outbox(id,shipment_id,status,base_version,created_at) VALUES(?,?,?,?,?)', id, c.shipment_id, c.desired_status, server.version, Date.now());
      await d.runAsync('DELETE FROM conflicts WHERE id=?', c.id);
      await d.runAsync('INSERT OR REPLACE INTO shipments(id,payload) VALUES(?,?)', server.id, JSON.stringify({ ...server, status: c.desired_status }));
    });
    setMessage('Your change was queued against the latest server version. Sync to retry; it may conflict again.');
  }
  return <SafeAreaView style={styles.screen}><ScrollView contentContainerStyle={styles.content}>
    <Text style={styles.title}>AfriLogistics Driver</Text><Text style={styles.sub}>Offline-first deliveries</Text>
    {!!message && <Text accessibilityRole="alert" style={styles.warning}>{message}</Text>}
    {!token ? <View style={styles.card}><Text style={styles.label}>Phone (E.164)</Text><TextInput style={styles.input} value={phone} onChangeText={setPhone} keyboardType="phone-pad"/><TouchableOpacity style={styles.button} onPress={() => run(async () => { await request('/auth/request-otp', { phone }); setMessage('Mock OTP requested. Use the configured local OTP code.'); })}><Text style={styles.buttonText}>Request OTP</Text></TouchableOpacity><TextInput style={styles.input} placeholder="OTP code" keyboardType="number-pad" value={code} onChangeText={setCode}/><TouchableOpacity style={styles.button} onPress={() => run(async () => { const r = await request('/auth/verify-otp', { phone, code }); await saveSetting('token', r.access_token); setToken(r.access_token); })}><Text style={styles.buttonText}>Sign in</Text></TouchableOpacity></View> : <>
      <View style={styles.card}><Text style={styles.label}>{pending} queued updates · {conflicts.length} conflicts need review</Text><TouchableOpacity style={styles.button} onPress={() => run(async () => sync(token))}><Text style={styles.buttonText}>Sync now</Text></TouchableOpacity><TouchableOpacity onPress={() => run(async () => { await saveSetting('token', ''); setToken(''); })}><Text style={styles.link}>Sign out</Text></TouchableOpacity></View>
      {conflicts.length > 0 && <View style={styles.conflictPanel}><Text style={styles.sectionTitle}>Resolve sync conflicts</Text><Text style={styles.sub}>Review the server's current version before retrying an offline change.</Text>{conflicts.map(c => { const server = c.server_payload ? JSON.parse(c.server_payload) as Shipment : null; return <View key={c.id} style={styles.conflictCard}><Text style={styles.item}>{server?.reference || c.shipment_id}</Text><Text style={styles.conflictReason}>{c.reason.replaceAll('_', ' ')}</Text><Text style={styles.sub}>Your change: {c.desired_status?.replaceAll('_', ' ') || 'unknown'} · base version {c.base_version}</Text>{server && <Text style={styles.sub}>Server: {server.status.replaceAll('_', ' ')} · version {server.version}</Text>}<TouchableOpacity style={styles.secondaryButton} onPress={() => run(() => acceptServer(c))}><Text style={styles.secondaryText}>Keep server version</Text></TouchableOpacity><TouchableOpacity disabled={!server || !c.desired_status || !c.shipment_id} style={[styles.button, (!server || !c.desired_status || !c.shipment_id) && styles.disabled]} onPress={() => run(() => retryMine(c))}><Text style={styles.buttonText}>Retry my change on latest version</Text></TouchableOpacity></View>; })}</View>}
      {items.map(s => <View style={styles.card} key={s.id}><Text style={styles.item}>{s.reference} · {s.status.replaceAll('_', ' ')}</Text><Text>{s.recipient_name}</Text><Text style={styles.sub}>{s.address}</Text><Text style={styles.landmark}>{s.landmark}</Text><View style={styles.actions}>{(['in_transit', 'delayed'] as const).map(status => <TouchableOpacity key={status} style={styles.smallButton} onPress={() => run(() => queueStatus(s, status))}><Text style={styles.buttonText}>{status.replace('_', ' ')}</Text></TouchableOpacity>)}<TouchableOpacity style={styles.smallButton} onPress={() => Alert.alert('PoD required', 'Fulfillment is disabled until secure proof-of-delivery and customer PIN verification are implemented.')}><Text style={styles.buttonText}>Fulfill</Text></TouchableOpacity></View></View>)}
    </>}{busy && <ActivityIndicator size="large"/>}
  </ScrollView></SafeAreaView>;
}
const styles = StyleSheet.create({ screen: { flex: 1, backgroundColor: '#f2f5f8' }, content: { padding: 20, gap: 12 }, title: { fontSize: 26, fontWeight: '800', color: '#17253a' }, sub: { color: '#64748b', marginTop: 4 }, card: { backgroundColor: '#fff', padding: 18, borderRadius: 12, gap: 10 }, label: { fontWeight: '700' }, input: { borderColor: '#cbd5e1', borderWidth: 1, padding: 12, borderRadius: 8 }, button: { backgroundColor: '#086c58', padding: 13, borderRadius: 8, alignItems: 'center' }, buttonText: { color: '#fff', fontWeight: '700', textTransform: 'capitalize', textAlign: 'center' }, warning: { color: '#9a3412', backgroundColor: '#ffedd5', padding: 10, borderRadius: 8 }, link: { color: '#086c58', textAlign: 'center', padding: 10 }, item: { fontSize: 18, fontWeight: '700', color: '#17253a' }, landmark: { color: '#1f6d58', fontWeight: '600' }, actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 }, smallButton: { backgroundColor: '#086c58', padding: 10, borderRadius: 7 }, conflictPanel: { backgroundColor: '#fff7ed', borderColor: '#fdba74', borderWidth: 1, borderRadius: 12, padding: 14, gap: 12 }, sectionTitle: { fontSize: 19, fontWeight: '800', color: '#7c2d12' }, conflictCard: { backgroundColor: '#fff', padding: 12, borderRadius: 10, gap: 8, borderWidth: 1, borderColor: '#fed7aa' }, conflictReason: { color: '#9a3412', fontWeight: '700', textTransform: 'capitalize' }, secondaryButton: { borderWidth: 1, borderColor: '#64748b', padding: 12, borderRadius: 8, alignItems: 'center' }, secondaryText: { color: '#334155', fontWeight: '700' }, disabled: { opacity: 0.45 } });
