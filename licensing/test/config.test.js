import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { loadConfig } from '../src/config.js';

const SECRET = 'a'.repeat(64);

test('config defaults are validated without creating files', (t) => {
  const cwd = mkdtempSync(join(tmpdir(), 'license-config-'));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));

  const config = loadConfig({ LICENSE_HASH_SECRET: SECRET }, cwd);
  assert.equal(config.licenseDb, join(cwd, 'data', 'licenses.sqlite'));
  assert.equal(config.publicOrigin, 'http://127.0.0.1:3000');
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 3000);
  assert.equal(config.trustProxy, false);
  assert.equal(Object.isFrozen(config), true);
  assert.equal(existsSync(join(cwd, 'data')), false);

  const custom = loadConfig({
    LICENSE_HASH_SECRET: SECRET,
    LICENSE_DB: 'custom.sqlite',
    PUBLIC_ORIGIN: 'https://licenses.example',
    HOST: '::1',
    PORT: '65535',
    TRUST_PROXY: 'loopback',
  }, cwd);
  assert.equal(custom.licenseDb, join(cwd, 'custom.sqlite'));
  assert.equal(custom.publicOrigin, 'https://licenses.example');
  assert.equal(custom.host, '::1');
  assert.equal(custom.port, 65535);
  assert.equal(custom.trustProxy, 'loopback');
});

test('config rejects invalid secrets, origins, ports, hosts, paths, and proxy trust without side effects', (t) => {
  const cwd = mkdtempSync(join(tmpdir(), 'license-invalid-config-'));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  const untouchedDirectory = join(cwd, 'must-not-be-created');
  const base = { LICENSE_HASH_SECRET: SECRET, LICENSE_DB: join(untouchedDirectory, 'db.sqlite') };

  const invalidConfigs = [
    { LICENSE_HASH_SECRET: 'not-a-secret' },
    { LICENSE_HASH_SECRET: 'g'.repeat(64) },
    { PUBLIC_ORIGIN: 'http://example.com' },
    { PUBLIC_ORIGIN: 'https://user:pass@example.com' },
    { PUBLIC_ORIGIN: 'https://example.com/path' },
    { PUBLIC_ORIGIN: 'https://example.com?query=1' },
    { PUBLIC_ORIGIN: 'https://example.com#fragment' },
    { PUBLIC_ORIGIN: 'http://127.0.0.1:3000/' },
    { PORT: '0' },
    { PORT: '65536' },
    { PORT: '3.5' },
    { PORT: '-1' },
    { HOST: '' },
    { HOST: ' 127.0.0.1' },
    { LICENSE_DB: '' },
    { TRUST_PROXY: 'true' },
    { TRUST_PROXY: '1' },
    { TRUST_PROXY: '0.0.0.0/0' },
  ];

  for (const override of invalidConfigs) {
    assert.throws(() => loadConfig({ ...base, ...override }, cwd));
  }
  assert.equal(existsSync(untouchedDirectory), false);
});

test('config accepts loopback HTTP origins, including IPv6', () => {
  assert.equal(
    loadConfig({ LICENSE_HASH_SECRET: SECRET, PUBLIC_ORIGIN: 'http://[::1]:8080' }).publicOrigin,
    'http://[::1]:8080',
  );
  assert.equal(
    loadConfig({ LICENSE_HASH_SECRET: SECRET, PUBLIC_ORIGIN: 'http://127.42.0.1:8080' }).publicOrigin,
    'http://127.42.0.1:8080',
  );
  assert.throws(() => loadConfig({ LICENSE_HASH_SECRET: SECRET, PUBLIC_ORIGIN: 'http://192.168.1.5' }));
});
