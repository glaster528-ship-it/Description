import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';

const target = new URL('../.env', import.meta.url);
const template = readFileSync(new URL('../.env.example', import.meta.url), 'utf8');

try {
  writeFileSync(
    target,
    template.replace(/^LICENSE_HASH_SECRET=$/m, `LICENSE_HASH_SECRET=${randomBytes(32).toString('hex')}`),
    { flag: 'wx', mode: 0o600 },
  );
  console.log('Created .env with a new secret. The secret was not printed.');
} catch (error) {
  if (error.code === 'EEXIST') {
    console.log('.env already exists; kept unchanged.');
  } else {
    console.error('Could not create .env. Check directory access.');
    process.exitCode = 1;
  }
}
