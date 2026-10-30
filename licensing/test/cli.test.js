import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { parseLicenseCommand, runLicenseCommand } from '../src/license-cli.js';
import { createLicenseStore } from '../src/store.js';

const SECRET = 'd'.repeat(64);

function capture() {
  let value = '';
  return {
    write(chunk) {
      value += chunk;
      return true;
    },
    value: () => value,
  };
}

test('CLI validates exact commands and emits issued keys once without listing secrets', (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'license-cli-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const store = createLicenseStore({ dbPath: join(directory, 'db.sqlite'), hashSecret: SECRET });
  t.after(() => store.close());

  assert.deepEqual(parseLicenseCommand(['issue', '--days', '30']), { command: 'issue', days: 30 });
  for (const invalid of [
    [],
    ['issue', '--days', '0'],
    ['issue', '--days', '30.5'],
    ['issue', '--days', '3651'],
    ['issue', '--days=30'],
    ['list', '--extra'],
    ['revoke', '--id', 'not-a-uuid'],
  ]) {
    assert.equal(parseLicenseCommand(invalid), null);
  }

  const stdout = capture();
  const stderr = capture();
  assert.equal(runLicenseCommand(['issue', '--days', '30'], { store, stdout, stderr }), 0);
  const issuedOutput = stdout.value();
  assert.equal(issuedOutput.trim().split('\n').length, 1);
  const issued = JSON.parse(issuedOutput);
  assert.deepEqual(Object.keys(issued).sort(), ['expiresAt', 'id', 'key']);
  assert.equal(issuedOutput.split(issued.key).length - 1, 1);
  assert.equal(stderr.value(), '');

  const device = 'client-device-0001';
  store.activate(issued.key, device);
  const listed = capture();
  assert.equal(runLicenseCommand(['list'], { store, stdout: listed, stderr }), 0);
  assert.equal(listed.value().includes(issued.key), false);
  assert.equal(listed.value().includes(device), false);
  assert.equal(listed.value().includes('digest'), false);

  const missing = capture();
  assert.equal(runLicenseCommand([
    'revoke', '--id', '00000000-0000-4000-8000-000000000000',
  ], { store, stdout: capture(), stderr: missing }), 1);
  assert.equal(missing.value(), 'License not found.\n');
});

test('CLI normalizes uppercase UUIDs for device reset and revocation', (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'license-cli-case-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const store = createLicenseStore({ dbPath: join(directory, 'db.sqlite'), hashSecret: SECRET });
  t.after(() => store.close());
  const id = 'abcdef01-2345-4678-9abc-def012345678';
  for (const command of ['reset-device', 'revoke']) {
    assert.deepEqual(parseLicenseCommand([command, '--id', id.toUpperCase()]), { command, id });
  }

  const issued = store.issueLicense({ days: 30 });
  const firstSession = store.activate(issued.key, 'client-device-0001');
  const io = { store, stdout: capture(), stderr: capture() };
  assert.equal(runLicenseCommand(['reset-device', '--id', issued.id.toUpperCase()], io), 0);
  assert.equal(store.getSession(firstSession.token), null);
  assert.equal(store.listLicenses()[0].bound, false);

  const nextSession = store.activate(issued.key, 'client-device-0002');
  assert.ok(nextSession);
  assert.equal(runLicenseCommand(['revoke', '--id', issued.id.toUpperCase()], io), 0);
  assert.equal(store.getSession(nextSession.token), null);
  assert.equal(store.activate(issued.key, 'client-device-0002'), null);
  assert.equal(io.stderr.value(), '');
});
