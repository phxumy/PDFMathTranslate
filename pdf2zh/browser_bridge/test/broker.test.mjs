import assert from 'node:assert/strict';
import { once } from 'node:events';
import path from 'node:path';
import test from 'node:test';
import { startWorkerServer } from '../broker.mjs';

const stateDir = path.resolve('test-broker-state');

async function fixture(t, run, options) {
  const { server, worker } = await startWorkerServer(stateDir, run, options);
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  const base = `http://127.0.0.1:${worker.port}`;
  const headers = {
    authorization: `Bearer ${worker.token}`,
    'content-type': 'application/json',
  };
  const request = (route, init = {}) => fetch(base + route, { headers, ...init });
  const operation = (payload = {}, init = {}) => request('/operation', {
    method: 'POST', body: JSON.stringify({ state_dir: stateDir, ...payload }), ...init,
  });
  return { server, worker, base, headers, request, operation };
}

async function waitUntil(check) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('Timed out waiting for the local worker test condition');
}

test('worker listens on loopback and rejects missing/wrong tokens and web origins', async t => {
  let calls = 0;
  const f = await fixture(t, async () => { calls++; return { ok: true }; });
  assert.equal(f.server.address().address, '127.0.0.1');
  assert.ok(f.worker.token.length >= 40);

  for (const headers of [
    {},
    { authorization: 'Bearer incorrect' },
    { ...f.headers, origin: 'https://example.invalid' },
    { ...f.headers, origin: 'null' },
  ]) {
    const response = await fetch(f.base + '/operation', {
      method: 'POST', headers, body: JSON.stringify({ state_dir: stateDir }),
    });
    assert.equal(response.status, 403);
    assert.equal((await response.json()).ok, false);
  }
  assert.equal(calls, 0);
  const health = await (await f.request('/health')).json();
  assert.equal(health.ok, true);
  assert.equal(health.busy, false);
  assert.equal(health.version, 'test');
});

test('malformed operations and a different state directory never reach the handler', async t => {
  let calls = 0;
  const f = await fixture(t, async () => { calls++; return { ok: true }; });
  const responses = [
    await f.request('/operation'),
    await f.request('/unknown', { method: 'POST', body: '{}' }),
    await f.request('/operation', { method: 'POST', body: '{invalid' }),
    await f.request('/operation', { method: 'POST', body: '{}' }),
    await f.operation({ state_dir: path.join(stateDir, 'different') }),
    await f.operation({}, { headers: { ...f.headers, 'content-type': 'text/plain' } }),
  ];
  for (const response of responses) {
    assert.equal(response.status, 400);
    assert.equal((await response.json()).ok, false);
  }
  assert.equal(calls, 0);
});

test('a canceled client leaves its operation running once and prevents overlapping work', async t => {
  let calls = 0;
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const f = await fixture(t, async () => {
    calls++;
    await gate;
    return { ok: true, status: 'complete' };
  });
  try {
    const cancel = new AbortController();
    const first = f.operation({ op: 'translate' }, { signal: cancel.signal }).catch(() => null);
    await waitUntil(() => calls === 1);
    cancel.abort();
    await first;

    const health = await (await f.request('/health')).json();
    assert.equal(health.busy, true);
    const second = await (await f.operation({ op: 'recover' })).json();
    assert.equal(second.ok, false);
    assert.equal(second.status, 'busy');
    assert.equal(second.not_submitted, false);
    assert.equal(calls, 1);

    const shutdown = await (await f.request('/shutdown', { method: 'POST', body: '{}' })).json();
    assert.equal(shutdown.ok, false);
    assert.equal(shutdown.busy, true);
    assert.equal(f.server.listening, true);

    finish();
    await waitUntil(async () => !(await (await f.request('/health')).json()).busy);
    assert.equal(calls, 1);
    const recovered = await (await f.operation({ op: 'recover' })).json();
    assert.equal(recovered.status, 'complete');
    assert.equal(calls, 2);
  } finally {
    finish();
  }
});

test('handler failure preserves an ambiguous submission and releases the busy state', async t => {
  let calls = 0;
  const f = await fixture(t, async () => {
    calls++;
    throw Object.assign(new Error('connection was lost'), { code: 'CONNECTION_LOST' });
  });
  const result = await (await f.operation({ op: 'translate' })).json();
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'CONNECTION_LOST');
  assert.equal(result.not_submitted, false);
  assert.equal(calls, 1);
  assert.equal((await (await f.request('/health')).json()).busy, false);
});

test('an idle worker accepts shutdown', async t => {
  const f = await fixture(t, async () => ({ ok: true }));
  const closed = once(f.server, 'close');
  const result = await (await f.request('/shutdown', { method: 'POST', body: '{}' })).json();
  assert.deepEqual(result, { ok: true, busy: false });
  await closed;
  assert.equal(f.server.listening, false);
});

test('idle expiry waits for an active operation to complete', async t => {
  let calls = 0;
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const f = await fixture(t, async () => { calls++; await gate; return { ok: true }; }, { idleMs: 60 });
  try {
    const operation = f.operation();
    await waitUntil(() => calls === 1);
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(f.server.listening, true);
    finish();
    assert.equal((await (await operation).json()).ok, true);
    await waitUntil(() => !f.server.listening);
    assert.equal(calls, 1);
  } finally {
    finish();
  }
});
