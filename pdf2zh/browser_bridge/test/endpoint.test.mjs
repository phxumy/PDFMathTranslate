import assert from 'node:assert/strict';
import { test } from 'node:test';
import { browserEndpoint, validateNativeEndpoint } from '../endpoint.mjs';

test('native discovery accepts loopback browser WebSocket without HTTP probing or profile copying', async () => {
  let calls = 0;
  const endpoint = await browserEndpoint('unused', { port: 9222, resolve: async config => {
    calls++;
    assert.equal(config.attachRunning, true);
    assert.deepEqual(config.remoteChrome, { host: '127.0.0.1', port: 9222 });
    return { host: '127.0.0.1', port: 9222, browserWSEndpoint: 'ws://127.0.0.1:9222/devtools/browser/native-fixture', profileRoot: 'not copied' };
  } });
  assert.equal(calls, 1);
  assert.equal(endpoint.browserWSEndpoint, 'ws://127.0.0.1:9222/devtools/browser/native-fixture');
  assert.equal('profileRoot' in endpoint, false);
});
test('native discovery fails closed for nonlocal, credentialed, wrong-port or wrong-target endpoints', () => {
  for (const browserWSEndpoint of ['ws://example.com:9222/devtools/browser/x', 'ws://127.0.0.1:9000/devtools/browser/x', 'ws://a:b@127.0.0.1:9222/devtools/browser/x', 'ws://127.0.0.1:9222/devtools/page/x', 'ws://127.0.0.1:9222/devtools/browser/x?token=x']) {
    assert.equal(validateNativeEndpoint({ host: '127.0.0.1', port: 9222, browserWSEndpoint }, 9222), null);
  }
});
test('disabled native debugging returns actionable guidance and cannot launch Chrome', async () => {
  await assert.rejects(browserEndpoint('unused', { port: 9222, resolve: async () => { throw new Error('not listening'); } }), error => {
    assert.equal(error.code, 'NATIVE_DEBUGGING_REQUIRED');
    assert.equal(error.status, 'needs_connection');
    assert.match(error.message, /chrome:\/\/inspect\/#remote-debugging/);
    return true;
  });
});
