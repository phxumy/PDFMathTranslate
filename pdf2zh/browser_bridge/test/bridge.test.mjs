import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { createHash } from 'node:crypto';
import { createBridge, connectOwnedPage, textSubmissionInput, OWNED_PAGE_NOTRANSLATE_SCRIPT, installOwnedPageTranslationHints, translatedHomeState, structuredUserContent, userTurn, submissionDiagnostic } from '../bridge.mjs';
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
      const client = { Runtime: { state, evaluate: async () => ({ result: { value: (state.count || 0) * 2 } }) }, Input: {}, Page: { reload: async () => calls.reloads.push(id) }, on: () => {},
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
    promptReady: async () => {}, readUserIds: async runtime => runtime.state.userIds || (runtime.state.messageId ? [runtime.state.messageId] : []),
    readFingerprint: async runtime => runtime.state.messageId ? browserPromptFingerprint(runtime.state.text, runtime.state.messageId) : undefined,
    userTurn: async runtime => ({ ...runtime.state }),
    submit: async (options, prompt) => {
      assert.equal(options.page, undefined, 'submission cannot bring the browser tab to the foreground');
      assert.equal(options.input.dispatchMouseEvent, undefined, 'the first and only send uses Oracle DOM click, never an ineffective background mouse then fallback');
      assert.equal(typeof options.input.insertText, 'function');
      assert.equal(typeof options.input.dispatchKeyEvent, 'function');
      calls.submit++;
      const state = options.runtime.state;
      const messageId = `submitted-user-${calls.submit}`;
      Object.assign(state, { messageId, text: prompt, renderedText: prompt, count: (state.count || 0) + 1,
        userIds: [...(state.userIds || []), messageId], url: 'https://chatgpt.com/c/our-request' });
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
    return { targetId, client: { Runtime: { state, evaluate: async () => ({ result: { value: (state.count || 0) * 2 } }) }, Input: {}, Page: { reload: async () => calls.reloads.push(targetId) } }, close: async options => { calls.close.push({ id: targetId, options }); } };
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
test('first submission persists its local client conversation ID through permanent URL assignment', async () => temporary(async directory => {
  const f = fixture(); const send = f.deps.submit;
  f.deps.submit = async (options, ...args) => { await send(options, ...args); if (options.runtime.state.count === 1) options.runtime.state.url = 'https://chatgpt.com/c/local-chatgpt%3Aclient-id'; };
  f.deps.waitAnswer = async (runtime, _timeout, _log, _baseline, conversationId, submittedTurn) => {
    assert.ok(conversationId === undefined || conversationId === 'our-request');
    assert.equal(submittedTurn.localConversationId, 'local-chatgpt:client-id');
    runtime.state.url = 'https://chatgpt.com/c/our-request';
    return { text: '{"translation":"complete"}', localConversationId: submittedTurn.localConversationId };
  };
  const req = { ...await request(directory), task_id: 'local-binding-across-batches' }; const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  assert.equal((await bridge.run(req)).ok, true);
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.local_conversation_id, 'local-chatgpt:client-id');
  assert.equal(journal.conversation_url, 'https://chatgpt.com/c/our-request');
  assert.equal(f.calls.submit, 1);
  const next = { ...await request(directory, 'translate', '-next'), task_id: req.task_id };
  assert.equal((await bridge.run(next)).ok, true);
  const nextJournal = JSON.parse(await fs.readFile(path.join(next.request_dir, 'journal.json'), 'utf8'));
  assert.equal(nextJournal.local_conversation_id, 'local-chatgpt:client-id');
  assert.equal(f.calls.submit, 2);
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
  release();
  const firstResult = await first; const sameResult = await same;
  assert.equal(firstResult.answer, 'done', JSON.stringify(firstResult));
  assert.equal(sameResult.answer, 'done', JSON.stringify(sameResult));
  assert.equal(f.calls.submit, 1);
}));
test('joining a pending batch retains its result if completion races the ownership journal read', async t => temporary(async directory => {
  const f = fixture(); let release; let entered;
  const capturing = new Promise(resolve => { entered = resolve; });
  f.deps.waitAnswer = async () => { entered(); return new Promise(resolve => { release = () => resolve({ text: 'done' }); }); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); const first = bridge.run(req); await capturing;
  const readFile = fs.readFile.bind(fs); let raced = false;
  t.mock.method(fs, 'readFile', async (filename, ...args) => {
    const data = await readFile(filename, ...args);
    if (!raced && filename === path.join(req.request_dir, 'journal.json')) {
      raced = true;
      // Deterministically finish the first call while the joining caller is
      // awaiting its ownership check. No wall-clock sleep or account is used.
      release();
      assert.equal((await first).answer, 'done');
    }
    return data;
  });
  const same = await bridge.run({ ...req, op: 'recover' });
  assert.equal(raced, true);
  assert.equal(same.answer, 'done');
  assert.equal(f.calls.submit, 1);
}));
test('Windows temporary journal sharing locks retry atomic replacement without another submission', { skip: process.platform !== 'win32' }, async t => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); const rename = fs.rename.bind(fs); let blocked = 0;
  t.mock.method(fs, 'rename', async (source, destination) => {
    if (destination === path.join(req.request_dir, 'journal.json') && blocked < 2 &&
        JSON.parse(await fs.readFile(source, 'utf8')).status === 'complete') {
      blocked++;
      assert.equal(JSON.parse(await fs.readFile(destination, 'utf8')).dispatch_started, true, 'prior submission proof survives a locked replacement');
      throw Object.assign(new Error('fixture sharing lock'), { code: 'EPERM' });
    }
    return rename(source, destination);
  });
  const result = await bridge.run(req);
  assert.equal(blocked, 2);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(f.calls.submit, 1);
  assert.equal(JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8')).status, 'complete');
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

test('shell rollout rebinds changed DOM text only with exact saved ID, URL, baseline and original full prompt', async () => temporary(async directory => {
  for (const condition of ['valid', 'wrong-id', 'wrong-prompt', 'wrong-url', 'missing-file']) {
    const dir = path.join(directory, condition); const f = fixture(); let captures = 0, clock = 0;
    f.deps.recoveryNow = () => clock; f.deps.recoveryPause = async ms => { clock += ms; };
    f.deps.waitAnswer = async () => { if (++captures === 1) throw new Error('original wait interrupted'); return { text: 'recovered original' }; };
    const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: dir });
    const req = await request(dir); await bridge.run(req);
    const state = f.tabs.get('owned-2'); const original = state.text;
    Object.assign(state, { shell: true, text: original + ' UI changed', renderedText: original, structuredText: original });
    if (condition === 'wrong-id') { state.messageId = 'another'; state.userIds = ['another']; }
    if (condition === 'wrong-prompt') { state.renderedText = 'different'; state.structuredText = 'different'; }
    if (condition === 'wrong-url') state.url = 'https://chatgpt.com/c/another';
    if (condition === 'missing-file') await fs.unlink(req.prompt_file);
    const result = await bridge.run({ ...req, op: 'recover' });
    assert.equal(f.calls.submit, 1);
    if (condition === 'valid') { assert.equal(result.answer, 'recovered original'); assert.equal(captures, 2); }
    else { assert.equal(result.ok, false); assert.equal(captures, 1); }
  }
}));

test('web language aliases keep later batches in the original task and conversation', async () => temporary(async directory => {
  const f = fixture(); let label = '即時';
  f.deps.ui.catalog = async () => ({ model: 'actual-account-model', effort: label });
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const first = await bridge.run({ ...await request(directory), task_id: 'same-pdf', effort: label });
  label = '即时';
  const second = await bridge.run({ ...await request(directory, 'translate', '-second'), task_id: 'same-pdf', effort: label });
  assert.equal(second.ok, true); assert.equal(second.conversation_url, first.conversation_url);
  assert.equal(f.calls.submit, 2); assert.equal(f.tabs.size, 3);
}));

test('explicit recovery refresh navigates the same verified conversation once without another submit', async () => temporary(async directory => {
  const f = fixture(); let captures = 0; let readyChecks = 0;
  f.deps.waitAnswer = async () => { if (++captures === 1) throw new Error('stalled connection'); return { text: 'refreshed answer' }; };
  f.deps.promptReady = async () => { readyChecks++; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'refresh-task' }; await bridge.run(req);
  const before = f.calls.navigate.length; const previousReady = readyChecks;
  const recovered = await bridge.run({ ...req, op: 'recover', refresh_conversation: true });
  assert.equal(recovered.answer, 'refreshed answer'); assert.equal(f.calls.submit, 1);
  assert.equal(f.calls.navigate.length, before + 1); assert.equal(f.calls.navigate.at(-1), 'https://chatgpt.com/c/our-request');
  assert.equal(readyChecks, previousReady + 2); assert.equal(f.tabs.size, 3);
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.target_id, 'owned-2'); assert.equal(journal.task_id, 'refresh-task');
}));

test('explicit recovery refresh refuses unverified identity, draft or missing original target without navigating', async () => temporary(async directory => {
  for (const failure of ['identity', 'draft', 'closed', 'missing-fingerprint']) {
    const f = fixture(); let captures = 0; let clock = 0;
    f.deps.waitAnswer = async () => { captures++; throw new Error('stalled connection'); };
    f.deps.recoveryNow = () => clock; f.deps.recoveryPause = async ms => { clock += ms; };
    const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
    const req = await request(directory, 'translate', `-${failure}`); await bridge.run(req);
    if (failure === 'identity') f.tabs.get('owned-2').text = 'Different request';
    if (failure === 'draft') f.tabs.get('owned-2').hasDraft = true;
    if (failure === 'closed') f.tabs.delete('owned-2');
    if (failure === 'missing-fingerprint') {
      const filename = path.join(req.request_dir, 'journal.json'); const journal = JSON.parse(await fs.readFile(filename, 'utf8'));
      delete journal.submitted_prompt_hash; await fs.writeFile(filename, JSON.stringify(journal));
    }
    const before = f.calls.navigate.length;
    const result = await bridge.run({ ...req, op: 'recover', refresh_conversation: true });
    assert.equal(result.ok, false); assert.equal(result.not_submitted, false);
    assert.equal(result.error_code, failure === 'draft' ? 'RECOVERY_DRAFT_PRESENT' : failure === 'closed' ? 'RECOVERY_UNAVAILABLE' : 'RECOVERY_IDENTITY_UNVERIFIED');
    assert.equal(f.calls.navigate.length, before); assert.equal(f.calls.submit, 1); assert.equal(captures, 1);
  }
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
  assert.equal((await bridge.run({ ...req, op: 'recover' })).error_code, 'RECOVERY_IDENTITY_UNVERIFIED');
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

test('text submission Input exposes only insertion/key events and forwards to the same bound target', async () => {
  const calls = [];
  const input = {
    async insertText(params) { assert.equal(this, input); calls.push(['insertText', params]); return 'inserted'; },
    async dispatchKeyEvent(params) { assert.equal(this, input); calls.push(['dispatchKeyEvent', params]); return 'key-sent'; },
    async dispatchMouseEvent() { throw new Error('background mouse must never be attempted'); },
  };
  const facade = textSubmissionInput(input);
  assert.deepEqual(Object.keys(facade).sort(), ['dispatchKeyEvent', 'insertText']);
  assert.equal('dispatchMouseEvent' in facade, false);
  assert.equal(await facade.insertText({ text: 'offline fixture' }), 'inserted');
  assert.equal(await facade.dispatchKeyEvent({ type: 'keyDown', key: 'Enter' }), 'key-sent');
  assert.deepEqual(calls, [['insertText', { text: 'offline fixture' }], ['dispatchKeyEvent', { type: 'keyDown', key: 'Enter' }]]);
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

test('two batches of one PDF reuse the same chat and persist the prior-turn baseline', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'pdf-settings-fixed' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: first.task_id };
  await fs.writeFile(second.prompt_file, 'Second batch of the same PDF.');
  assert.equal((await bridge.run(first)).ok, true);
  assert.equal((await bridge.run(second)).ok, true);
  assert.equal(f.calls.submit, 2); assert.deepEqual(f.calls.navigate, ['https://chatgpt.com/', 'https://chatgpt.com/']);
  const journal = JSON.parse(await fs.readFile(path.join(second.request_dir, 'journal.json'), 'utf8'));
  assert.deepEqual(journal.previous_user_ids, ['submitted-user-1']); assert.equal(journal.previous_user_count, 1); assert.equal(journal.baseline_turns, 2);
  assert.deepEqual(journal.accepted_user_ids, ['submitted-user-1', 'submitted-user-2']);
  const task = JSON.parse(await fs.readFile(path.join(directory, 'tasks', `${first.task_id}.json`), 'utf8'));
  assert.equal(task.target_id, 'owned-2'); assert.equal(task.pending_request_dir, null); assert.equal(task.last_user_count, 2);
  assert.equal(task.conversation_url, 'https://chatgpt.com/c/our-request');
}));

test('a reordered batch recovers the pending task before submitting its own prompt', async () => temporary(async directory => {
  const f = fixture(); let captures = 0;
  f.deps.waitAnswer = async () => { if (++captures === 1) throw new Error('interrupted first batch'); return { text: 'complete' }; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'same-pdf' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: 'same-pdf' };
  assert.equal((await bridge.run(first)).not_submitted, false);
  assert.equal((await bridge.run(second)).ok, true); assert.equal(f.calls.submit, 2);
  assert.equal((await bridge.run({ ...first, op: 'recover' })).ok, true);
  assert.equal(f.calls.submit, 2);
  assert.equal(f.calls.navigate.length, 2);
}));

test('eight PDF batches retain ordered identities when the shell mounts only the last five turns', async () => temporary(async directory => {
  const f = fixture(); const submit = f.deps.submit;
  f.deps.submit = async (...args) => {
    await submit(...args);
    const state = args[0].runtime.state;
    state.shell = true; state.userIds = state.userIds.slice(-5); state.count = state.userIds.length;
  };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  for (let index = 1; index <= 8; index++) {
    const req = { ...await request(directory, 'translate', `-${index}`), task_id: 'virtualized-pdf' };
    const result = await bridge.run(req);
    assert.equal(result.ok, true, JSON.stringify(result));
    const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json')));
    assert.deepEqual(journal.accepted_user_ids, Array.from({ length: index }, (_, i) => `submitted-user-${i + 1}`));
    assert.equal(journal.submitted_user_count, index);
  }
  assert.equal(f.calls.submit, 8); assert.equal(f.calls.navigate.length, 2);
  assert.equal(f.tabs.get('owned-2').count, 5);
}));

test('an old unbound sixth batch recovers after prefix virtualization without resending', async () => temporary(async directory => {
  const f = fixture(); const submit = f.deps.submit;
  f.deps.submit = async (...args) => { await submit(...args); const s = args[0].runtime.state; s.shell = true; s.userIds = s.userIds.slice(-5); s.count = s.userIds.length; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  for (let i = 1; i <= 5; i++) assert.equal((await bridge.run({ ...await request(directory, 'translate', `-${i}`), task_id: 'old-sixth' })).ok, true);
  const req = { ...await request(directory, 'translate', '-6'), task_id: 'old-sixth' };
  const completed = await bridge.run(req); assert.equal(completed.ok, true);
  const file = path.join(req.request_dir, 'journal.json'); const saved = JSON.parse(await fs.readFile(file));
  for (const key of ['submitted_prompt_hash', 'submitted_user_id', 'submitted_user_count', 'accepted_user_ids']) delete saved[key];
  saved.status = 'paused'; await fs.writeFile(file, JSON.stringify(saved)); await fs.unlink(saved.output_file);
  const taskFile = path.join(directory, 'tasks', 'old-sixth.json'); const task = JSON.parse(await fs.readFile(taskFile));
  task.pending_request_dir = req.request_dir; await fs.writeFile(taskFile, JSON.stringify(task));
  const recovered = await bridge.run({ ...req, op: 'recover' });
  assert.equal(recovered.ok, true, JSON.stringify(recovered)); assert.equal(f.calls.submit, 6);
  assert.equal(JSON.parse(await fs.readFile(file)).accepted_user_ids.length, 6);
}));

test('failed recovery of a reordered task never sends the new batch', async () => temporary(async directory => {
  const f = fixture(); f.deps.waitAnswer = async () => { throw Object.assign(new Error('original answer pending'), { code: 'ASSISTANT_TIMEOUT' }); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'still-pending' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: first.task_id };
  assert.equal((await bridge.run(first)).ok, false);
  const blocked = await bridge.run(second);
  assert.equal(blocked.error_code, 'ASSISTANT_TIMEOUT'); assert.equal(blocked.not_submitted, true); assert.equal(f.calls.submit, 1);
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'tasks', 'still-pending.json'))).pending_request_dir, first.request_dir);
}));

test('closed task tab reopens only the exact saved conversation before appending the next batch', async () => temporary(async directory => {
  const f = fixture(); let saved;
  const create = f.deps.ui.createNativePage;
  f.deps.ui.createNativePage = client => {
    const page = create(client); const goto = page.goto;
    page.goto = async url => { await goto(url); if (url === 'https://chatgpt.com/c/our-request' && saved) Object.assign(client.Runtime.state, saved); };
    return page;
  };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'reopen-original' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: first.task_id };
  assert.equal((await bridge.run(first)).ok, true); saved = { ...f.tabs.get('owned-2') }; f.tabs.delete('owned-2');
  assert.equal((await bridge.run(second)).ok, true);
  assert.deepEqual(f.calls.navigate, ['https://chatgpt.com/', 'https://chatgpt.com/', 'https://chatgpt.com/c/our-request']);
  assert.equal(f.calls.submit, 2);
}));

test('closed pristine task tab can be replaced after a model-picker failure that never reached submission', async () => temporary(async directory => {
  const f = fixture(); const catalog = f.deps.ui.catalog;
  f.deps.ui.catalog = async () => { throw Object.assign(new Error('picker not ready'), { code: 'PICKER_UNAVAILABLE' }); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'unsent-picker-failure' };
  assert.equal((await bridge.run(req)).not_submitted, true);
  assert.equal(f.calls.submit, 0);
  f.tabs.delete('owned-2'); f.deps.ui.catalog = catalog;
  assert.equal((await bridge.run(req)).ok, true);
  assert.equal(f.calls.submit, 1);
  assert.ok(!f.calls.attach.some(item => item.targetId === 'personal-tab'));
}));

test('closed task without a saved conversation cannot be replaced once settings or a dispatch journal exist', async () => temporary(async directory => {
  const f = fixture(); const ids = f.deps.readUserIds;
  f.deps.readUserIds = async () => { throw new Error('baseline interrupted'); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'possibly-used-task' };
  await bridge.run(req); f.tabs.delete('owned-2'); f.deps.readUserIds = ids;
  assert.equal((await bridge.run(req)).error_code, 'TASK_CONVERSATION_UNAVAILABLE');
  assert.equal(f.calls.submit, 0);
  const taskFile = path.join(directory, 'tasks', `${req.task_id}.json`);
  const task = JSON.parse(await fs.readFile(taskFile, 'utf8'));
  Object.assign(task, { model: null, effort: null }); await fs.writeFile(taskFile, JSON.stringify(task));
  await fs.writeFile(path.join(req.request_dir, 'journal.json'), JSON.stringify({ version: 3, task_id: req.task_id, dispatch_started: true, previous_user_ids: [], target_id: 'owned-2', browser_ws: 'ws://127.0.0.1:9222/devtools/browser/fixture' }));
  const before = f.calls.navigate.length;
  assert.equal((await bridge.run(req)).error_code, 'RECOVERY_UNAVAILABLE');
  assert.equal(f.calls.navigate.length, before); assert.equal(f.calls.submit, 0);
}));

test('fixed task model and changed prior user IDs prevent continuation before any send', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'fixed-task' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: first.task_id };
  assert.equal((await bridge.run(first)).ok, true);
  assert.equal((await bridge.run({ ...second, model: 'different-model' })).error_code, 'TASK_SETTINGS_CHANGED');
  f.tabs.get('owned-2').userIds = ['tampered-earlier-id'];
  assert.equal((await bridge.run(second)).error_code, 'RECOVERY_IDENTITY_UNVERIFIED');
  assert.equal(f.calls.submit, 1); assert.equal(f.calls.navigate.length, 2);
}));

test('a new message cannot bind when earlier user IDs are replaced or reordered', async () => temporary(async directory => {
  const f = fixture(); const submit = f.deps.submit;
  f.deps.submit = async (...args) => { await submit(...args); if (f.calls.submit === 2) args[0].runtime.state.userIds[0] = 'replaced-prior-user'; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'id-prefix' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: first.task_id };
  assert.equal((await bridge.run(first)).ok, true);
  const result = await bridge.run(second); assert.equal(result.error_code, 'RECOVERY_IDENTITY_UNVERIFIED');
  assert.equal(result.diagnostic.new_user_matches_baseline, false); assert.equal(result.not_submitted, false);
  assert.equal(f.calls.submit, 2);
}));

test('rendered whitespace can match original prompt while identity stays bound to raw DOM', async () => temporary(async directory => {
  const f = fixture(); const submit = f.deps.submit;
  f.deps.submit = async (...args) => { await submit(...args); const state = args[0].runtime.state; state.text = state.text.replaceAll('\n', ''); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'rendered-layout' };
  await fs.writeFile(req.prompt_file, 'Paragraph one.\n\nParagraph two.');
  const result = await bridge.run(req); assert.equal(result.ok, true);
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.matched_text_source, 'innerText');
  assert.equal(journal.submitted_prompt_hash, browserPromptFingerprint('Paragraph one.Paragraph two.', 'submitted-user-1'));
}));

test('legacy missing fingerprint diagnoses read-only and recovers only its original owned target', async () => temporary(async directory => {
  const f = fixture(); let captures = 0;
  f.deps.waitAnswer = async () => { if (++captures === 1) throw new Error('interrupted'); return { text: 'legacy recovered' }; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await bridge.run(req);
  const filename = path.join(req.request_dir, 'journal.json'); const journal = JSON.parse(await fs.readFile(filename, 'utf8'));
  const oldTask = journal.task_id;
  for (const name of ['task_id', 'submitted_prompt_hash', 'submitted_user_id', 'submitted_user_count', 'accepted_user_ids', 'previous_user_ids', 'previous_user_count']) delete journal[name];
  journal.version = 2; journal.conversation_url = null;
  await fs.writeFile(filename, JSON.stringify(journal));
  await fs.unlink(path.join(directory, 'tasks', `${oldTask}.json`));
  const original = await fs.readFile(filename, 'utf8');
  const diagnostic = await bridge.run({ ...req, op: 'diagnose_request', task_id: 'adopted-pdf' });
  assert.equal(diagnostic.status, 'diagnostic'); assert.equal(diagnostic.diagnostic.normalized_hash_equal, true);
  assert.equal(await fs.readFile(filename, 'utf8'), original, 'diagnosis does not bind or mutate a journal');
  assert.doesNotMatch(JSON.stringify(diagnostic), /Fixture text|submitted-user|our-request|https:\/\//);
  assert.equal((await bridge.run({ ...req, op: 'recover', task_id: 'adopted-pdf' })).answer, 'legacy recovered');
  assert.equal((await bridge.run({ ...req, op: 'recover', task_id: 'different-pdf' })).error_code, 'TASK_OWNERSHIP_MISMATCH');
  assert.equal(f.calls.submit, 1); assert.equal(f.calls.navigate.length, 2);
}));

test('missing-fingerprint recovery cannot create a new tab even with a saved URL', async () => temporary(async directory => {
  const f = fixture(); f.deps.waitAnswer = async () => { throw new Error('interrupted'); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory); await bridge.run(req);
  const filename = path.join(req.request_dir, 'journal.json'); const journal = JSON.parse(await fs.readFile(filename, 'utf8'));
  delete journal.submitted_prompt_hash; await fs.writeFile(filename, JSON.stringify(journal)); f.tabs.delete('owned-2');
  const response = await bridge.run({ ...req, op: 'recover' }); assert.equal(response.error_code, 'RECOVERY_UNAVAILABLE');
  assert.equal(response.not_submitted, false); assert.equal(f.calls.submit, 1); assert.equal(f.calls.navigate.length, 2);
}));

test('task IDs reject path traversal and Windows device filenames before creating files', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps); const req = await request(directory);
  for (const task_id of ['../other', 'a/b', 'CON', '', 'a'.repeat(129)]) assert.equal((await bridge.run({ ...req, task_id })).error_code, 'INVALID_TASK_ID');
  assert.equal(await fs.stat(path.join(directory, 'tasks')).catch(() => null), null);
  assert.equal(f.calls.attach.length, 0);
}));

test('concurrent different task cannot inherit another task pending response from the same request directory', async () => temporary(async directory => {
  const f = fixture(); let release; let entered;
  const waiting = new Promise(resolve => { entered = resolve; });
  f.deps.waitAnswer = async () => { entered(); return new Promise(resolve => { release = () => resolve({ text: 'original task answer' }); }); };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'original-owner' };
  const pending = bridge.run(req); await waiting;
  const other = await bridge.run({ ...req, op: 'recover', task_id: 'other-owner' });
  assert.equal(other.error_code, 'TASK_OWNERSHIP_MISMATCH'); assert.equal(other.answer, undefined);
  release(); assert.equal((await pending).answer, 'original task answer'); assert.equal(f.calls.submit, 1);
}));

test('failed capture after navigation never replaces a verified conversation URL', async () => temporary(async directory => {
  const f = fixture();
  f.deps.waitAnswer = async runtime => { runtime.state.url = 'https://chatgpt.com/c/other-thread'; return { text: 'must not be accepted' }; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'url-integrity' };
  const response = await bridge.run(req); assert.equal(response.error_code, 'RECOVERY_IDENTITY_UNVERIFIED');
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.conversation_url, 'https://chatgpt.com/c/our-request'); assert.equal(journal.status, 'paused');
}));

test('simultaneous direct calls serialize before the journal read and submit only once', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'simultaneous' };
  const responses = await Promise.all([bridge.run(req), bridge.run(req)]);
  assert.ok(responses.every(response => response.ok)); assert.equal(f.calls.submit, 1);
  assert.equal(f.calls.navigate.length, 2);
}));

test('simultaneous different batches cannot both cross the initial async journal read', async () => temporary(async directory => {
  const f = fixture(); const bridge = createBridge(f.deps);
  await bridge.run({ op: 'login', state_dir: directory });
  const first = { ...await request(directory, 'translate', '-1'), task_id: 'race-task' };
  const second = { ...await request(directory, 'translate', '-2'), task_id: first.task_id };
  const responses = await Promise.all([bridge.run(first), bridge.run(second)]);
  assert.equal(responses.filter(response => response.ok).length, 1);
  assert.equal(responses.find(response => !response.ok).error_code, 'BROWSER_BUSY');
  assert.equal(f.calls.submit, 1); assert.equal(f.calls.navigate.length, 2);
}));

function domNode(tagName, ...children) {
  return { nodeType: 1, tagName, childNodes: children.map(child => typeof child === 'string' ? { nodeType: 3, nodeValue: child } : child) };
}
test('DOM text reconstruction keeps every nested text node and only restores structural breaks and inline code marks', () => {
  const content = domNode('DIV', domNode('P', 'First ', domNode('A', domNode('FONT', domNode('FONT', 'linked'))), ' paragraph.'),
    domNode('DIV', 'Second', domNode('BR'), 'line: ', domNode('CODE', 'text'), ' / ', domNode('CODE', domNode('SPAN', 'index'))),
    domNode('PRE', domNode('CODE', '{"literal":"\\n"}')));
  const result = structuredUserContent(content);
  assert.equal(result.text, 'First linked paragraph.\nSecond\nline: text / index\n{"literal":"\\n"}\n');
  assert.equal(result.inlineCodeText, 'First linked paragraph.\nSecond\nline: `text` / `index`\n{"literal":"\\n"}\n');
  assert.equal(result.inlineCodeCount, 2); assert.equal(result.tagCounts.code, 3);
  assert.equal(result.tagCounts.br, 1); assert.equal(result.tagCounts.div, 2); assert.equal(result.tagCounts.p, 1);
  assert.equal(structuredUserContent(domNode('PRE', domNode('CODE', 'only fenced content'))).inlineCodeText, null);
});

test('structural candidates omit native UI controls but preserve literal expansion labels and nested links in prompt content', () => {
  const content = domNode('DIV', domNode('P', '正文显示更多 ', domNode('A', domNode('SPAN', domNode('FONT', '顯示更多'))), ' ', domNode('CODE', 'text')),
    domNode('BUTTON', domNode('SPAN', domNode('FONT', '顯示更多'))), domNode('INPUT'),
    domNode('SELECT', domNode('OPTION', 'UI choice')), domNode('TEXTAREA', 'UI field'));
  const result = structuredUserContent(content);
  assert.equal(result.text, '正文显示更多 顯示更多 text\n');
  assert.equal(result.inlineCodeText, '正文显示更多 顯示更多 `text`\n');
  assert.equal(result.buttonCount, 1); assert.equal(result.controlCount, 4);
  assert.equal(result.inlineCodeCount, 1);
  assert.equal(structuredUserContent(domNode('DIV', '顯示更多')).text, '顯示更多');
});

test('owned user snapshot evaluates the serialized structural reader and keeps raw fingerprint inputs untouched', async () => {
  const latest = Object.assign(domNode('DIV', domNode('P', 'A', domNode('BR'), 'B ', domNode('CODE', 'text'))), {
    textContent: 'AB text', innerText: 'AB text', getAttribute: () => 'latest-user',
  });
  const earlier = { getAttribute: () => 'earlier-user' };
  const document = { querySelectorAll: selector => selector.includes('data-message-author-role') ? [earlier, latest] : [{ textContent: '' }], querySelector: () => null };
  const runtime = { evaluate: async ({ expression }) => ({ result: { value: vm.runInNewContext(expression, { document, location: { href: 'https://chatgpt.com/c/owned' } }) } }) };
  const turn = await userTurn(runtime);
  assert.equal(turn.text, 'AB text'); assert.equal(turn.messageId, 'latest-user'); assert.equal(turn.count, 2);
  assert.equal(turn.structuredText, 'A\nB text\n'); assert.equal(turn.inlineCodeText, 'A\nB `text`\n'); assert.equal(turn.inlineCodeCount, 1);
  assert.deepEqual(Array.from(turn.userIds), ['earlier-user', 'latest-user']);
});

test('structural-only match accepts the whole prompt and retains Oracle raw text fingerprint', async () => temporary(async directory => {
  const f = fixture(); const submit = f.deps.submit;
  f.deps.submit = async (...args) => {
    await submit(...args);
    const state = args[0].runtime.state;
    Object.assign(state, { text: 'First.Second.line.', renderedText: 'First.Second.line.',
      structuredText: structuredUserContent(domNode('DIV', domNode('P', 'First.'), domNode('P', 'Second.', domNode('BR'), 'line.'))).text });
  };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = { ...await request(directory), task_id: 'structure-only' };
  await fs.writeFile(req.prompt_file, 'First.\r\n\r\nSecond.\r\nline.');
  assert.equal((await bridge.run(req)).ok, true);
  const journal = JSON.parse(await fs.readFile(path.join(req.request_dir, 'journal.json'), 'utf8'));
  assert.equal(journal.matched_text_source, 'domStructure');
  assert.equal(journal.submitted_prompt_hash, browserPromptFingerprint('First.Second.line.', 'submitted-user-1'));
  assert.equal(f.calls.submit, 1);
}));

test('legacy unbound batch can recover inline-code DOM formatting once without resending or changing conversations', async () => temporary(async directory => {
  const f = fixture(); let captures = 0;
  f.deps.waitAnswer = async () => { if (++captures === 1) throw new Error('interrupted'); return { text: 'old answer recovered' }; };
  const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
  const req = await request(directory);
  await fs.writeFile(req.prompt_file, 'Return `text`\r\n\r\nand `index`.');
  await bridge.run(req);
  const filename = path.join(req.request_dir, 'journal.json'); const journal = JSON.parse(await fs.readFile(filename, 'utf8'));
  const oldTask = journal.task_id;
  for (const key of ['task_id', 'submitted_prompt_hash', 'submitted_user_id', 'submitted_user_count', 'accepted_user_ids', 'previous_user_ids', 'previous_user_count']) delete journal[key];
  journal.version = 2; journal.conversation_url = null;
  await fs.writeFile(filename, JSON.stringify(journal)); await fs.unlink(path.join(directory, 'tasks', `${oldTask}.json`));
  const structure = structuredUserContent(domNode('DIV', domNode('P', 'Return ', domNode('CODE', 'text')), domNode('P', 'and ', domNode('CODE', 'index'), '.'),
    domNode('BUTTON', domNode('SPAN', '顯示更多'))));
  Object.assign(f.tabs.get('owned-2'), { text: 'Return textand index.顯示更多', renderedText: 'Return text and index. 顯示更多',
    structuredText: structure.text, inlineCodeText: structure.inlineCodeText, inlineCodeCount: structure.inlineCodeCount,
    buttonCount: structure.buttonCount, controlCount: structure.controlCount, tagCounts: structure.tagCounts });
  const diagnostic = await bridge.run({ ...req, op: 'diagnose_request', task_id: 'adopt-structure' });
  assert.equal(diagnostic.diagnostic.normalized_hash_equal, false); assert.equal(diagnostic.diagnostic.rendered_normalized_hash_equal, false);
  assert.equal(diagnostic.diagnostic.structural_normalized_hash_equal, false); assert.equal(diagnostic.diagnostic.inline_code_normalized_hash_equal, true);
  assert.equal(diagnostic.diagnostic.inline_code_count, 2); assert.equal(diagnostic.diagnostic.observed_fingerprint_matches_raw, true);
  assert.equal(diagnostic.diagnostic.button_count, 1); assert.equal(diagnostic.diagnostic.control_count, 1);
  assert.equal(diagnostic.diagnostic.normalized_mismatches.inline_code.index, -1);
  const recovered = await bridge.run({ ...req, op: 'recover', task_id: 'adopt-structure' });
  assert.equal(recovered.answer, 'old answer recovered'); assert.equal(f.calls.submit, 1); assert.equal(f.calls.navigate.length, 2);
  const saved = JSON.parse(await fs.readFile(filename, 'utf8'));
  assert.equal(saved.matched_text_source, 'domInlineCode');
  assert.equal(saved.submitted_prompt_hash, browserPromptFingerprint('Return textand index.顯示更多', 'submitted-user-1'));
  assert.equal(saved.conversation_url, 'https://chatgpt.com/c/our-request');
}));

test('structural matching never removes token separators, punctuation, escapes or zero-width characters', async () => temporary(async directory => {
  for (const [expected, actual] of [['A B', 'AB'], ['A,B', 'AB'], ['A\\nB', 'A\nB'], ['AB', 'A\u200bB'], ['`text`', '`different`']]) {
    const f = fixture(); const submit = f.deps.submit;
    f.deps.submit = async (...args) => { await submit(...args); Object.assign(args[0].runtime.state, { text: 'mismatch', renderedText: 'mismatch', structuredText: actual, inlineCodeText: actual }); };
    const bridge = createBridge(f.deps); await bridge.run({ op: 'login', state_dir: directory });
    const req = await request(directory, 'translate', `-${Buffer.from(expected).toString('hex')}`); await fs.writeFile(req.prompt_file, expected);
    const result = await bridge.run(req); assert.equal(result.error_code, 'RECOVERY_IDENTITY_UNVERIFIED'); assert.equal(result.not_submitted, false);
    assert.equal(result.diagnostic.structural_normalized_hash_equal, false); assert.equal(f.calls.submit, 1);
    assert.equal((await bridge.run({ ...req, op: 'recover' })).error_code, 'RECOVERY_IDENTITY_UNVERIFIED'); assert.equal(f.calls.submit, 1);
  }
}));

test('diagnostics distinguish CRLF normalization from a real mismatch without exposing prompt text', () => {
  const prompt = 'schema.\r\n\r\nX'; const normalized = 'schema. X';
  const digest = value => createHash('sha256').update(value).digest('hex');
  const journal = { prompt_hash: digest(prompt), prompt_normalized_hash: digest(normalized), previous_user_ids: [], baseline_turns: 0 };
  const turn = { count: 1, messageId: 'hidden-message-id', userIds: ['hidden-message-id'], url: 'https://chatgpt.com/c/hidden-conversation',
    text: 'schema.X', renderedText: 'schema.\nX', structuredText: 'schema.\n\nX', tagCounts: { br: 2, p: 1 } };
  const result = submissionDiagnostic(journal, turn, prompt, browserPromptFingerprint(turn.text, turn.messageId));
  assert.equal(result.first_mismatch, 7); assert.equal(result.original_length, 12); assert.equal(result.original_lf_normalized_length, 10);
  assert.equal(result.original_newline_count, 2); assert.equal(result.br_count, 2);
  assert.deepEqual(result.normalized_mismatches.raw, { index: 7, window_start: 5, expected_codepoints: [97, 46, 32, 88], actual_codepoints: [97, 46, 88] });
  assert.equal(result.normalized_mismatches.rendered.index, -1); assert.equal(result.normalized_mismatches.structural.index, -1);
  assert.equal(result.normalized_mismatches.inline_code, null);
  assert.doesNotMatch(JSON.stringify(result), /schema|hidden-|https:|messageId|structuredText|inlineCodeText/);
  for (const mismatch of Object.values(result.normalized_mismatches).filter(Boolean)) {
    assert.ok(mismatch.expected_codepoints.length <= 5); assert.ok(mismatch.actual_codepoints.length <= 5);
    assert.ok([...mismatch.expected_codepoints, ...mismatch.actual_codepoints].every(Number.isInteger));
  }
});
