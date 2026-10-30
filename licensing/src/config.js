import { isIP } from 'node:net';
import { resolve } from 'node:path';

const HASH_SECRET_PATTERN = /^[a-fA-F0-9]{64}$/;

function isLoopbackHost(hostname) {
  const host = hostname.startsWith('[') && hostname.endsWith(']')
    ? hostname.slice(1, -1)
    : hostname;

  if (host.toLowerCase() === 'localhost') return true;

  if (isIP(host) === 4) {
    return Number(host.split('.')[0]) === 127;
  }

  return isIP(host) === 6 && host.toLowerCase() === '::1';
}

function parsePublicOrigin(value) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error('PUBLIC_ORIGIN must be an HTTP(S) origin');
  }

  let origin;
  try {
    origin = new URL(value);
  } catch {
    throw new Error('PUBLIC_ORIGIN must be an HTTP(S) origin');
  }

  if (
    !['http:', 'https:'].includes(origin.protocol)
    || origin.username !== ''
    || origin.password !== ''
    || origin.search !== ''
    || origin.hash !== ''
    || origin.origin !== value
  ) {
    throw new Error('PUBLIC_ORIGIN must not contain credentials, path, query, or fragment');
  }

  if (origin.protocol === 'http:' && !isLoopbackHost(origin.hostname)) {
    throw new Error('Non-loopback PUBLIC_ORIGIN must use HTTPS');
  }

  return origin.origin;
}

function parsePort(value) {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) {
    throw new Error('PORT must be an integer from 1 to 65535');
  }

  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('PORT must be an integer from 1 to 65535');
  }
  return port;
}

export function loadConfig(env = process.env, cwd = process.cwd()) {
  if (env === null || typeof env !== 'object') {
    throw new TypeError('Environment configuration must be an object');
  }

  const licenseHashSecret = env.LICENSE_HASH_SECRET;
  if (typeof licenseHashSecret !== 'string' || !HASH_SECRET_PATTERN.test(licenseHashSecret)) {
    throw new Error('LICENSE_HASH_SECRET must be 64 hexadecimal characters');
  }

  const dbInput = env.LICENSE_DB ?? './data/licenses.sqlite';
  if (typeof dbInput !== 'string' || dbInput.trim() === '' || dbInput.includes('\0')) {
    throw new Error('LICENSE_DB must be a non-empty path');
  }

  const host = env.HOST ?? '127.0.0.1';
  if (
    typeof host !== 'string'
    || host.trim() === ''
    || host !== host.trim()
    || /[\0\r\n]/.test(host)
  ) {
    throw new Error('HOST must be a non-empty hostname or IP address');
  }

  const trustProxyInput = env.TRUST_PROXY ?? '';
  if (typeof trustProxyInput !== 'string') {
    throw new Error('TRUST_PROXY must be empty, false, or loopback');
  }

  let trustProxy;
  if (trustProxyInput === '' || trustProxyInput === 'false') {
    trustProxy = false;
  } else if (trustProxyInput === 'loopback') {
    trustProxy = 'loopback';
  } else {
    throw new Error('TRUST_PROXY must be empty, false, or loopback');
  }

  return Object.freeze({
    licenseHashSecret,
    licenseDb: resolve(cwd, dbInput),
    publicOrigin: parsePublicOrigin(env.PUBLIC_ORIGIN ?? 'http://127.0.0.1:3000'),
    host,
    port: parsePort(env.PORT ?? '3000'),
    trustProxy,
  });
}
