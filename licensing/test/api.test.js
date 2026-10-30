import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createApp } from '../src/app.js';
import { createLicenseStore } from '../src/store.js';

const SECRET = 'c'.repeat(64);
const DEVICE_ONE = 'client-device-0001';
const DEVICE_TWO = 'client-device-0002';
const DEFAULT_ORIGIN = 'http://127.0.0.1:3000';

async function startApi(t, {
  publicOrigin = DEFAULT_ORIGIN,
  rateLimits,
  now,
} = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'license-api-'));
  const store = createLicenseStore({
    dbPath: join(directory, 'licenses.sqlite'),
    hashSecret: SECRET,
    ...(now ? { now } : {}),
  });
  const app = createApp({
    store,
    config: { publicOrigin, trustProxy: false },
    rateLimits,
  });
  const server = createServer(app);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  const baseUrl = `http://127.0.0.1:${port}`;

  t.after(async () => {
    await new Promise((resolve) => {
      server.close(resolve);
      server.closeAllConnections();
    });
    store.close();
    rmSync(directory, { recursive: true, force: true });
  });

  return { baseUrl, store };
}

async function postJson(baseUrl, path, value, headers = {}) {
  return fetch(`${baseUrl}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(value),
  });
}

function cookiePair(response) {
  const cookie = response.headers.get('set-cookie');
  assert.ok(cookie);
  return cookie.split(';', 1)[0];
}

test('activation binds the first HWID, rejects mismatches uniformly, and isolates keys', async (t) => {
  const { baseUrl, store } = await startApi(t);
  const first = store.issueLicense({ days: 30 });
  const second = store.issueLicense({ days: 30 });

  const activation = await postJson(baseUrl, '/api/activate', {
    key: first.key,
    hwid: DEVICE_ONE,
  }, { origin: DEFAULT_ORIGIN });
  assert.equal(activation.status, 200);
  const body = await activation.json();
  assert.equal(body.authenticated, true);
  assert.deepEqual(Object.keys(body.license).sort(), ['expiresAt', 'id', 'sessionExpiresAt']);
  assert.equal(body.license.id, first.id);
  assert.equal(Number.isNaN(Date.parse(body.license.expiresAt)), false);
  assert.equal(activation.headers.get('cache-control'), 'no-store');

  const cookie = activation.headers.get('set-cookie');
  assert.match(cookie, /^license_session=[A-Za-z0-9_-]{43};/);
  assert.match(cookie, /Path=\/api/);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
  assert.doesNotMatch(cookie, /; Secure(?:;|$)/);
  assert.equal(cookie.includes(first.key), false);
  const contentSecurityPolicy = activation.headers.get('content-security-policy');
  assert.ok(contentSecurityPolicy.includes("script-src 'self'"));
  assert.ok(contentSecurityPolicy.includes("style-src 'self'"));

  const wrongDevice = await postJson(baseUrl, '/api/activate', {
    key: first.key,
    hwid: DEVICE_TWO,
  });
  assert.equal(wrongDevice.status, 401);
  assert.deepEqual(await wrongDevice.json(), { error: 'invalid_license' });

  const wrongKey = await postJson(baseUrl, '/api/activate', {
    key: 'LIC-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    hwid: DEVICE_ONE,
  });
  assert.equal(wrongKey.status, 401);
  assert.deepEqual(await wrongKey.json(), { error: 'invalid_license' });

  const separate = await postJson(baseUrl, '/api/activate', {
    key: second.key,
    hwid: DEVICE_ONE,
  });
  assert.equal(separate.status, 200);
  assert.equal((await separate.json()).license.id, second.id);
});

test('session fetch checks expiry, cookie rotation, logout, reset, and revocation', async (t) => {
  let clock = Date.now();
  const { baseUrl, store } = await startApi(t, { now: () => clock });
  const license = store.issueLicense({ days: 30 });

  const anonymous = await fetch(`${baseUrl}/api/session`);
  assert.deepEqual(await anonymous.json(), { authenticated: false });
  assert.match(anonymous.headers.get('set-cookie'), /Max-Age=0/);

  const first = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_ONE });
  const firstCookie = cookiePair(first);
  const second = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_ONE });
  const secondCookie = cookiePair(second);
  assert.notEqual(firstCookie, secondCookie);

  const rotated = await fetch(`${baseUrl}/api/session`, { headers: { cookie: firstCookie } });
  assert.deepEqual(await rotated.json(), { authenticated: false });
  assert.match(rotated.headers.get('set-cookie'), /Max-Age=0/);

  const current = await fetch(`${baseUrl}/api/session`, { headers: { cookie: secondCookie } });
  assert.equal((await current.json()).authenticated, true);

  const logout = await postJson(baseUrl, '/api/logout', {}, { cookie: secondCookie });
  assert.equal(logout.status, 200);
  assert.deepEqual(await logout.json(), { authenticated: false });
  assert.match(logout.headers.get('set-cookie'), /Max-Age=0/);
  assert.deepEqual(await (await fetch(`${baseUrl}/api/session`, { headers: { cookie: secondCookie } })).json(), {
    authenticated: false,
  });

  const third = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_ONE });
  const thirdCookie = cookiePair(third);
  assert.equal(store.resetDevice(license.id), true);
  assert.deepEqual(await (await fetch(`${baseUrl}/api/session`, { headers: { cookie: thirdCookie } })).json(), {
    authenticated: false,
  });

  const rebound = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_TWO });
  assert.equal(rebound.status, 200);
  const reboundCookie = cookiePair(rebound);
  assert.equal(store.revokeLicense(license.id), true);
  assert.deepEqual(await (await fetch(`${baseUrl}/api/session`, { headers: { cookie: reboundCookie } })).json(), {
    authenticated: false,
  });
  const revoked = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_TWO });
  assert.equal(revoked.status, 401);
});

test('expired licenses and expired sessions do not authenticate', async (t) => {
  let clock = Date.now();
  const { baseUrl, store } = await startApi(t, { now: () => clock });
  const license = store.issueLicense({ days: 1 });
  const activation = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_ONE });
  const cookie = cookiePair(activation);

  clock += 15 * 60 * 1000;
  const expiredSession = await fetch(`${baseUrl}/api/session`, { headers: { cookie } });
  assert.deepEqual(await expiredSession.json(), { authenticated: false });

  clock = Date.parse(license.expiresAt);
  const expiredLicense = await postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_ONE });
  assert.equal(expiredLicense.status, 401);
  assert.deepEqual(await expiredLicense.json(), { error: 'invalid_license' });
});

test('request validation, origin checks, no-store, health, and API 404 are deterministic', async (t) => {
  const { baseUrl } = await startApi(t);

  const forbidden = await postJson(baseUrl, '/api/activate', { key: 'x', hwid: DEVICE_ONE }, {
    origin: 'https://other.example',
  });
  assert.equal(forbidden.status, 403);
  assert.deepEqual(await forbidden.json(), { error: 'forbidden_origin' });
  assert.equal(forbidden.headers.get('cache-control'), 'no-store');

  const unsupported = await fetch(`${baseUrl}/api/activate`, {
    method: 'POST',
    headers: { 'content-type': 'text/plain' },
    body: 'not json',
  });
  assert.equal(unsupported.status, 415);
  assert.deepEqual(await unsupported.json(), { error: 'unsupported_media_type' });

  const badJson = await fetch(`${baseUrl}/api/activate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{',
  });
  assert.equal(badJson.status, 400);
  assert.deepEqual(await badJson.json(), { error: 'invalid_request' });

  const tooLarge = await fetch(`${baseUrl}/api/activate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ key: 'x'.repeat(2100), hwid: DEVICE_ONE }),
  });
  assert.equal(tooLarge.status, 413);
  assert.deepEqual(await tooLarge.json(), { error: 'payload_too_large' });

  for (const body of ['[]', 'null', '{"key":"x","hwid":"y","extra":true}', '{"key":1,"hwid":"y"}']) {
    const invalid = await fetch(`${baseUrl}/api/activate`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body,
    });
    assert.equal(invalid.status, 400);
    assert.deepEqual(await invalid.json(), { error: 'invalid_request' });
  }

  const emptyLogout = await fetch(`${baseUrl}/api/logout`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
  });
  assert.equal(emptyLogout.status, 400);
  assert.deepEqual(await emptyLogout.json(), { error: 'invalid_request' });

  const unknown = await fetch(`${baseUrl}/api/admin`);
  assert.equal(unknown.status, 404);
  assert.deepEqual(await unknown.json(), { error: 'not_found' });
  assert.equal(unknown.headers.get('cache-control'), 'no-store');

  const health = await fetch(`${baseUrl}/healthz`);
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { status: 'ok' });
});

test('HTTPS public origins get Secure cookies; absent Origin remains valid for native clients', async (t) => {
  const publicOrigin = 'https://licenses.example';
  const { baseUrl, store } = await startApi(t, { publicOrigin });
  const license = store.issueLicense({ days: 3 });

  const accepted = await postJson(baseUrl, '/api/activate', {
    key: license.key,
    hwid: DEVICE_ONE,
  }, { origin: publicOrigin });
  assert.equal(accepted.status, 200);
  assert.match(accepted.headers.get('set-cookie'), /; Secure(?:;|$)/);

  const native = store.issueLicense({ days: 3 });
  const absentOrigin = await postJson(baseUrl, '/api/activate', {
    key: native.key,
    hwid: DEVICE_TWO,
  });
  assert.equal(absentOrigin.status, 200);
});

test('activation and API rate limits return exact JSON errors', async (t) => {
  const { baseUrl } = await startApi(t, {
    rateLimits: { windowMs: 60_000, activationLimit: 1, apiLimit: 10 },
  });
  const first = await postJson(baseUrl, '/api/activate', { key: 'bad', hwid: DEVICE_ONE });
  assert.equal(first.status, 401);
  const throttled = await postJson(baseUrl, '/api/activate', { key: 'bad', hwid: DEVICE_ONE });
  assert.equal(throttled.status, 429);
  assert.deepEqual(await throttled.json(), { error: 'too_many_requests' });
});

test('the general API limiter covers session and unknown endpoints', async (t) => {
  const { baseUrl } = await startApi(t, {
    rateLimits: { windowMs: 60_000, activationLimit: 10, apiLimit: 1 },
  });
  const first = await fetch(`${baseUrl}/api/session`);
  assert.equal(first.status, 200);
  const second = await fetch(`${baseUrl}/api/unknown`);
  assert.equal(second.status, 429);
  assert.deepEqual(await second.json(), { error: 'too_many_requests' });
  assert.equal(second.headers.get('cache-control'), 'no-store');
});

test('parallel first activations for different HWIDs serialize to one binding', async (t) => {
  const { baseUrl, store } = await startApi(t);
  const license = store.issueLicense({ days: 10 });
  const responses = await Promise.all([
    postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_ONE }),
    postJson(baseUrl, '/api/activate', { key: license.key, hwid: DEVICE_TWO }),
  ]);

  assert.deepEqual(responses.map((response) => response.status).sort(), [200, 401]);
  const winner = responses.find((response) => response.status === 200);
  const cookie = cookiePair(winner);
  const session = await fetch(`${baseUrl}/api/session`, { headers: { cookie } });
  assert.equal((await session.json()).authenticated, true);
});
