import { pathToFileURL } from 'node:url';
import { loadConfig } from './config.js';
import { createLicenseStore } from './store.js';

const USAGE = [
  'Usage:',
  '  npm run license -- issue --days <1..3650>',
  '  npm run license -- list',
  '  npm run license -- revoke --id <uuid>',
  '  npm run license -- reset-device --id <uuid>',
].join('\n');
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function parseLicenseCommand(argv) {
  if (!Array.isArray(argv) || argv.some((argument) => typeof argument !== 'string')) return null;

  if (argv.length === 1 && argv[0] === 'list') return { command: 'list' };

  if (
    argv.length === 3
    && argv[0] === 'issue'
    && argv[1] === '--days'
    && /^\d+$/.test(argv[2])
  ) {
    const days = Number(argv[2]);
    if (Number.isInteger(days) && days >= 1 && days <= 3650) return { command: 'issue', days };
    return null;
  }

  if (
    argv.length === 3
    && (argv[0] === 'revoke' || argv[0] === 'reset-device')
    && argv[1] === '--id'
    && UUID_PATTERN.test(argv[2])
  ) {
    return { command: argv[0], id: argv[2] };
  }

  return null;
}

export function runLicenseCommand(argv, {
  store,
  stdout = process.stdout,
  stderr = process.stderr,
} = {}) {
  const parsed = parseLicenseCommand(argv);
  if (!parsed) {
    stderr.write(`${USAGE}\n`);
    return 2;
  }
  if (!store) {
    stderr.write('License store is unavailable.\n');
    return 1;
  }

  if (parsed.command === 'issue') {
    const license = store.issueLicense({ days: parsed.days });
    stdout.write(`${JSON.stringify(license)}\n`);
    return 0;
  }

  if (parsed.command === 'list') {
    stdout.write(`${JSON.stringify(store.listLicenses())}\n`);
    return 0;
  }

  if (parsed.command === 'revoke') {
    if (!store.revokeLicense(parsed.id)) {
      stderr.write('License not found.\n');
      return 1;
    }
    stdout.write(`${JSON.stringify({ id: parsed.id, revoked: true })}\n`);
    return 0;
  }

  if (!store.resetDevice(parsed.id)) {
    stderr.write('License not found.\n');
    return 1;
  }
  stdout.write(`${JSON.stringify({ id: parsed.id, reset: true })}\n`);
  return 0;
}

function main() {
  const parsed = parseLicenseCommand(process.argv.slice(2));
  if (!parsed) {
    process.stderr.write(`${USAGE}\n`);
    process.exitCode = 2;
    return;
  }

  let store;
  try {
    const config = loadConfig();
    store = createLicenseStore({
      dbPath: config.licenseDb,
      hashSecret: config.licenseHashSecret,
    });
    process.exitCode = runLicenseCommand(process.argv.slice(2), { store });
  } catch {
    process.stderr.write('License command failed.\n');
    process.exitCode = 1;
  } finally {
    store?.close();
  }
}

const entryPath = process.argv[1];
if (entryPath && import.meta.url === pathToFileURL(entryPath).href) main();
