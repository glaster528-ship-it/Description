import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync, statSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createLicenseStore } from '../src/store.js';

const SECRET = 'b'.repeat(64);
const DEVICE_ONE = 'client-device-0001';
const DEVICE_TWO = 'client-device-0002';

function makeDatabase(t) {
  const directory = mkdtempSync(join(tmpdir(), 'license-store-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return join(directory, 'licenses.sqlite');
}

function createStore(t, dbPath, options = {}) {
  const store = createLicenseStore({ dbPath, hashSecret: SECRET, ...options });
  t.after(() => store.close());
  return store;
}

test('license key, device binding, and session are hashed and persist across reopen', (t) => {
  const dbPath = makeDatabase(t);
  const store = createStore(t, dbPath);
  const license = store.issueLicense({ days: 30 });

  assert.match(license.id, /^[0-9a-f-]{36}$/i);
  assert.equal(license.key.length, 47);
  assert.match(license.key, /^LIC-[A-Za-z0-9_-]{43}$/);
  assert.equal(Date.parse(license.expiresAt) > Date.now(), true);

  const session = store.activate(license.key, DEVICE_ONE);
  assert.ok(session);
  assert.equal(session.id, license.id);
  assert.equal(session.token.length, 43);
  assert.match(session.token, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(Date.parse(session.sessionExpiresAt) <= Date.parse(license.expiresAt), true);
  assert.equal(store.getSession(session.token).id, license.id);
  assert.deepEqual(Object.keys(store.listLicenses()[0]).sort(), [
    'bound', 'expiresAt', 'id', 'issuedAt', 'status',
  ]);
  assert.equal(store.listLicenses()[0].bound, true);

  store.close();
  assert.equal(statSync(dbPath).mode & 0o777, 0o600);
  const databaseContents = readFileSync(dbPath);
  for (const secret of [license.key, DEVICE_ONE, session.token]) {
    assert.equal(databaseContents.includes(Buffer.from(secret)), false);
  }

  const reopened = createStore(t, dbPath);
  assert.equal(reopened.getSession(session.token).id, license.id);
  assert.equal(reopened.activate(license.key, DEVICE_ONE).id, license.id);
  assert.equal(reopened.activate(license.key, DEVICE_TWO), null);
});

test('invalid keys and HWIDs do not bind a license; valid HWIDs stay case-sensitive', (t) => {
  const store = createStore(t, makeDatabase(t));
  const license = store.issueLicense({ days: 3 });

  assert.equal(store.activate('wrong-key', DEVICE_ONE), null);
  assert.equal(store.activate(license.key, 'short'), null);
  assert.equal(store.activate(license.key, 'invalid hwid with spaces'), null);
  assert.equal(store.listLicenses()[0].bound, false);

  assert.ok(store.activate(license.key, DEVICE_ONE));
  assert.equal(store.activate(license.key, DEVICE_ONE.toUpperCase()), null);
});

test('sessions expire, and license expiry bounds the session expiry', (t) => {
  const dbPath = makeDatabase(t);
  let clock = Date.now();
  const store = createStore(t, dbPath, { now: () => clock });
  const license = store.issueLicense({ days: 1 });
  const session = store.activate(license.key, DEVICE_ONE);
  assert.equal(
    Date.parse(session.sessionExpiresAt),
    clock + 15 * 60 * 1000,
  );

  clock += 15 * 60 * 1000;
  assert.equal(store.getSession(session.token), null);

  const expiring = store.issueLicense({ days: 1 });
  clock = Date.parse(expiring.expiresAt) - 60_000;
  const shortSession = store.activate(expiring.key, DEVICE_ONE);
  assert.equal(Date.parse(shortSession.sessionExpiresAt), Date.parse(expiring.expiresAt));
  clock = Date.parse(expiring.expiresAt);
  assert.equal(store.getSession(shortSession.token), null);
  assert.equal(store.activate(expiring.key, DEVICE_ONE), null);
});

test('revoke and reset-device invalidate existing sessions', (t) => {
  const store = createStore(t, makeDatabase(t));
  const license = store.issueLicense({ days: 10 });
  const firstSession = store.activate(license.key, DEVICE_ONE);
  assert.ok(firstSession);

  assert.equal(store.resetDevice(license.id), true);
  assert.equal(store.getSession(firstSession.token), null);
  assert.equal(store.listLicenses()[0].bound, false);

  const rebound = store.activate(license.key, DEVICE_TWO);
  assert.ok(rebound);
  assert.equal(store.revokeLicense(license.id), true);
  assert.equal(store.getSession(rebound.token), null);
  assert.equal(store.activate(license.key, DEVICE_TWO), null);
  assert.equal(store.revokeLicense('not-a-uuid'), false);
});

test('license issuance is independent and lists disclose no digests or HWIDs', (t) => {
  const store = createStore(t, makeDatabase(t));
  const first = store.issueLicense({ days: 1 });
  const second = store.issueLicense({ days: 2 });
  assert.notEqual(first.key, second.key);

  store.activate(first.key, DEVICE_ONE);
  const entries = store.listLicenses();
  assert.equal(entries.length, 2);
  const serialized = JSON.stringify(entries);
  for (const secret of [first.key, second.key, DEVICE_ONE, 'digest', 'hash']) {
    assert.equal(serialized.includes(secret), false);
  }
});

test('database path must be a regular file and invalid issue durations are rejected', (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'license-invalid-store-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const dirPath = join(directory, 'as-directory');
  const { mkdirSync } = await import('node:fs');
  mkdirSync(dirPath);
  assert.throws(() => createLicenseStore({ dbPath: dirPath, hashSecret: SECRET }));

  const dbPath = join(directory, 'db.sqlite');
  assert.equal(existsSync(dbPath), false);
  const store = createLicenseStore({ dbPath, hashSecret: SECRET });
  t.after(() => store.close());
  for (const days of [0, -1, 1.5, 3651, '30']) {
    assert.throws(() => store.issueLicense({ days }));
  }
  assert.deepEqual(store.listLicenses(), []);
});
