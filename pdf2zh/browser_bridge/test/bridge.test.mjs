import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { createBridge, connectOwnedPage, OWNED_PAGE_NOTRANSLATE_SCRIPT, installOwnedPageTranslationHints, translatedHomeState } from '../bridge.mjs';
import { browserPromptFingerprint } from '../node_modules/@steipete/oracle/dist/src/browser/promptFingerprint.js';

async function temporary(fn) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf2zh-native-test-'));
  try { await fn(directory); }
  finally { await fs.rm(directory, { recursive: true, force: true }); }
}
function fixture() {
  const calls = { discover: 0, attach: [], submit: 0, close: [], navigate: [], catalog: 0, scoped: [], hints: [], reloads: [] };
  const tabs = new Map([['personal-tab', { messageId: 'private', text: 'DO NOT READ', url: 'https://chatgpt.com/c/personal' }]]);
  let next = 0;
  const deps = {
    discover: async () => { calls.discover++; return { host: '127.0.0.1', port: 9222, browserWSEndpoint: 'ws://127.0.0.1:9222/devtools/browser/fixture' }; },
    attach: async (_host, _port, _logger, options) => {
      calls.attach.push(options);
      const id = options.targetId || `owned-${++next}`;
      if (options.targetId && !tabs.has(id)) throw new Error('No target with given id found');
      const state = tabs.get(id) || { url: 'about:blank', count: 0 };
      tabs.set(id, state);
      const client = { Runtime: { state, evaluate: async () => ({ result: { value: 0 } }) }, Input: {}, Page: { reload: async () => calls.reloads.push(id) }, on: () => {},
        _ws: { readyState: 1, on: () => {} },
        Target: { createTarget: async options => { assert.deepEqual(options, { url: 'about:blank', background: true }); const targetId = `owned-${++next}`; tabs.set(targetId, { url: 'about:blank', count: 0 }); return { targetId }; } } };
      return { client, targetId: id, close: async options => { calls.close.push({ id, options }); } };
    },
    ui: {
      createNativePage: client => ({ client, goto: async url => { assert.equal(client.Runtime.state.hintsInstalled, true, 'document-start hints must precede the first navigation'); calls.navigate.push(url); client.Runtime.state.url = url; } }),
      prepareLoginPage: async () => ({ ok: true, status: 'ready' }),
      inspectPage: async () => ({ ready: true }), connectionStatus: () => ({ ok: true, status: 'ready' }),
      ensureChat: async () => {},
      catalog: async (_page, model, effort) => { calls.catalog++; return { model: model || 'actual-account-model', effort: effort || 'actual-effort', models: [], efforts: [] }; },
    },
    promptReady: async () => {}, readUserIds: async runtime => runtime.state.messageId ? [runtime.state.messageId] : [],
    readFingerprint: async runtime => runtime.state.messageId ? browserPromptFingerprint(runtime.state.text, runtime.state.messageId) : undefined,
    userTurn: async runtime => ({ ...runtime.state }),
    submit: async (options, prompt) => {
      assert.equal(options.page, undefined, 'submission cannot bring the browser tab to the foreground');
      calls.submit++;
      Object.assign(options.runtime.state, { messageId: 'submitted-user', text: prompt, count: 1, url: 'https://chatgpt.com/c/our-request' });
      await options.onPromptSubmitted();
    },
    waitAnswer: async () => ({ text: '{"translation":"离线测试译文"}' }),
    installTranslationHints: async client => { calls.hints.push(client.Runtime.state); client.Runtime.state.hintsInstalled = true; },
    translatedHomeState: async runtime => ({ emptyHome: runtime.state.url === 'https://chatgpt.com/' && !runtime.state.messageId && !runtime.state.draft, translated: !!runtime.state.translated }),
  };
  deps.scopedAttach = async (_anchor, targetId) => {
    calls.scoped.push(targetId);
    const state = tabs.get(targetId);
    if (!state) throw new Error('No target with given id found');
    return { targetId, client: { Runtime: { state, evaluate: async () => ({ result: { value: 0 } }) }, Input: {}, Page: { reload: async () => calls.reloads.push(targetId) } }, close: async options => { calls.close.push({ id: targetId, options }); } };
  };
  return { deps, calls, tabs };
}
async function request(directory, op = 'translate', suffix = '') {
  const request_dir = path.join(directory, `request${suffix}`);
  await fs.mkdir(request_dir, { recursive: true });
  const prompt_file = path.join(request_dir, 'prompt.txt');
  await fs.writeFile(prompt_file, 'Fixture text, never sent to a real browser.');
  return { op, state_dir: directory, request_dir, prompt_file, output_file: path.join(request_dir, 'answer.txt'), timeout_seconds: 1 };
}
test('status never discovers, authorizes, creates tabs or writes login state', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  const result = await bridge.run({ op: 'status', state_dir: directory });
  assert.equal(result.status, 'needs_connection');
  assert.equal(f.calls.discover, 0); assert.equal(f.calls.attach.length, 0);
  assert.deepEqual(await fs.readdir(directory), []);
}));
test('explicit login and subsequent operations use only software-owned targets', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  assert.equal((await bridge.run({ op: 'login', state_dir: directory })).status, 'ready');
  for (const op of ['status', 'catalog', 'login']) assert.equal((await bridge.run({ op, state_dir: directory })).ok, true);
  assert.equal(f.calls.discover, 1);
  assert.equal(f.calls.attach.filter(x => !x.targetId).length, 1);
  assert.ok(f.calls.attach.every(x => !x.targetId || x.targetId === 'owned-1'));
  assert.equal(f.calls.attach[0].approvalWaitMs, 180000);
  assert.ok(f.calls.close.every(x => x.options.preserveTarget));
}));
test('broker restart reuses exact persisted owned target and never adopts personal home/auth/chat', async () => temporary(async directory => {
  const f = fixture();
  await createBridge(f.deps).run({ op: 'login', state_dir: directory });
  const second = createBridge(f.deps);
  assert.equal((await second.run({ op: 'login', state_dir: directory })).ok, true);
  assert.equal(f.calls.attach.filter(x => !x.targetId).length, 1);
  assert.equal(f.calls.attach.at(-1).targetId, 'owned-1');
  f.tabs.delete('owned-1');
  assert.equal((await second.run({ op: 'status', state_dir: directory })).status, 'needs_login');
  assert.equal(f.calls.attach.filter(x => !x.targetId).length, 1, 'status cannot create a replacement');
  assert.equal((await second.run({ op: 'login', state_dir: directory })).ok, true);
  assert.ok(f.tabs.has('owned-2'));
}));
test('one translation persists before submit, selects actual model and completes without duplicate submission', async () => temporary(async directory => {
  const f = fixture(); const req = await request(directory);
  const submit = f.deps.submit;
  f.deps.submit = async (...args) => {
    const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
    assert.equal(journal.dispatch_started, true); assert.equal(journal.prompt_submitted, false);
    await submit(...args);
  };
  const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  const result = await bridge.run(req);
  assert.equal(result.ok, true); assert.equal(result.model, 'actual-account-model');
  assert.equal(result.conversation_url, 'https://chatgpt.com/c/our-request');
  assert.equal(f.calls.submit, 1);
  assert.equal((await bridge.run(req)).answer, result.answer);
  assert.equal((await bridge.run({ ...req, op: 'recover' })).answer, result.answer);
  assert.equal(f.calls.submit, 1);
  assert.ok(f.calls.attach.every(x => x.targetId !== 'personal-tab'));
}));
test('cancelled caller does not duplicate pending work and a different batch is blocked', async () => temporary(async directory => {
  const f = fixture(); let release; let entered;
  const capturing = new Promise(resolve => { entered = resolve; });
  f.deps.waitAnswer = async () => { entered(); return new Promise(resolve => { release = () => resolve({ text: 'done' }); }); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); const first = bridge.run(req); await capturing;
  const same = bridge.run({ ...req, op: 'recover' });
  const other = await bridge.run(await request(directory, 'translate', '-other'));
  assert.equal(other.error_code, 'BROWSER_BUSY'); assert.equal(other.not_submitted, true);
  release(); assert.equal((await first).answer, 'done'); assert.equal((await same).answer, 'done');
  assert.equal(f.calls.submit, 1);
}));
test('failed capture resumes only the same verified user turn without resubmission', async () => temporary(async directory => {
  const f = fixture(); let attempts = 0;
  f.deps.waitAnswer = async () => { if (++attempts === 1) throw new Error('fixture disconnected wait'); return { text: 'recovered' }; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); const failed = await bridge.run(req);
  assert.equal(failed.not_submitted, false);
  const recovered = await bridge.run({ ...req, op: 'recover' });
  assert.equal(recovered.answer, 'recovered'); assert.equal(f.calls.submit, 1);
}));
test('recovery waits for the saved message and later Chat controls before reading an answer', async () => temporary(async directory => {
  const f = fixture(); let captures = 0; let clock = 0; let recovering = false; let modeChecks = 0;
  let savedTurn;
  f.deps.waitAnswer = async () => {
    captures++;
    if (captures === 1) throw new Error('fixture interrupted capture');
    assert.ok(modeChecks >= 2, 'answer cannot be read before delayed Chat controls hydrate');
    return { text: 'loaded original answer' };
  };
  f.deps.ui.ensureChat = async () => {
    if (recovering && ++modeChecks < 2) throw Object.assign(new Error('sidebar is still loading'), { code: 'CHAT_MODE_UNVERIFIED' });
  };
  f.deps.recoveryNow = () => clock;
  f.deps.recoveryPause = async ms => {
    clock += ms;
    assert.equal(captures, 1, 'waiting for hydration must not read assistant content');
    if (clock === 500) Object.assign(f.tabs.get('owned-3'), savedTurn);
  };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await bridge.run(req);
  savedTurn = { ...f.tabs.get('owned-2') }; f.tabs.delete('owned-2'); recovering = true;
  const recovered = await bridge.run({ ...req, op: 'recover' });
  assert.equal(recovered.answer, 'loaded original answer');
  assert.equal(clock, 750); assert.equal(f.calls.submit, 1); assert.equal(captures, 2);
  assert.equal(f.calls.navigate.at(-1), savedTurn.url);
}));
test('recovery hydration timeout is bounded and never resubmits or reads another answer', async () => temporary(async directory => {
  const f = fixture(); let captures = 0; let clock = 0; let pauses = 0;
  f.deps.waitAnswer = async () => { captures++; throw new Error('interrupted capture'); };
  f.deps.recoveryNow = () => clock;
  f.deps.recoveryPause = async ms => { clock += ms; pauses++; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await bridge.run(req); f.tabs.delete('owned-2');
  const recovered = await bridge.run({ ...req, op: 'recover' });
  assert.equal(recovered.error_code, 'RECOVERY_NOT_READY'); assert.equal(recovered.not_submitted, false);
  assert.equal(clock, 1000); assert.equal(pauses, 4);
  assert.equal(f.calls.submit, 1); assert.equal(captures, 1);
}));
test('recovery rejects a different conversation before reading its answer or changing saved URL', async () => temporary(async directory => {
  const f = fixture(); let captures = 0;
  f.deps.waitAnswer = async () => { captures++; throw new Error('interrupted capture'); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await bridge.run(req);
  f.tabs.get('owned-2').url = 'https://chatgpt.com/c/different-conversation';
  const recovered = await bridge.run({ ...req, op: 'recover' });
  assert.equal(recovered.error_code, 'RECOVERY_IDENTITY_UNVERIFIED'); assert.equal(recovered.not_submitted, false);
  assert.equal(captures, 1); assert.equal(f.calls.submit, 1);
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.conversation_url, 'https://chatgpt.com/c/our-request');
}));
test('changed page identity is rejected and never accepted as the old answer', async () => temporary(async directory => {
  const f = fixture(); f.deps.waitAnswer = async () => { throw new Error('wait failed'); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await bridge.run(req);
  f.tabs.get('owned-2').messageId = 'some-other-user-turn';
  const recovered = await bridge.run({ ...req, op: 'recover' });
  assert.equal(recovered.error_code, 'RECOVERY_IDENTITY_UNVERIFIED');
  assert.equal(recovered.not_submitted, false); assert.equal(f.calls.submit, 1);
}));
test('first rendered user message must equal this batch prompt before binding a fingerprint', async () => temporary(async directory => {
  const f = fixture();
  f.deps.submit = async options => {
    f.calls.submit++;
    Object.assign(options.runtime.state, { count: 1, text: 'an unrelated user prompt', messageId: 'other', url: 'https://chatgpt.com/c/other' });
    await options.onPromptSubmitted();
  };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); const response = await bridge.run(req);
  assert.equal(response.error_code, 'RECOVERY_IDENTITY_UNVERIFIED'); assert.equal(response.not_submitted, false);
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.submitted_prompt_hash, undefined);
  assert.equal((await bridge.run({ ...req, op: 'recover' })).error_code, 'RECOVERY_UNAVAILABLE');
  assert.equal(f.calls.submit, 1);
}));
test('missing journals prove no send; corrupt and ambiguous journals never do', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps); const req = await request(directory, 'recover');
  const missing = await bridge.run(req); assert.equal(missing.error_code, 'NOT_SUBMITTED'); assert.equal(missing.not_submitted, true);
  for (const text of ['{bad', '{}', 'null', '[]', '{"dispatch_started":"false"}']) {
    await fs.writeFile(path.join(req.request_dir, 'journal.json'), text);
    for (const op of ['translate', 'recover']) {
      const result = await bridge.run({ ...req, op });
      assert.equal(result.error_code, 'JOURNAL_UNREADABLE'); assert.equal(result.not_submitted, false);
    }
  }
  await fs.writeFile(path.join(req.request_dir, 'journal.json'), JSON.stringify({ dispatch_started: true }));
  const result = await bridge.run({ ...req, op: 'translate' });
  assert.equal(result.not_submitted, false); assert.equal(f.calls.submit, 0); assert.equal(f.calls.attach.length, 0);
}));
test('legacy completed files migrate without opening Chrome or resending', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps); const req = await request(directory, 'recover');
  await fs.writeFile(req.output_file, 'legacy answer');
  await fs.writeFile(path.join(req.request_dir, 'journal.json'), JSON.stringify({ version: 1, dispatch_started: true, output_file: req.output_file }));
  assert.equal((await bridge.run(req)).answer, 'legacy answer');
  assert.equal(f.calls.submit, 0); assert.equal(f.calls.attach.length, 0);
}));
test('shared-browser implementation has no global cookie, tab enumeration, CLI or process-launch operations', async () => {
  const source = await fs.readFile(new URL('../bridge.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\bspawn\s*\(|\.getAllCookies\s*\(|\.deleteCookies\s*\(|\.getTargets\s*\(|\brunBrowserMode\s*\(|\bchromium\s*\./);
  assert.match(source, /invokeBroker\(request\)/);
});
test('disconnected browser status cannot reattach or open an authorization WebSocket', async () => temporary(async directory => {
  const f = fixture(); let anchor;
  const attach = f.deps.attach;
  f.deps.attach = async (...args) => { anchor = await attach(...args); return anchor; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  anchor.client._ws.readyState = 3;
  const before = f.calls.attach.length;
  for (const op of ['status', 'catalog', 'select']) {
    const response = await bridge.run({ op, state_dir: directory });
    assert.equal(response.status, 'needs_connection');
  }
  assert.equal(f.calls.attach.length, before); assert.equal(f.calls.scoped.length, 0);
}));
test('owned page facade binds every command to its exact session without reconnecting or closing browser', async () => {
  const commands = [];
  const connection = await connectOwnedPage({ client: {
    Target: { attachToTarget: async options => { assert.deepEqual(options, { targetId: 'owned-tab', flatten: true }); return { sessionId: 'own-session' }; }, detachFromTarget: async options => commands.push(['detach', options]) },
    send: async (...args) => { commands.push(args); return { result: { value: 'fixture' } }; },
  } }, 'owned-tab');
  await connection.client.Runtime.evaluate({ expression: 'location.href' });
  await connection.client.Input.insertText({ text: 'offline' });
  await connection.close();
  assert.deepEqual(commands, [['Runtime.evaluate', { expression: 'location.href' }, 'own-session'], ['Input.insertText', { text: 'offline' }, 'own-session'], ['detach', { sessionId: 'own-session' }]]);
});

test('notranslate document-start hints tolerate delayed root/head, are idempotent and skip other origins', async () => {
  let callback; let disconnected = 0;
  const classes = new Set(); const attrs = {}; const metas = [];
  const root = { setAttribute: (name, value) => { attrs[name] = value; }, classList: { add: value => classes.add(value) } };
  const head = { querySelector: () => metas[0], appendChild: node => metas.push(node) };
  const document = { documentElement: null, head: null, createElement: tag => { assert.equal(tag, 'meta'); return {}; } };
  const window = {}; window.top = window;
  const context = { document, window, location: { origin: 'https://chatgpt.com' }, MutationObserver: class {
    constructor(fn) { callback = fn; } observe(target, options) { assert.equal(target, document); assert.deepEqual({ ...options }, { childList: true, subtree: true }); } disconnect() { disconnected++; }
  } };
  vm.runInNewContext(OWNED_PAGE_NOTRANSLATE_SCRIPT, context);
  document.documentElement = root; callback();
  assert.equal(attrs.translate, 'no'); assert.ok(classes.has('notranslate')); assert.equal(disconnected, 0);
  document.head = head; callback();
  assert.equal(disconnected, 1); assert.deepEqual(metas, [{ name: 'google', content: 'notranslate' }]);
  vm.runInNewContext(OWNED_PAGE_NOTRANSLATE_SCRIPT, context);
  assert.equal(metas.length, 1);
  context.location.origin = 'https://example.invalid'; attrs.translate = 'yes';
  vm.runInNewContext(OWNED_PAGE_NOTRANSLATE_SCRIPT, context);
  assert.equal(attrs.translate, 'yes');
  context.location.origin = 'https://chatgpt.com'; window.top = {};
  vm.runInNewContext(OWNED_PAGE_NOTRANSLATE_SCRIPT, context);
  assert.equal(attrs.translate, 'yes', 'subframes are untouched');
});

test('notranslate installation uses the bound Page registration before applying current-page hints', async () => {
  const calls = [];
  await installOwnedPageTranslationHints({
    Page: { addScriptToEvaluateOnNewDocument: async args => calls.push(['register', args.source]) },
    Runtime: { evaluate: async args => { calls.push(['current', args.expression]); return {}; } },
  });
  assert.deepEqual(calls, [['register', OWNED_PAGE_NOTRANSLATE_SCRIPT], ['current', OWNED_PAGE_NOTRANSLATE_SCRIPT]]);
});

test('translated homepage detection excludes conversation, draft, generation and missing composer', async () => {
  const location = { origin: 'https://chatgpt.com', pathname: '/', search: '', hash: '' };
  let messages = []; let editors = [{ textContent: '' }]; let generating = null;
  const classes = new Set(['translated-ltr']);
  const document = {
    documentElement: { classList: { contains: name => classes.has(name) } },
    querySelectorAll: selector => selector.includes('data-message-author-role') ? messages : editors,
    querySelector: () => generating,
  };
  const runtime = { evaluate: async ({ expression }) => ({ result: { value: vm.runInNewContext(expression, { location, document }) } }) };
  assert.equal((await translatedHomeState(runtime)).emptyHome, true);
  assert.equal((await translatedHomeState(runtime)).translated, true);
  for (const modify of [() => { location.pathname = '/c/saved'; }, () => { messages = [{}]; }, () => { editors = [{ textContent: 'unsent draft' }]; }, () => { generating = {}; }, () => { editors = []; }]) {
    location.pathname = '/'; messages = []; editors = [{ textContent: '' }]; generating = null;
    modify(); assert.equal((await translatedHomeState(runtime)).emptyHome, false);
  }
  classes.clear(); assert.equal((await translatedHomeState(runtime)).translated, false);
});

test('only a legacy empty owned setup page reloads once on first protection, across broker restarts', async () => temporary(async directory => {
  const f = fixture(); let bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  assert.deepEqual(f.calls.reloads, [], 'new pages receive hints before navigation without a reload');
  const savedPath = path.join(directory, 'native-browser.json');
  const legacy = JSON.parse(await fs.readFile(savedPath, 'utf8'));
  delete legacy.notranslate_targets;
  await fs.writeFile(savedPath, JSON.stringify(legacy));
  await bridge.run({ op: 'status', state_dir: directory });
  assert.deepEqual(f.calls.reloads, [], 'read-only status never repairs/reloads');
  bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  assert.deepEqual(f.calls.reloads, ['owned-1']);
  await bridge.run({ op: 'catalog', state_dir: directory });
  bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  assert.deepEqual(f.calls.reloads, ['owned-1'], 'persistent one-shot prevents a reload loop');
  assert.equal(f.tabs.get('personal-tab').hintsInstalled, undefined);
}));

test('translation hints never reload a draft, submitted page or recovering conversation', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  const setup = f.tabs.get('owned-1'); setup.translated = true; setup.draft = true;
  await bridge.run({ op: 'catalog', state_dir: directory });
  setup.draft = false; setup.messageId = 'existing-message';
  await bridge.run({ op: 'login', state_dir: directory });
  let captures = 0;
  f.deps.waitAnswer = async () => { if (++captures === 1) throw new Error('interrupted capture'); return { text: 'recovered' }; };
  // createBridge snapshots dependencies; use the same fixture through a new broker.
  const resumed = createBridge(f.deps); await resumed.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await resumed.run(req);
  f.tabs.get('owned-2').translated = true;
  assert.equal((await resumed.run({ ...req, op: 'recover' })).answer, 'recovered');
  assert.deepEqual(f.calls.reloads, []);
}));
