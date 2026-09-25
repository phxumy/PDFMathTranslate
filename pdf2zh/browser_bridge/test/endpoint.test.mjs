import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import { browserEndpoint } from '../endpoint.mjs';

test('stale Chrome port file recovers only through owned-process discovery and caches socket identity', async () => {
  const state = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf2zh-endpoint-'));
  const profile = path.join(state, 'chrome-profile');
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ Browser: 'Chrome/153.0', webSocketDebuggerUrl: `ws://127.0.0.1:${server.address().port}/devtools/browser/owned` }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await fs.mkdir(profile);
    const port = server.address().port;
    await fs.writeFile(path.join(profile, 'DevToolsActivePort'), `${port}\n/devtools/browser/old-instance`);
    assert.equal(await browserEndpoint(state, { discover: async () => [] }), null);
    let discoveries = 0;
    const discover = async candidate => { assert.equal(candidate, profile); discoveries++; return [port]; };
    assert.equal((await browserEndpoint(state, { discover })).port, port);
    assert.equal((await browserEndpoint(state, { discover })).socketPath, '/devtools/browser/owned');
    assert.equal(discoveries, 1);
    await fs.writeFile(path.join(state, 'browser-endpoint.json'), JSON.stringify({ port, socketPath: '/devtools/browser/wrong' }));
    assert.equal(await browserEndpoint(state, { discover: async () => [] }), null);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await fs.rm(state, { recursive: true, force: true });
  }
});
