import { createApp } from './app.js';
import { loadConfig } from './config.js';
import { createLicenseStore } from './store.js';

const config = loadConfig();
const store = createLicenseStore({
  dbPath: config.licenseDb,
  hashSecret: config.licenseHashSecret,
});
const app = createApp({ store, config });
const server = app.listen(config.port, config.host, () => {
  console.log(`Licensing API listening on ${config.host}:${config.port}`);
});

let shuttingDown = false;
function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;

  const timeout = setTimeout(() => server.closeAllConnections(), 5000);
  timeout.unref();
  server.close(() => {
    clearTimeout(timeout);
    store.close();
  });
}

process.once('SIGINT', shutdown);
process.once('SIGTERM', shutdown);

server.on('error', () => {
  store.close();
  console.error('Licensing API failed to start');
  process.exitCode = 1;
});
