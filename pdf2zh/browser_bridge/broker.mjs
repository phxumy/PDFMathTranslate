// One local worker keeps Chrome's user-approved Oracle connection alive across UI
// operations and PDF batches. Clients may exit without cancelling a submitted turn.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const alive = pid => { try { if (!Number.isInteger(pid) || pid <= 0) return false; process.kill(pid, 0); return true; } catch { return false; } };
const failure = message => Object.assign(new Error(message), { code: 'CONNECTION_WORKER_UNAVAILABLE', not_submitted: false });
const statePath = directory => path.join(directory, 'connection-worker.json');
const samePath = (a, b) => process.platform === 'win32' ? path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase() : path.resolve(a) === path.resolve(b);

export async function workerVersion() {
  const hash = createHash('sha256');
  for (const name of ['broker.mjs', 'bridge.mjs', 'shell-page.mjs', 'endpoint.mjs', 'native-ui.mjs', 'ui.mjs', 'package-lock.json']) {
    hash.update(name);
    hash.update(await fs.readFile(path.join(HERE, name)).catch(() => Buffer.from('missing')));
  }
  return hash.digest('hex').slice(0, 24);
}
async function readWorker(directory) {
  try {
    const data = JSON.parse(await fs.readFile(statePath(directory), 'utf8'));
    if (Number.isInteger(data.port) && data.port > 0 && data.port < 65536 && typeof data.token === 'string' && data.token.length >= 32 && alive(data.pid)) return data;
  } catch { /* Missing or stale worker metadata. */ }
  return null;
}
async function rpc(worker, route, payload, timeout = 2000) {
  return new Promise((resolve, reject) => {
    const content = payload === undefined ? null : Buffer.from(JSON.stringify(payload));
    const request = http.request({ hostname: '127.0.0.1', port: worker.port, path: route,
      method: content ? 'POST' : 'GET', headers: { authorization: `Bearer ${worker.token}`,
        ...(content ? { 'content-type': 'application/json', 'content-length': content.length } : {}) } }, response => {
      const chunks = []; let length = 0;
      response.on('data', chunk => {
        length += chunk.length;
        if (length > 16 * 1024 * 1024) response.destroy(new Error('Worker response too large'));
        else chunks.push(chunk);
      });
      response.on('error', reject);
      response.on('end', () => {
        clearTimeout(timer);
        try {
          if (response.statusCode !== 200) throw new Error('Worker authentication or protocol failed');
          resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
        } catch (error) { reject(error); }
      });
    });
    const timer = setTimeout(() => request.destroy(new Error('Worker request timed out')), timeout);
    request.on('error', error => { clearTimeout(timer); reject(error); });
    request.end(content);
  });
}

export async function startWorkerServer(directory, run, { version = 'test', idleMs = 30 * 60_000 } = {}) {
  const token = randomBytes(32).toString('base64url');
  let busy = false;
  let lastUse = Date.now();
  const send = (response, body, status = 200) => {
    response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
    response.end(JSON.stringify(body));
  };
  const server = http.createServer(async (request, response) => {
    const provided = Buffer.from(request.headers.authorization || '');
    const expected = Buffer.from(`Bearer ${token}`);
    if (request.headers.origin || provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
      send(response, { ok: false }, 403); return;
    }
    lastUse = Date.now();
    if (request.method === 'GET' && request.url === '/health') {
      send(response, { ok: true, version, busy }); return;
    }
    if (request.method === 'POST' && request.url === '/shutdown') {
      // Drain the request before closing its connection. On Windows, closing
      // with unread POST bytes can reset the socket before the reply is read.
      let received = 0;
      for await (const chunk of request) {
        received += chunk.length;
        if (received > 1024) { send(response, { ok: false }, 400); return; }
      }
      if (!busy) {
        response.setHeader('connection', 'close');
        response.once('finish', () => server.close());
      }
      send(response, { ok: !busy, busy });
      return;
    }
    if (request.method !== 'POST' || request.url !== '/operation' || request.headers['content-type'] !== 'application/json') {
      send(response, { ok: false }, 400); return;
    }
    try {
      const chunks = []; let bytes = 0;
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > 1024 * 1024) throw new Error('Request too large');
        chunks.push(chunk);
      }
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!payload || typeof payload.state_dir !== 'string' || !samePath(payload.state_dir, directory)) throw new Error('Invalid state directory');
      if (busy) {
        send(response, { ok: false, status: 'busy', error_code: 'BROWSER_BUSY', not_submitted: false,
          message: 'ChatGPT 正在处理上一项操作。已发送的批次继续收取回答，请稍后恢复原文件。' });
        return;
      }
      busy = true;
      try { send(response, await run(payload)); }
      catch (error) {
        send(response, { ok: false, status: 'paused', error_code: error.code || 'BROWSER_ERROR', not_submitted: false,
          message: error.message || '浏览器连接中断，请重新连接后恢复原任务。' });
      } finally { busy = false; lastUse = Date.now(); }
    } catch {
      send(response, { ok: false, message: 'Invalid local operation.' }, 400);
    }
  });
  server.requestTimeout = 0;
  server.headersTimeout = 30_000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const worker = { pid: process.pid, port: server.address().port, token, version };
  const idle = setInterval(() => { if (!busy && Date.now() - lastUse > idleMs) server.close(); }, Math.min(60_000, idleMs));
  idle.unref();
  server.on('close', () => clearInterval(idle));
  return { server, worker };
}

async function ensureWorker(directory, version) {
  const lockPath = path.join(directory, 'connection-worker-start.lock');
  const lockTemporary = `${lockPath}.${process.pid}.${randomBytes(6).toString('hex')}.tmp`;
  await fs.writeFile(lockTemporary, String(process.pid), { flag: 'wx', mode: 0o600 });
  try { await fs.link(lockTemporary, lockPath); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const owner = Number(await fs.readFile(lockPath, 'utf8').catch(() => ''));
    if (owner && !alive(owner)) { await fs.unlink(lockPath).catch(() => {}); return ensureWorker(directory, version); }
    for (let i = 0; i < 75; i++) {
      await pause(200);
      const current = await readWorker(directory);
      if (current?.version === version && (await rpc(current, '/health').catch(() => null))?.ok) return current;
    }
    throw failure('浏览器连接组件仍在启动，请稍后重试。');
  } finally { await fs.unlink(lockTemporary).catch(() => {}); }
  try {
    // Recheck only after owning the startup lock; another client may have just
    // created the worker while we were waiting.
    const existing = await readWorker(directory);
    if (existing) {
      const health = await rpc(existing, '/health').catch(() => null);
      if (!health) throw failure('已有浏览器连接组件暂时没有响应；为避免重复执行，请稍后恢复原任务。');
      if (health.version === version) return existing;
      if (health.busy) throw failure('浏览器连接组件有任务正在执行，请等待完成后再使用更新版本。');
      const stopped = await rpc(existing, '/shutdown', {}).catch(() => null);
      if (!stopped?.ok) throw failure('连接组件仍在执行任务，暂时无法切换到更新版本。');
      for (let i = 0; i < 50 && alive(existing.pid); i++) await pause(100);
      if (alive(existing.pid)) throw failure('旧连接组件尚未退出，请稍后重新连接。');
    }
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--serve', directory], {
      detached: true, windowsHide: true, stdio: 'ignore', cwd: HERE,
    });
    await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
    child.unref();
    for (let i = 0; i < 100; i++) {
      await pause(150);
      const current = await readWorker(directory);
      if (current?.pid === child.pid && current.version === version && (await rpc(current, '/health').catch(() => null))?.ok) return current;
    }
    throw failure('无法启动浏览器连接组件，请重新打开软件。');
  } finally { await fs.unlink(lockPath).catch(() => {}); }
}

export async function invokeBroker(request) {
  if (typeof request?.state_dir !== 'string' || !path.isAbsolute(request.state_dir)) throw failure('Invalid state directory');
  await fs.mkdir(request.state_dir, { recursive: true });
  const worker = await ensureWorker(request.state_dir, await workerVersion());
  const timeout = Math.max(240_000, ((request.timeout_seconds || 900) + 45) * 1000);
  // Never replay an operation after a transport failure: it may already have sent.
  try { return await rpc(worker, '/operation', request, timeout); }
  catch { throw failure('浏览器连接组件的响应中断；已发送任务可能仍在运行，请重新连接后恢复同一文件。'); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url) && process.argv[2] === '--serve') {
  const directory = path.resolve(process.argv[3]);
  const { run } = await import('./bridge.mjs');
  const { server, worker } = await startWorkerServer(directory, run, { version: await workerVersion() });
  const temporary = `${statePath(directory)}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(worker), { mode: 0o600 });
  await fs.rename(temporary, statePath(directory));
  server.once('close', () => { setTimeout(() => process.exit(0), 50); });
}
