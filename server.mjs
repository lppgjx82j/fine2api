import http from 'node:http';
import { readFileSync, writeFileSync, mkdirSync, renameSync, existsSync, chmodSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { randomUUID, randomBytes, timingSafeEqual } from 'node:crypto';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import compatibility from './proxy.js';

const ROOT = dirname(fileURLToPath(import.meta.url));
const MAX_BODY = 2 * 1024 * 1024;
const DEFAULT_UPSTREAM = 'https://gateway.finest.so/gateway/openai/v1';
const DEFAULT_SETTINGS = { rotation: 'round-robin', timeoutMs: 120000, cooldownMs: 60000, stripParameters: true };
const PUBLIC_FILES = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/styles.css', ['styles.css', 'text/css; charset=utf-8']],
]);

class HttpError extends Error {
  constructor(status, message, type = 'invalid_request_error') {
    super(message); this.status = status; this.type = type;
  }
}

function json(res, status, value) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
}

async function readJson(req) {
  if (Number(req.headers['content-length']) > MAX_BODY) throw new HttpError(413, 'Request body exceeds 2 MiB.');
  const chunks = []; let length = 0;
  for await (const chunk of req) {
    length += chunk.length;
    if (length > MAX_BODY) throw new HttpError(413, 'Request body exceeds 2 MiB.');
    chunks.push(chunk);
  }
  let value;
  try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new HttpError(400, 'Request body must be valid JSON.'); }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new HttpError(400, 'Request body must be a JSON object.');
  return value;
}

function validSettings(input, current) {
  const next = { ...current };
  for (const [key, value] of Object.entries(input)) {
    if (!Object.hasOwn(DEFAULT_SETTINGS, key)) throw new HttpError(400, `Unknown setting: ${key}`);
    if (key === 'rotation' && !['round-robin', 'priority'].includes(value)) throw new HttpError(400, 'Invalid rotation policy.');
    if (key === 'stripParameters' && typeof value !== 'boolean') throw new HttpError(400, 'stripParameters must be a boolean.');
    if (key === 'timeoutMs' && (!Number.isInteger(value) || value < 1000 || value > 600000)) throw new HttpError(400, 'timeoutMs must be between 1000 and 600000.');
    if (key === 'cooldownMs' && (!Number.isInteger(value) || value < 1000 || value > 3600000)) throw new HttpError(400, 'cooldownMs must be between 1000 and 3600000.');
    next[key] = value;
  }
  return next;
}

function validateAccount(input, creating = false) {
  const result = {};
  for (const key of Object.keys(input)) {
    if (!['name', 'key', 'enabled'].includes(key)) throw new HttpError(400, `Unknown account field: ${key}`);
  }
  for (const field of ['name', 'key']) {
    if (!creating && input[field] === undefined) continue;
    if (typeof input[field] !== 'string' || !input[field].trim() || input[field].length > (field === 'key' ? 4096 : 80)) {
      throw new HttpError(400, `A valid ${field} is required.`);
    }
    result[field] = input[field].trim();
    if (field === 'key' && /[\s\x00-\x1f\x7f]/.test(result[field])) throw new HttpError(400, 'API key must not contain whitespace or control characters.');
  }
  if (input.enabled !== undefined) {
    if (typeof input.enabled !== 'boolean') throw new HttpError(400, 'enabled must be a boolean.');
    result.enabled = input.enabled;
  }
  return result;
}

function usageObserver(streaming, onUsage) {
  let pending = ''; let tooLarge = false;
  const decoder = new TextDecoder();
  const inspect = (text) => {
    try {
      const usage = JSON.parse(text).usage;
      if (usage) onUsage(usage);
    } catch { /* Usage metadata is optional; never modify the upstream payload. */ }
  };
  return new Transform({
    transform(chunk, _encoding, callback) {
      if (!tooLarge) {
        pending += decoder.decode(chunk, { stream: true });
        if (streaming) {
          let end;
          while ((end = pending.indexOf('\n')) >= 0) {
            const line = pending.slice(0, end).trim(); pending = pending.slice(end + 1);
            if (line.startsWith('data:')) inspect(line.slice(5).trim());
          }
        }
        if (pending.length > MAX_BODY) { tooLarge = true; pending = ''; }
      }
      callback(null, chunk);
    },
    flush(callback) {
      pending += decoder.decode();
      if (!tooLarge) {
        if (!streaming) inspect(pending);
        else if (pending.trim().startsWith('data:')) inspect(pending.trim().slice(5).trim());
      }
      callback();
    },
  });
}

export async function createApp(options = {}) {
  const dataDir = resolve(options.dataDir || process.env.DATA_DIR || join(ROOT, '.data'));
  mkdirSync(dataDir, { recursive: true, mode: 0o700 });
  const statePath = join(dataDir, 'state.json');
  const keyPath = join(dataDir, 'admin-key');
  let proxyKey = options.proxyKey || process.env.PROXY_KEY;
  if (!proxyKey) {
    if (existsSync(keyPath)) proxyKey = readFileSync(keyPath, 'utf8').trim();
    else { proxyKey = `fine-${randomBytes(32).toString('hex')}`; writeFileSync(keyPath, `${proxyKey}\n`, { mode: 0o600 }); }
    if (!proxyKey) throw new Error('The stored access key is empty.');
    chmodSync(keyPath, 0o600);
  }
  const upstream = new URL(options.upstream || process.env.UPSTREAM || DEFAULT_UPSTREAM);
  if (!['http:', 'https:'].includes(upstream.protocol) || upstream.username || upstream.password || upstream.search || upstream.hash) throw new Error('UPSTREAM must be an HTTP(S) base URL without credentials, query or fragment.');
  const base = upstream.href.replace(/\/+$/, '');
  const state = { settings: { ...DEFAULT_SETTINGS }, accounts: [] };
  if (existsSync(statePath)) {
    const saved = JSON.parse(readFileSync(statePath, 'utf8'));
    state.settings = validSettings(saved.settings || {}, DEFAULT_SETTINGS);
    if (!Array.isArray(saved.accounts)) throw new Error('Invalid account storage.');
    state.accounts = saved.accounts.map((a) => ({ ...validateAccount({ name: a.name, key: a.key, enabled: a.enabled }, true), id: a.id, createdAt: a.createdAt, cooldownUntil: 0, lastError: null }));
    chmodSync(statePath, 0o600);
  }
  if (options.timeoutMs !== undefined) state.settings = validSettings({ timeoutMs: options.timeoutMs }, state.settings);
  function save() {
    const temp = `${statePath}.tmp`;
    const accounts = state.accounts.map(({ id, name, key, enabled, createdAt }) => ({ id, name, key, enabled, createdAt }));
    writeFileSync(temp, JSON.stringify({ settings: state.settings, accounts }, null, 2), { mode: 0o600 });
    chmodSync(temp, 0o600); renameSync(temp, statePath);
  }
  const safeAccount = ({ id, name, key, enabled, createdAt, cooldownUntil, lastError }) => ({ id, name, keyHint: key.length > 8 ? `••••${key.slice(-4)}` : '••••', enabled, createdAt, cooldownUntil, lastError });
  const logs = [];
  const stats = { requests: 0, successes: 0, failures: 0, inputTokens: 0, outputTokens: 0, averageLatencyMs: 0 };
  let latencyTotal = 0; let cursor = 0;
  const activeControllers = new Set();
  const expectedKey = Buffer.from(proxyKey);
  const authorized = (req) => {
    const value = req.headers.authorization || '';
    if (!value.startsWith('Bearer ')) return false;
    const key = Buffer.from(value.slice(7));
    return key.length === expectedKey.length && timingSafeEqual(key, expectedKey);
  };
  const selectAccounts = () => {
    const available = state.accounts.filter((a) => a.enabled && a.cooldownUntil <= Date.now());
    if (state.settings.rotation !== 'round-robin' || !available.length) return available;
    const start = cursor++ % available.length;
    return [...available.slice(start), ...available.slice(0, start)];
  };
  const cooldown = (account, status) => {
    account.cooldownUntil = Date.now() + state.settings.cooldownMs;
    account.lastError = status ? `Upstream HTTP ${status}` : 'Upstream connection failed';
  };
  function record(entry) {
    logs.unshift(entry); if (logs.length > 200) logs.pop();
    stats.requests++;
    if (entry.status >= 200 && entry.status < 300) stats.successes++; else stats.failures++;
    stats.inputTokens += entry.inputTokens; stats.outputTokens += entry.outputTokens;
    latencyTotal += entry.durationMs; stats.averageLatencyMs = Math.round(latencyTotal / stats.requests);
  }

  async function forward(req, res, path, payload) {
    const started = Date.now();
    const prepared = payload ? compatibility.prepareBody(payload, state.settings.stripParameters) : { removed: [] };
    const entry = { id: randomUUID(), time: new Date().toISOString(), model: payload?.model || '', account: '', status: 502, durationMs: 0, stream: payload?.stream === true, inputTokens: 0, outputTokens: 0, error: null, removedParameters: prepared.removed };
    const controller = new AbortController(); activeControllers.add(controller);
    const disconnect = () => { if (!res.writableFinished) controller.abort(); };
    res.on('close', disconnect);
    let timer;
    try {
      const candidates = selectAccounts();
      if (!candidates.length) throw new HttpError(503, 'No available upstream account. Add an enabled API key or wait for cooldown.', 'service_unavailable');
      for (let i = 0; i < candidates.length; i++) {
        if (controller.signal.aborted) throw new HttpError(499, 'Client disconnected.');
        const account = candidates[i]; entry.account = account.name;
        let response;
        const attempt = new AbortController();
        const abort = () => attempt.abort(); controller.signal.addEventListener('abort', abort, { once: true });
        timer = setTimeout(() => attempt.abort(), state.settings.timeoutMs);
        try {
          response = await fetch(`${base}${path}`, {
            method: payload ? 'POST' : 'GET',
            headers: { Authorization: `Bearer ${account.key}`, Accept: payload?.stream ? 'text/event-stream' : 'application/json', ...(payload ? { 'Content-Type': 'application/json' } : {}) },
            body: payload ? JSON.stringify(prepared.body) : undefined,
            signal: attempt.signal, redirect: 'error',
          });
          if (!response.ok) {
            const status = response.status;
            await response.body?.cancel();
            const retryable = [401, 403, 429].includes(status) || status >= 500;
            if (retryable) cooldown(account, status);
            if (retryable && i + 1 < candidates.length) continue;
            if (status === 429) {
              const retryAfter = response.headers.get('retry-after');
              if (retryAfter && /^\d{1,6}$/.test(retryAfter)) res.setHeader('Retry-After', retryAfter);
            }
            throw new HttpError(status, `Upstream returned HTTP ${status}. Check the account, model and request parameters.`, 'upstream_error');
          }
          account.cooldownUntil = 0; account.lastError = null;
          entry.status = response.status;
          res.writeHead(response.status, {
            'Content-Type': response.headers.get('content-type') || 'application/json',
            'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
          });
          if (response.body) {
            const observer = usageObserver(entry.stream, (usage) => {
              const input = usage.prompt_tokens ?? usage.input_tokens;
              const output = usage.completion_tokens ?? usage.output_tokens;
              if (Number.isSafeInteger(input) && input >= 0) entry.inputTokens = input;
              if (Number.isSafeInteger(output) && output >= 0) entry.outputTokens = output;
            });
            await pipeline(Readable.fromWeb(response.body), observer, res);
          } else res.end();
          return;
        } catch (error) {
          if (res.headersSent) throw error;
          if (error instanceof HttpError || controller.signal.aborted) throw error;
          cooldown(account);
          if (i + 1 === candidates.length) throw new HttpError(attempt.signal.aborted ? 504 : 502, attempt.signal.aborted ? 'Upstream request timed out.' : 'Could not connect to upstream.', 'upstream_error');
        } finally {
          clearTimeout(timer); controller.signal.removeEventListener('abort', abort);
        }
      }
    } catch (error) {
      entry.status = controller.signal.aborted ? 499 : (error.status || 502);
      entry.error = error instanceof HttpError ? error.message : 'Upstream connection interrupted.';
      if (res.headersSent) res.destroy();
      else json(res, entry.status === 499 ? 502 : entry.status, { error: { message: entry.error, type: error.type || 'upstream_error' } });
    } finally {
      clearTimeout(timer); activeControllers.delete(controller); res.off('close', disconnect);
      entry.durationMs = Date.now() - started;
      if (payload) record(entry);
    }
  }

  const server = http.createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
    try {
      const url = new URL(req.url, 'http://localhost');
      const path = url.pathname;
      // Browser management is same-origin; native OpenAI clients are unaffected.
      if (req.headers.origin) {
        let origin;
        try { origin = new URL(req.headers.origin); } catch { throw new HttpError(403, 'Origin is not allowed.'); }
        if (origin.host !== req.headers.host || !['http:', 'https:'].includes(origin.protocol)) throw new HttpError(403, 'Origin is not allowed.');
      }
      if (req.method === 'OPTIONS') { res.writeHead(204, { Allow: 'GET, POST, PATCH, DELETE, OPTIONS' }); res.end(); return; }
      if (req.method === 'GET' && path === '/health') return json(res, 200, { ok: true });
      if (req.method === 'GET' && PUBLIC_FILES.has(path)) {
        const [file, mime] = PUBLIC_FILES.get(path);
        const content = await readFile(join(ROOT, 'public', file));
        res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'no-cache' }); res.end(content); return;
      }
      if (!path.startsWith('/api/') && !path.startsWith('/v1/')) throw new HttpError(404, 'Not found.');
      if (!authorized(req)) throw new HttpError(401, 'Invalid or missing local access key.', 'authentication_error');
      if (req.method === 'GET' && path === '/api/status') {
        return json(res, 200, { version: '1.0.0', upstream: base, baseUrl: '/v1', settings: state.settings, accounts: state.accounts.map(safeAccount), stats, logs });
      }
      if (path === '/api/accounts') {
        if (req.method === 'GET') return json(res, 200, { accounts: state.accounts.map(safeAccount) });
        if (req.method === 'POST') {
          if (state.accounts.length >= 100) throw new HttpError(400, 'Maximum 100 accounts.');
          const input = validateAccount(await readJson(req), true);
          const account = { id: randomUUID(), ...input, enabled: input.enabled ?? true, createdAt: new Date().toISOString(), cooldownUntil: 0, lastError: null };
          state.accounts.push(account); save(); return json(res, 201, { account: safeAccount(account) });
        }
      }
      const match = path.match(/^\/api\/accounts\/([^/]+)(\/check)?$/);
      if (match) {
        const account = state.accounts.find((a) => a.id === match[1]);
        if (!account) throw new HttpError(404, 'Account not found.');
        if (match[2] && req.method === 'POST') {
          const controller = new AbortController(); activeControllers.add(controller);
          const timer = setTimeout(() => controller.abort(), state.settings.timeoutMs);
          const disconnect = () => { if (!res.writableFinished) controller.abort(); };
          res.on('close', disconnect);
          try {
            const response = await fetch(`${base}/models`, { headers: { Authorization: `Bearer ${account.key}` }, signal: controller.signal, redirect: 'error' });
            if (!response.ok) {
              await response.body?.cancel(); cooldown(account, response.status);
              return json(res, 200, { ok: false, status: response.status, models: 0, message: `Upstream HTTP ${response.status}` });
            }
            const body = await response.json();
            if (!Array.isArray(body.data)) throw new Error('Invalid model response');
            account.cooldownUntil = 0; account.lastError = null;
            return json(res, 200, { ok: true, status: response.status, models: body.data.length });
          } catch {
            cooldown(account);
            return json(res, 200, { ok: false, status: controller.signal.aborted ? 504 : 502, models: 0, message: 'Account check failed. Check the upstream URL and API key.' });
          } finally { clearTimeout(timer); activeControllers.delete(controller); res.off('close', disconnect); }
        }
        if (!match[2] && req.method === 'PATCH') {
          const input = validateAccount(await readJson(req));
          Object.assign(account, input);
          if (input.key || input.enabled === true) { account.cooldownUntil = 0; account.lastError = null; }
          save(); return json(res, 200, { account: safeAccount(account) });
        }
        if (!match[2] && req.method === 'DELETE') {
          state.accounts = state.accounts.filter((a) => a.id !== account.id); save(); return json(res, 200, { ok: true });
        }
      }
      if (path === '/api/settings' && req.method === 'PATCH') {
        state.settings = validSettings(await readJson(req), state.settings); save(); return json(res, 200, { settings: state.settings });
      }
      if (path === '/api/logs') {
        if (req.method === 'GET') return json(res, 200, { logs });
        if (req.method === 'DELETE') { logs.length = 0; return json(res, 200, { ok: true }); }
      }
      if (path === '/v1/models' && req.method === 'GET') return await forward(req, res, '/models', null);
      if (path === '/v1/chat/completions' && req.method === 'POST') {
        const payload = await readJson(req);
        if (typeof payload.model !== 'string' || !payload.model.trim() || payload.model.length > 256) throw new HttpError(400, 'model is required (maximum 256 characters).');
        if (!Array.isArray(payload.messages) || !payload.messages.length || payload.messages.some((m) => !m || typeof m !== 'object' || Array.isArray(m) || typeof m.role !== 'string')) throw new HttpError(400, 'messages must be a non-empty array of message objects with roles.');
        if (payload.stream !== undefined && typeof payload.stream !== 'boolean') throw new HttpError(400, 'stream must be a boolean.');
        return await forward(req, res, '/chat/completions', payload);
      }
      throw new HttpError(404, 'Not found.');
    } catch (error) {
      if (res.headersSent) res.destroy();
      else json(res, error.status || 500, { error: { message: error instanceof HttpError ? error.message : 'Internal server error. Check local configuration and storage permissions.', type: error.type || 'server_error' } });
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 15000;
  return {
    server, state,
    async close() {
      for (const controller of activeControllers) controller.abort();
      const closed = new Promise((resolveClose) => server.close(resolveClose));
      server.closeAllConnections(); await closed;
    },
  };
}

export async function start() {
  const envPath = join(ROOT, '.env');
  if (existsSync(envPath)) process.loadEnvFile(envPath);
  const port = Number(process.env.PORT || 8787);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid PORT.');
  const host = process.argv.includes('--preview') ? '0.0.0.0' : (process.env.HOST || '127.0.0.1');
  const app = await createApp();
  await new Promise((resolveListen, reject) => { app.server.once('error', reject); app.server.listen(port, host, resolveListen); });
  console.log(`fine2api is listening on http://${host}:${port}`);
  console.log('Use PROXY_KEY, or read .data/admin-key locally, to sign in. Keep this key private.');
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, async () => { await app.close(); process.exit(0); });
  return app;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  start().catch(() => { console.error('fine2api could not start. Check PORT, UPSTREAM and data directory permissions.'); process.exitCode = 1; });
}
