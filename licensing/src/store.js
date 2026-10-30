import {
  createHmac,
  randomBytes,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';
import {
  chmodSync,
  closeSync,
  lstatSync,
  mkdirSync,
  openSync,
} from 'node:fs';
import { dirname, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const DAY_MS = 24 * 60 * 60 * 1000;
const SESSION_TTL_MS = 15 * 60 * 1000;
const LICENSE_KEY_PATTERN = /^LIC-[A-Za-z0-9_-]{43}$/;
const SESSION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HWID_PATTERN = /^[A-Za-z0-9._:-]{16,128}$/;

function validateHashSecret(hashSecret) {
  if (typeof hashSecret !== 'string' || !/^[a-fA-F0-9]{64}$/.test(hashSecret)) {
    throw new Error('License hash secret must be 64 hexadecimal characters');
  }
  return Buffer.from(hashSecret, 'hex');
}

function prepareDatabasePath(dbPath) {
  if (dbPath === ':memory:') return dbPath;
  if (typeof dbPath !== 'string' || dbPath.trim() === '' || dbPath.includes('\0')) {
    throw new Error('Invalid license database path');
  }

  const filename = resolve(dbPath);
  mkdirSync(dirname(filename), { recursive: true, mode: 0o700 });

  try {
    const fd = openSync(filename, 'wx', 0o600);
    closeSync(fd);
  } catch (error) {
    if (error?.code !== 'EEXIST') {
      throw new Error('Unable to initialize license database');
    }
  }

  let stat;
  try {
    stat = lstatSync(filename);
  } catch {
    throw new Error('Unable to inspect license database');
  }
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error('License database must be a regular file');
  }

  chmodSync(filename, 0o600);
  return filename;
}

function restrictDatabaseFiles(filename) {
  if (filename === ':memory:') return;

  for (const candidate of [filename, `${filename}-wal`, `${filename}-shm`, `${filename}-journal`]) {
    let stat;
    try {
      stat = lstatSync(candidate);
    } catch (error) {
      if (error?.code === 'ENOENT') continue;
      throw new Error('Unable to secure license database files');
    }

    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error('License database sidecar must be a regular file');
    }
    chmodSync(candidate, 0o600);
  }
}

function makeDigest(secret, domain, value) {
  return createHmac('sha256', secret)
    .update(`${domain}\0`, 'utf8')
    .update(value, 'utf8')
    .digest('hex');
}

function equalDigests(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false;
  if (!/^[a-f0-9]{64}$/.test(left) || !/^[a-f0-9]{64}$/.test(right)) return false;
  return timingSafeEqual(Buffer.from(left, 'hex'), Buffer.from(right, 'hex'));
}

function isoTimestamp(value) {
  return new Date(value).toISOString();
}

function validateTimestamp(value) {
  return Number.isSafeInteger(value) && Math.abs(value) <= 8.64e15;
}

function validHwid(hwid) {
  return typeof hwid === 'string' && HWID_PATTERN.test(hwid);
}

function validLicenseKey(key) {
  return typeof key === 'string' && LICENSE_KEY_PATTERN.test(key);
}

function validSessionToken(token) {
  return typeof token === 'string' && SESSION_TOKEN_PATTERN.test(token);
}

function validLicenseId(id) {
  return typeof id === 'string' && UUID_PATTERN.test(id);
}

export function createLicenseStore({
  dbPath,
  hashSecret,
  now = Date.now,
  busyTimeoutMs = 5000,
} = {}) {
  if (typeof now !== 'function') throw new TypeError('Clock must be a function');
  if (!Number.isInteger(busyTimeoutMs) || busyTimeoutMs < 1 || busyTimeoutMs > 60000) {
    throw new RangeError('Database timeout must be from 1 to 60000 milliseconds');
  }

  const secret = validateHashSecret(hashSecret);
  const filename = prepareDatabasePath(dbPath);
  const db = new DatabaseSync(filename, { timeout: busyTimeoutMs });
  let closed = false;

  try {
    db.exec(`
      PRAGMA foreign_keys = ON;
      PRAGMA busy_timeout = ${busyTimeoutMs};
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = FULL;
      PRAGMA wal_autocheckpoint = 1000;
    `);

    const schemaVersion = db.prepare('PRAGMA user_version').get().user_version;
    if (schemaVersion > 1) throw new Error('License database schema is newer than this server');

    db.exec(`
      CREATE TABLE IF NOT EXISTS licenses (
        id TEXT PRIMARY KEY NOT NULL,
        key_digest TEXT NOT NULL UNIQUE CHECK(length(key_digest) = 64),
        device_digest TEXT CHECK(device_digest IS NULL OR length(device_digest) = 64),
        status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
        issued_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL CHECK(expires_at > issued_at),
        revoked_at INTEGER
      ) STRICT;

      CREATE TABLE IF NOT EXISTS sessions (
        license_id TEXT PRIMARY KEY NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
        token_digest TEXT NOT NULL UNIQUE CHECK(length(token_digest) = 64),
        device_digest TEXT NOT NULL CHECK(length(device_digest) = 64),
        issued_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
      ) STRICT;

      CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
    `);

    if (schemaVersion === 0) db.exec('PRAGMA user_version = 1');
    restrictDatabaseFiles(filename);
  } catch (error) {
    db.close();
    throw error;
  }

  function ensureOpen() {
    if (closed) throw new Error('License store is closed');
  }

  function readNow() {
    const timestamp = now();
    if (!validateTimestamp(timestamp)) throw new Error('Clock returned an invalid timestamp');
    return timestamp;
  }

  function immediateTransaction(operation) {
    ensureOpen();
    db.exec('BEGIN IMMEDIATE');
    try {
      const value = operation();
      db.exec('COMMIT');
      return value;
    } catch (error) {
      try {
        db.exec('ROLLBACK');
      } catch {
        // Preserve the original database error.
      }
      throw error;
    }
  }

  const insertLicense = db.prepare(`
    INSERT INTO licenses (id, key_digest, status, issued_at, expires_at)
    VALUES (?, ?, 'active', ?, ?)
  `);
  const findByKey = db.prepare(`
    SELECT id, device_digest, status, expires_at
    FROM licenses
    WHERE key_digest = ?
  `);
  const bindDevice = db.prepare(`
    UPDATE licenses SET device_digest = ?
    WHERE id = ? AND status = 'active' AND device_digest IS NULL
  `);
  const upsertSession = db.prepare(`
    INSERT INTO sessions (license_id, token_digest, device_digest, issued_at, expires_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(license_id) DO UPDATE SET
      token_digest = excluded.token_digest,
      device_digest = excluded.device_digest,
      issued_at = excluded.issued_at,
      expires_at = excluded.expires_at
  `);
  const findSession = db.prepare(`
    SELECT
      s.license_id,
      s.device_digest AS session_device_digest,
      s.expires_at AS session_expires_at,
      l.device_digest AS license_device_digest,
      l.status,
      l.expires_at AS license_expires_at
    FROM sessions s
    JOIN licenses l ON l.id = s.license_id
    WHERE s.token_digest = ?
  `);

  function issueLicense({ days } = {}) {
    if (!Number.isInteger(days) || days < 1 || days > 3650) {
      throw new RangeError('License duration must be an integer from 1 to 3650 days');
    }

    const issuedAt = readNow();
    const expiresAt = issuedAt + (days * DAY_MS);
    if (!validateTimestamp(expiresAt) || expiresAt <= issuedAt) {
      throw new RangeError('License expiry is outside the supported date range');
    }

    const id = randomUUID();
    const key = `LIC-${randomBytes(32).toString('base64url')}`;
    const keyDigest = makeDigest(secret, 'license-key:v1', key);

    immediateTransaction(() => insertLicense.run(id, keyDigest, issuedAt, expiresAt));
    return { id, key, expiresAt: isoTimestamp(expiresAt) };
  }

  function listLicenses() {
    ensureOpen();
    const rows = db.prepare(`
      SELECT id, status, issued_at, expires_at, device_digest
      FROM licenses
      ORDER BY issued_at DESC, id ASC
    `).all();

    return rows.map((row) => ({
      id: row.id,
      status: row.status,
      issuedAt: isoTimestamp(row.issued_at),
      expiresAt: isoTimestamp(row.expires_at),
      bound: row.device_digest !== null,
    }));
  }

  function activate(key, hwid) {
    if (!validLicenseKey(key) || !validHwid(hwid)) return null;

    const keyDigest = makeDigest(secret, 'license-key:v1', key);
    const deviceDigest = makeDigest(secret, 'device-hwid:v1', hwid);

    return immediateTransaction(() => {
      const timestamp = readNow();
      const license = findByKey.get(keyDigest);
      if (!license || license.status !== 'active' || license.expires_at <= timestamp) return null;

      if (license.device_digest === null) {
        const result = bindDevice.run(deviceDigest, license.id);
        if (Number(result.changes) !== 1) return null;
      } else if (!equalDigests(license.device_digest, deviceDigest)) {
        return null;
      }

      const sessionExpiresAt = Math.min(timestamp + SESSION_TTL_MS, license.expires_at);
      const token = randomBytes(32).toString('base64url');
      const tokenDigest = makeDigest(secret, 'session-token:v1', token);
      upsertSession.run(
        license.id,
        tokenDigest,
        deviceDigest,
        timestamp,
        sessionExpiresAt,
      );

      return {
        id: license.id,
        token,
        expiresAt: isoTimestamp(license.expires_at),
        sessionExpiresAt: isoTimestamp(sessionExpiresAt),
      };
    });
  }

  function getSession(token) {
    if (!validSessionToken(token)) return null;
    const tokenDigest = makeDigest(secret, 'session-token:v1', token);

    return immediateTransaction(() => {
      const timestamp = readNow();
      const session = findSession.get(tokenDigest);
      if (!session) return null;

      const valid = session.status === 'active'
        && session.session_expires_at > timestamp
        && session.license_expires_at > timestamp
        && session.license_device_digest !== null
        && equalDigests(session.session_device_digest, session.license_device_digest);

      if (!valid) {
        db.prepare('DELETE FROM sessions WHERE token_digest = ?').run(tokenDigest);
        return null;
      }

      return {
        id: session.license_id,
        expiresAt: isoTimestamp(session.license_expires_at),
        sessionExpiresAt: isoTimestamp(session.session_expires_at),
      };
    });
  }

  function logout(token) {
    if (!validSessionToken(token)) return false;
    const tokenDigest = makeDigest(secret, 'session-token:v1', token);
    return immediateTransaction(() => {
      const result = db.prepare('DELETE FROM sessions WHERE token_digest = ?').run(tokenDigest);
      return Number(result.changes) === 1;
    });
  }

  function revokeLicense(id) {
    if (!validLicenseId(id)) return false;
    return immediateTransaction(() => {
      const row = db.prepare('SELECT id FROM licenses WHERE id = ?').get(id);
      if (!row) return false;
      db.prepare(`
        UPDATE licenses SET status = 'revoked', revoked_at = COALESCE(revoked_at, ?)
        WHERE id = ?
      `).run(readNow(), id);
      db.prepare('DELETE FROM sessions WHERE license_id = ?').run(id);
      return true;
    });
  }

  function resetDevice(id) {
    if (!validLicenseId(id)) return false;
    return immediateTransaction(() => {
      const row = db.prepare('SELECT id FROM licenses WHERE id = ?').get(id);
      if (!row) return false;
      db.prepare('UPDATE licenses SET device_digest = NULL WHERE id = ?').run(id);
      db.prepare('DELETE FROM sessions WHERE license_id = ?').run(id);
      return true;
    });
  }

  function close() {
    if (closed) return;
    db.close();
    closed = true;
  }

  return Object.freeze({
    issueLicense,
    listLicenses,
    activate,
    getSession,
    logout,
    revokeLicense,
    resetDevice,
    close,
  });
}
