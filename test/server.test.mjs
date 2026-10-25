import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { createApp } from '../server.mjs';

const proxyKey = 'test-local-key';
const fixtures = new Set();

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}

async function closeServer(server) {
  if (!server?.listening) return;
  server.closeAllConnections?.();
  await new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
}

async function fixture({ upstreamHandler, timeoutMs = 5000 } = {}) {
  const requests = [];
  const upstream = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks).toString('utf8');
    let body;
    try {
      body = rawBody ? JSON.parse(rawBody) : undefined;
    } catch {
      body = rawBody;
    }
    const record = {
      method: req.method,
      url: req.url,
      headers: req.headers,
      body,
      rawBody,
    };
    requests.push(record);

    if (upstreamHandler) {
      await upstreamHandler(req, res, record, requests);
      return;
    }
    res.setHeader('content-type', 'application/json');
    if (req.url === '/v1/models') {
      res.end(JSON.stringify({ data: [{ id: 'mock-model', object: 'model' }] }));
      return;
    }
    res.end(JSON.stringify({ id: 'mock-completion', choices: [{ message: { content: 'ok' } }] }));
  });

  const upstreamPort = await listen(upstream);
  const dataDir = await mkdtemp(join(tmpdir(), 'fine2api-test-'));
  const app = await createApp({
    dataDir,
    proxyKey,
    upstream: `http://127.0.0.1:${upstreamPort}/v1`,
    timeoutMs,
  });
  const appPort = await listen(app.server);
  const baseUrl = `http://127.0.0.1:${appPort}`;
  const entry = {
    requests,
    dataDir,
    app,
    baseUrl,
    async close() {
      await app.close();
      await closeServer(upstream);
      await rm(dataDir, { recursive: true, force: true });
      fixtures.delete(entry);
    },
  };
  fixtures.add(entry);
  return entry;
}

async function request(f, path, { admin = true, ...options } = {}) {
  const headers = new Headers(options.headers);
  if (admin) headers.set('authorization', `Bearer ${proxyKey}`);
  return fetch(`${f.baseUrl}${path}`, { ...options, headers });
}

async function json(response) {
  return response.json();
}

async function addAccount(f, name, key, enabled = true) {
  const response = await request(f, '/api/accounts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name, key, enabled }),
  });
  assert.equal(response.status, 201);
  const result = await json(response);
  assert.ok(result.account?.id);
  assert.equal(result.account.name, name);
  assert.equal(result.account.enabled, enabled);
  assert.ok(result.account.keyHint);
  assert.equal(Object.hasOwn(result.account, 'key'), false);
  return result.account;
}

before(() => {
  assert.ok(createApp, 'server.mjs must export createApp');
});

after(async () => {
  await Promise.all([...fixtures].map((entry) => entry.close()));
});

test('health is public while admin and proxy endpoints require the local bearer key', async (t) => {
  const f = await fixture();
  t.after(() => f.close());

  const health = await request(f, '/health', { admin: false });
  assert.equal(health.status, 200);

  for (const path of ['/api/status', '/api/accounts', '/v1/models', '/v1/chat/completions']) {
    const response = await request(f, path, {
      admin: false,
      method: path.endsWith('completions') ? 'POST' : 'GET',
      headers: path.endsWith('completions') ? { 'content-type': 'application/json' } : {},
      ...(path.endsWith('completions') ? { body: '{}' } : {}),
    });
    assert.ok(response.status === 401 || response.status === 403, `${path} unexpectedly returned ${response.status}`);
  }
  assert.equal((await request(f, '/api/status')).status, 200);
  assert.equal((await request(f, '/v1/models')).status, 200);
});

test('account CRUD persists credentials privately but masks them in every HTTP view', async (t) => {
  const f = await fixture();
  t.after(() => f.close());
  const accountKey = 'upstream-secret-account-key-7f3a';
  const account = await addAccount(f, 'primary', accountKey);

  let listed = await json(await request(f, '/api/accounts'));
  assert.equal(listed.accounts.length, 1);
  assert.equal(listed.accounts[0].id, account.id);
  assert.equal(Object.hasOwn(listed.accounts[0], 'key'), false);

  const patched = await request(f, `/api/accounts/${account.id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'renamed', key: 'rotated-secret-account-key', enabled: false }),
  });
  assert.equal(patched.status, 200);
  const patchResult = await json(patched);
  assert.equal(patchResult.account?.name, 'renamed');
  assert.equal(patchResult.account?.enabled, false);
  assert.equal(Object.hasOwn(patchResult.account ?? {}, 'key'), false);

  const statePath = join(f.dataDir, 'state.json');
  const state = await readFile(statePath, 'utf8');
  assert.match(state, /rotated-secret-account-key/);
  assert.equal((await stat(statePath)).mode & 0o777, 0o600);

  const statusResponse = await request(f, '/api/status');
  assert.equal(statusResponse.status, 200);
  const status = await json(statusResponse);
  assert.ok(status.settings);
  assert.ok(status.accounts);
  assert.ok(status.stats);
  assert.ok(status.logs);
  assert.equal(Object.hasOwn(status, 'proxyKey'), false);
  const statusText = JSON.stringify(status);
  assert.equal(statusText.includes(proxyKey), false);
  assert.equal(statusText.includes(accountKey), false);
  assert.equal(statusText.includes('rotated-secret-account-key'), false);

  const deleted = await request(f, `/api/accounts/${account.id}`, { method: 'DELETE' });
  assert.equal(deleted.status, 200);
  assert.deepEqual(await json(deleted), { ok: true });
  assert.deepEqual((await json(await request(f, '/api/accounts'))).accounts, []);
});

test('chat completions strip only configured root sampling parameters and preserve nested payloads', async (t) => {
  const f = await fixture();
  t.after(() => f.close());
  await addAccount(f, 'one', 'upstream-key-one');
  const input = {
    model: 'mock-model',
    messages: [{ role: 'user', content: [{ type: 'text', text: 'hello' }, { type: 'image_url', image_url: { url: 'https://example.invalid/image.png' } }] }],
    tools: [{ type: 'function', function: { name: 'lookup', parameters: { type: 'object', properties: { q: { type: 'string' } } } } }],
    tool_choice: 'auto',
    presence_penalty: 0.5,
    frequency_penalty: 0.25,
    top_p: 0.8,
    top_k: 40,
    temperature: 0.6,
    stream: false,
    metadata: { preserve: true },
  };

  const response = await request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  });
  assert.equal(response.status, 200);
  assert.equal(f.requests[0].headers.authorization, 'Bearer upstream-key-one');
  const sent = f.requests[0].body;
  for (const key of ['presence_penalty', 'frequency_penalty', 'top_p', 'top_k', 'temperature']) {
    assert.equal(Object.hasOwn(sent, key), false, `${key} should be stripped`);
  }
  for (const key of ['model', 'messages', 'tools', 'tool_choice', 'stream', 'metadata']) {
    assert.deepEqual(sent[key], input[key], `${key} should be preserved`);
  }

  const settings = await request(f, '/api/settings', {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ stripParameters: false }),
  });
  assert.equal(settings.status, 200);
  const keepSampling = await request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(input),
  });
  assert.equal(keepSampling.status, 200);
  assert.deepEqual(f.requests[1].body, input);
});

test('GET /v1/models and account check use the configured upstream and account credential', async (t) => {
  const f = await fixture();
  t.after(() => f.close());
  const account = await addAccount(f, 'model account', 'model-check-secret');

  const models = await request(f, '/v1/models');
  assert.equal(models.status, 200);
  assert.deepEqual((await json(models)).data.map((model) => model.id), ['mock-model']);
  assert.equal(f.requests[0].url, '/v1/models');

  const check = await request(f, `/api/accounts/${account.id}/check`, { method: 'POST' });
  assert.equal(check.status, 200);
  assert.deepEqual(await json(check), { ok: true, status: 200, models: 1 });
  assert.equal(f.requests[1].url, '/v1/models');
  assert.equal(f.requests[1].headers.authorization, 'Bearer model-check-secret');
});

test('SSE responses preserve the upstream content type and streamed bytes', async (t) => {
  const bytes = Buffer.from('data: {"choices":[{"delta":{"content":"你好"}}]}\r\n\r\ndata: [DONE]\r\n\r\n');
  const f = await fixture({ upstreamHandler: async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' });
    res.end(bytes);
  } });
  t.after(() => f.close());
  await addAccount(f, 'stream', 'stream-secret');

  const response = await request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'mock-model', stream: true, messages: [] }),
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'text/event-stream; charset=utf-8');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
});

for (const status of [401, 403, 429, 500, 502, 503]) {
  test(`pre-stream upstream ${status} fails over to the next account`, async (t) => {
    const f = await fixture({ upstreamHandler: async (_req, res, record) => {
      res.setHeader('content-type', 'application/json');
      if (record.headers.authorization === 'Bearer first-key') {
        res.writeHead(status);
        res.end(JSON.stringify({ error: `mock ${status}` }));
      } else {
        res.end(JSON.stringify({ ok: true }));
      }
    } });
    t.after(() => f.close());
    await addAccount(f, 'first', 'first-key');
    await addAccount(f, 'second', 'second-key');

    const response = await request(f, '/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: 'mock-model', messages: [] }),
    });
    assert.equal(response.status, 200);
    assert.deepEqual(f.requests.map((record) => record.headers.authorization), ['Bearer first-key', 'Bearer second-key']);
  });
}

test('a network error before response fails over, while an error after stream bytes does not replay', async (t) => {
  let streamStarted;
  const started = new Promise((resolve) => { streamStarted = resolve; });
  const f = await fixture({ upstreamHandler: async (req, res, record) => {
    if (record.headers.authorization === 'Bearer network-key') {
      req.socket.destroy();
      return;
    }
    if (record.headers.authorization === 'Bearer stream-error-key') {
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.write('data: first\n\n');
      streamStarted();
      setTimeout(() => res.destroy(), 40);
      return;
    }
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ ok: true }));
  } });
  t.after(() => f.close());
  await addAccount(f, 'network-failure', 'network-key');
  await addAccount(f, 'network-success', 'network-success-key');

  const failover = await request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'mock-model', messages: [] }),
  });
  assert.equal(failover.status, 200);
  assert.deepEqual(f.requests.slice(0, 2).map((record) => record.headers.authorization), ['Bearer network-key', 'Bearer network-success-key']);

  await request(f, '/api/accounts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'stream-failure', key: 'stream-error-key' }),
  }).then(async (response) => assert.equal(response.status, 201));
  await addAccount(f, 'stream-fallback', 'stream-fallback-key');
  const streamRequest = request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'mock-model', stream: true, messages: [] }),
  });
  const response = await streamRequest;
  assert.equal(response.status, 200);
  const reader = response.body.getReader();
  const firstChunk = await reader.read();
  assert.equal(firstChunk.done, false);
  assert.match(new TextDecoder().decode(firstChunk.value), /data: first/);
  await started;
  await reader.cancel().catch(() => {});
  await new Promise((resolve) => setTimeout(resolve, 60));
  const streamCalls = f.requests.filter((record) => record.headers.authorization === 'Bearer stream-error-key' || record.headers.authorization === 'Bearer stream-fallback-key');
  assert.deepEqual(streamCalls.map((record) => record.headers.authorization), ['Bearer stream-error-key']);
});

test('no enabled account returns 503; unsupported upstream status is not retried', async (t) => {
  const f = await fixture({ upstreamHandler: async (_req, res) => {
    res.writeHead(400, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'bad request' }));
  } });
  t.after(() => f.close());
  const noAccounts = await request(f, '/v1/models');
  assert.equal(noAccounts.status, 503);

  await addAccount(f, 'one', 'only-key');
  await addAccount(f, 'disabled', 'disabled-key', false);
  const response = await request(f, '/v1/models');
  assert.equal(response.status, 400);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].headers.authorization, 'Bearer only-key');
});

test('invalid JSON, invalid fields, and bodies over 2 MiB are rejected', async (t) => {
  const f = await fixture();
  t.after(() => f.close());
  const invalidJson = await request(f, '/api/accounts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{bad json',
  });
  assert.equal(invalidJson.status, 400);

  const invalidAccount = await request(f, '/api/accounts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: '', key: '' }),
  });
  assert.equal(invalidAccount.status, 400);

  const invalidSettings = await request(f, '/api/settings', {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ rotation: 'random', timeoutMs: 9999999, stripParameters: 'yes' }),
  });
  assert.equal(invalidSettings.status, 400);

  const oversized = await request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: `{"padding":"${'x'.repeat(2 * 1024 * 1024)}"}`,
  });
  assert.equal(oversized.status, 413);
  assert.equal(f.requests.length, 0);
});

test('status and logs never retain credentials, raw upstream errors, or request payloads', async (t) => {
  const f = await fixture({ upstreamHandler: async (_req, res) => {
    res.writeHead(500, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'raw-upstream-error-marker' }));
  } });
  t.after(() => f.close());
  await addAccount(f, 'secret account', 'account-key-marker-91');

  const response = await request(f, '/v1/chat/completions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'mock-model', messages: [{ role: 'user', content: 'payload-secret-marker' }] }),
  });
  assert.equal(response.status, 500);

  const status = await json(await request(f, '/api/status'));
  const serialized = JSON.stringify(status);
  for (const secret of [proxyKey, 'account-key-marker-91', 'raw-upstream-error-marker', 'payload-secret-marker']) {
    assert.equal(serialized.includes(secret), false, `${secret} leaked into status or logs`);
  }
});

