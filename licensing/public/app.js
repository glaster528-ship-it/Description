const byId = (id) => document.getElementById(id);
const form = byId('license-form');
const sessionPanel = byId('license-session');
const keyInput = byId('license-key');
const hwidInput = byId('hwid');
const feedback = byId('feedback');
const dateFormatter = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeStyle: 'short' });
let busy = false;
let expiryTimer;

const messages = {
  invalid_license: 'не удалось активировать лицензию. проверьте ключ, HWID и статус у администратора.',
  invalid_request: 'проверьте формат ключа и отпечатка устройства.',
  too_many_requests: 'слишком много запросов. подождите минуту и повторите попытку.',
  forbidden_origin: 'адрес страницы не совпадает с PUBLIC_ORIGIN на сервере.',
  timeout: 'сервер не ответил вовремя. повторите попытку.',
  network: 'нет связи с сервером. проверьте подключение и повторите попытку.',
  service_unavailable: 'сервер временно недоступен. повторите попытку позже.',
};

class ApiError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function setConnection(state) {
  byId('connection').dataset.state = state;
  byId('connection-label').textContent = state === 'online' ? 'api доступен' : 'нет связи с api';
}

function setBusy(value) {
  busy = value;
  for (const control of [keyInput, hwidInput, byId('activate'), byId('refresh'), byId('logout')]) {
    control.disabled = value;
  }
  byId('activate-label').textContent = value ? 'проверка…' : 'активировать лицензию';
  byId('activate').setAttribute('aria-busy', String(value));
}

function showFeedback(message = '', kind = 'error') {
  feedback.textContent = message;
  feedback.dataset.kind = kind;
  feedback.hidden = !message;
}

async function request(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    setConnection('online');
    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError('service_unavailable');
    }
    if (!response.ok) throw new ApiError(data.error || 'service_unavailable');
    if (typeof data.authenticated !== 'boolean') throw new ApiError('service_unavailable');
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    setConnection('offline');
    throw new ApiError(error.name === 'AbortError' ? 'timeout' : 'network');
  } finally {
    clearTimeout(timer);
  }
}

function renderSession(data) {
  clearTimeout(expiryTimer);
  const authenticated = data.authenticated;
  if (authenticated) {
    const license = data.license;
    if (!license || !Number.isFinite(Date.parse(license.expiresAt)) || !Number.isFinite(Date.parse(license.sessionExpiresAt))) {
      throw new ApiError('service_unavailable');
    }
    byId('session-id').textContent = license.id;
    byId('license-expires').textContent = dateFormatter.format(new Date(license.expiresAt));
    byId('session-expires').textContent = dateFormatter.format(new Date(license.sessionExpiresAt));
    keyInput.value = '';
    expiryTimer = setTimeout(() => refreshSession(true), Math.max(0, Date.parse(license.sessionExpiresAt) - Date.now() + 100));
  } else {
    byId('session-id').textContent = '';
    byId('license-expires').textContent = '';
    byId('session-expires').textContent = '';
  }
  form.hidden = authenticated;
  sessionPanel.hidden = !authenticated;
  byId('panel-title').textContent = authenticated ? 'лицензия активна' : 'вход по лицензии';
  byId('panel-description').textContent = authenticated ? 'доступ подтверждён. ключ больше не нужен в этой сессии.' : 'введите ключ и стабильный отпечаток устройства.';
}

function showError(error) {
  showFeedback(messages[error.code] || messages.service_unavailable);
}

async function refreshSession(expired = false) {
  if (busy) return;
  setBusy(true);
  showFeedback();
  try {
    const data = await request('/api/session');
    renderSession(data);
    if (!data.authenticated && expired) showFeedback('сессия завершена. активируйте лицензию снова.', 'info');
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy || !form.reportValidity()) return;
  setBusy(true);
  showFeedback();
  try {
    const data = await request('/api/activate', { key: keyInput.value.trim(), hwid: hwidInput.value.trim() });
    renderSession(data);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
});

byId('refresh').addEventListener('click', () => refreshSession(true));
byId('logout').addEventListener('click', async () => {
  if (busy) return;
  setBusy(true);
  showFeedback();
  try {
    renderSession(await request('/api/logout', {}));
    keyInput.value = '';
    hwidInput.value = '';
    showFeedback('вы вышли. привязка лицензии к устройству сохранена.', 'info');
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
});

refreshSession();
