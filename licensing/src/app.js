import express from 'express';
import helmet from 'helmet';
import { rateLimit } from 'express-rate-limit';
import { resolve } from 'node:path';

const API_BODY_LIMIT = 2048;

function sendError(response, status, error) {
  response.status(status).json({ error });
}

function requireJson(request, response, next) {
  const contentType = request.get('content-type') ?? '';
  const mediaType = contentType.split(';', 1)[0].trim().toLowerCase();
  if (mediaType !== 'application/json') {
    sendError(response, 415, 'unsupported_media_type');
    return;
  }
  next();
}

function isPlainObject(value) {
  return value !== null
    && typeof value === 'object'
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}

function hasExactKeys(value, expected) {
  return isPlainObject(value)
    && Object.keys(value).length === expected.length
    && expected.every((key) => Object.hasOwn(value, key));
}

function readSessionCookie(request) {
  const header = request.headers.cookie;
  if (typeof header !== 'string') return null;

  let result = null;
  let matches = 0;
  for (const item of header.split(';')) {
    const separator = item.indexOf('=');
    if (separator < 0 || item.slice(0, separator).trim() !== 'license_session') continue;
    matches += 1;
    result = item.slice(separator + 1).trim();
  }
  return matches === 1 ? result : null;
}

function cookieAttributes(secure) {
  return `Path=/api; HttpOnly; SameSite=Strict${secure ? '; Secure' : ''}`;
}

function clearSessionCookie(response, secure) {
  response.setHeader(
    'Set-Cookie',
    `license_session=; ${cookieAttributes(secure)}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT`,
  );
}

function setSessionCookie(response, secure, sessionExpiresAt, token) {
  const expires = new Date(sessionExpiresAt);
  const maxAge = Math.max(0, Math.floor((expires.getTime() - Date.now()) / 1000));
  response.setHeader(
    'Set-Cookie',
    `license_session=${token}; ${cookieAttributes(secure)}; Max-Age=${maxAge}; Expires=${expires.toUTCString()}`,
  );
}

function createRateLimiter({ limit, windowMs }) {
  return rateLimit({
    windowMs,
    limit,
    standardHeaders: false,
    legacyHeaders: false,
    statusCode: 429,
    handler: (_request, response) => sendError(response, 429, 'too_many_requests'),
  });
}

function safeErrorHandler(error, _request, response, _next) {
  if (response.headersSent) {
    response.end();
    return;
  }

  if (error?.type === 'entity.too.large') {
    sendError(response, 413, 'payload_too_large');
    return;
  }
  if (error?.type === 'charset.unsupported' || error?.type === 'encoding.unsupported' || error?.status === 415) {
    sendError(response, 415, 'unsupported_media_type');
    return;
  }
  if (error?.type === 'entity.parse.failed' || error?.status === 400) {
    sendError(response, 400, 'invalid_request');
    return;
  }
  sendError(response, 500, 'internal_error');
}

export function createApp({
  store,
  config,
  publicDir = resolve(process.cwd(), 'public'),
  rateLimits = {},
} = {}) {
  if (!store || typeof store.activate !== 'function' || typeof store.getSession !== 'function') {
    throw new TypeError('A license store is required');
  }
  if (!config || typeof config.publicOrigin !== 'string' || typeof config.trustProxy === 'undefined') {
    throw new TypeError('Server configuration is required');
  }

  const windowMs = rateLimits.windowMs ?? 60_000;
  const apiLimit = rateLimits.apiLimit ?? 60;
  const activationLimit = rateLimits.activationLimit ?? 10;
  if (
    !Number.isInteger(windowMs) || windowMs < 1
    || !Number.isInteger(apiLimit) || apiLimit < 1
    || !Number.isInteger(activationLimit) || activationLimit < 1
  ) {
    throw new RangeError('Rate limits must be positive integers');
  }

  const secureCookie = config.publicOrigin.startsWith('https://');
  const app = express();
  app.set('trust proxy', config.trustProxy);
  app.use(helmet({
    contentSecurityPolicy: {
      useDefaults: false,
      directives: {
        defaultSrc: ["'self'"],
        baseUri: ["'self'"],
        connectSrc: ["'self'"],
        fontSrc: ["'self'"],
        formAction: ["'self'"],
        frameAncestors: ["'none'"],
        imgSrc: ["'self'", 'data:'],
        objectSrc: ["'none'"],
        scriptSrc: ["'self'"],
        styleSrc: ["'self'"],
      },
    },
  }));

  app.use('/api', (_request, response, next) => {
    response.set('Cache-Control', 'no-store');
    response.set('Pragma', 'no-cache');
    next();
  });
  app.use('/api', (request, response, next) => {
    const origin = request.get('origin');
    if (origin !== undefined && origin !== config.publicOrigin) {
      sendError(response, 403, 'forbidden_origin');
      return;
    }
    next();
  });
  app.use('/api', createRateLimiter({ limit: apiLimit, windowMs }));

  const jsonParser = express.json({
    limit: API_BODY_LIMIT,
    strict: true,
    type: 'application/json',
    verify: (request, _response, body) => {
      request.apiJsonLength = body.length;
    },
  });
  const requireNonEmptyJson = (request, response, next) => {
    if (request.apiJsonLength === 0) {
      sendError(response, 400, 'invalid_request');
      return;
    }
    next();
  };
  const activationLimiter = createRateLimiter({ limit: activationLimit, windowMs });

  app.post('/api/activate', activationLimiter, requireJson, jsonParser, requireNonEmptyJson, (request, response) => {
    if (!hasExactKeys(request.body, ['key', 'hwid'])
      || typeof request.body.key !== 'string'
      || typeof request.body.hwid !== 'string') {
      sendError(response, 400, 'invalid_request');
      return;
    }

    const session = store.activate(request.body.key, request.body.hwid);
    if (!session) {
      sendError(response, 401, 'invalid_license');
      return;
    }

    setSessionCookie(response, secureCookie, session.sessionExpiresAt, session.token);
    response.status(200).json({
      authenticated: true,
      license: {
        id: session.id,
        expiresAt: session.expiresAt,
        sessionExpiresAt: session.sessionExpiresAt,
      },
    });
  });

  app.get('/api/session', (request, response) => {
    const token = readSessionCookie(request);
    const session = token === null ? null : store.getSession(token);
    if (!session) {
      clearSessionCookie(response, secureCookie);
      response.status(200).json({ authenticated: false });
      return;
    }

    response.status(200).json({
      authenticated: true,
      license: {
        id: session.id,
        expiresAt: session.expiresAt,
        sessionExpiresAt: session.sessionExpiresAt,
      },
    });
  });

  app.post('/api/logout', requireJson, jsonParser, requireNonEmptyJson, (request, response) => {
    if (!hasExactKeys(request.body, [])) {
      sendError(response, 400, 'invalid_request');
      return;
    }

    const token = readSessionCookie(request);
    if (token !== null) store.logout(token);
    clearSessionCookie(response, secureCookie);
    response.status(200).json({ authenticated: false });
  });

  app.get('/healthz', (_request, response) => {
    response.status(200).json({ status: 'ok' });
  });

  app.use('/api', (_request, response) => sendError(response, 404, 'not_found'));
  app.use(express.static(publicDir, { dotfiles: 'ignore', fallthrough: true, index: 'index.html' }));
  app.use((_request, response) => sendError(response, 404, 'not_found'));
  app.use(safeErrorHandler);

  return app;
}
