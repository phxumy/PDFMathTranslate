import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import { connectToRemoteChromeTarget } from './node_modules/@steipete/oracle/dist/src/browser/chromeLifecycle.js';
import { submitPrompt, waitForAssistantResponse, ensurePromptReady } from './node_modules/@steipete/oracle/dist/src/browser/pageActions.js';
import { browserPromptFingerprint, readUserMessageIds, readSubmittedPromptFingerprint } from './node_modules/@steipete/oracle/dist/src/browser/promptFingerprint.js';
import { buildConversationTurnCountExpression } from './node_modules/@steipete/oracle/dist/src/browser/conversationTurns.js';
import * as nativeUI from './native-ui.mjs';
import { browserEndpoint, CONNECTION_GUIDANCE } from './endpoint.mjs';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = value => createHash('sha256').update(value).digest('hex');
const normalizedPrompt = value => String(value).replace(/\r\n?/g, '\n').replace(/\s+/g, ' ').trim();
const coded = (code, message, extra = {}) => Object.assign(new Error(message), { code, ...extra });
const alive = pid => { if (!Number.isInteger(pid) || pid <= 0) return false; try { process.kill(pid, 0); return true; } catch { return false; } };
const quiet = () => {};
async function readJson(filename, fallback = null) {
  try { return JSON.parse(await fs.readFile(filename, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}
async function writeJson(filename, value) {
  await fs.mkdir(path.dirname(filename), { recursive: true });
  const temp = `${filename}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  await fs.rename(temp, filename);
}
function localPath(value, field) {
  if (!value || !path.isAbsolute(value)) throw coded('INVALID_REQUEST', `${field} must be an absolute local path.`);
  return path.resolve(value);
}
async function readJournal(directory) {
  const absent = Symbol();
  let journal;
  try { journal = await readJson(path.join(directory, 'journal.json'), absent); }
  catch { throw coded('JOURNAL_UNREADABLE', '本批任务记录损坏或无法读取，已暂停以避免重复提交。'); }
  if (journal === absent) return null;
  if (!journal || typeof journal !== 'object' || Array.isArray(journal) || typeof journal.dispatch_started !== 'boolean') {
    throw coded('JOURNAL_UNREADABLE', '本批任务记录不完整，无法确认是否提交；已暂停以避免重复提交。');
  }
  return journal;
}
function identity(journal) {
  return { session_id: journal.session_id || null, conversation_url: journal.conversation_url || null };
}
function conversationURL(value) {
  try { const url = new URL(value); return url.origin === 'https://chatgpt.com' && /^\/c\/[a-zA-Z0-9-]+$/.test(url.pathname) && !url.search && !url.hash; } catch { return false; }
}
async function evaluation(runtime, expression) {
  const result = await runtime.evaluate({ expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw coded('PAGE_SCRIPT_ERROR', 'ChatGPT 页面状态读取失败。');
  return result.result?.value;
}
async function userTurn(runtime) {
  return evaluation(runtime, `(() => {
    const users = [...document.querySelectorAll('[data-message-author-role="user"]')];
    const last = users.at(-1);
    return { url: location.href, count: users.length, messageId: last?.getAttribute('data-message-id'), text: last?.textContent };
  })()`);
}

// Oracle's connection helper reconnects when its shared WebSocket cache is empty.
// After explicit login, bind new owned page sessions to the existing transport
// directly: an interrupted socket must fail instead of asking for authorization.
export async function connectOwnedPage(anchor, targetId) {
  const root = anchor.client;
  const { sessionId } = await root.Target.attachToTarget({ targetId, flatten: true });
  let closed = false;
  const client = { oraclePageSessionId: sessionId };
  for (const domain of ['Runtime', 'Page', 'Input', 'DOM', 'Network', 'Emulation']) {
    client[domain] = new Proxy({}, { get: (_target, method) => params => {
      if (closed) return Promise.reject(coded('PAGE_CLOSED', '翻译标签页连接已关闭。'));
      return root.send(`${domain}.${String(method)}`, params || {}, sessionId);
    } });
  }
  return { client, targetId, close: async () => {
    if (closed) return;
    closed = true;
    await root.Target.detachFromTarget({ sessionId }).catch(() => {});
  } };
}

// Dependency injection keeps lifecycle and no-resubmission tests entirely offline.
export function createBridge(overrides = {}) {
  const deps = { discover: browserEndpoint, attach: connectToRemoteChromeTarget, scopedAttach: connectOwnedPage, ui: nativeUI,
    submit: submitPrompt, waitAnswer: waitForAssistantResponse, promptReady: ensurePromptReady,
    readUserIds: readUserMessageIds, readFingerprint: readSubmittedPromptFingerprint, userTurn,
    recoveryNow: Date.now, recoveryPause: pause,
    ...overrides };
  const connections = new Map();
  const pending = new Map();
  const active = new Map();

  function connected(stateDir) {
    const entry = connections.get(stateDir);
    if (entry?.anchor?.client?._ws && entry.anchor.client._ws.readyState !== 1) entry.authorized = false;
    if (!entry?.authorized) throw coded('CONNECTION_REQUIRED', CONNECTION_GUIDANCE, { status: 'needs_connection' });
    return entry;
  }
  async function saveOwners(entry) {
    await writeJson(path.join(entry.stateDir, 'native-browser.json'), { version: 2,
      browser_ws: entry.endpoint.browserWSEndpoint, owned_targets: [...entry.owned], ui_tab: entry.uiTarget || null });
  }
  async function attachOwned(entry, target) {
    if (!entry.authorized || !entry.owned.has(target)) throw coded('TAB_NOT_OWNED', '该标签页不属于翻译软件，已拒绝访问。');
    return deps.scopedAttach(entry.anchor, target);
  }
  async function newOwned(entry, url = 'https://chatgpt.com/', login = false) {
    if (!entry.authorized && !login) throw coded('CONNECTION_REQUIRED', CONNECTION_GUIDANCE, { status: 'needs_connection' });
    let connection;
    if (login) {
      connection = await deps.attach(entry.endpoint.host, entry.endpoint.port, quiet, {
        browserWSEndpoint: entry.endpoint.browserWSEndpoint, targetUrl: 'about:blank', closeTargetOnDispose: false,
        approvalWaitMs: 180000,
      });
    } else {
      const created = await entry.anchor.client.Target.createTarget({ url: 'about:blank', background: true });
      entry.owned.add(created.targetId);
      await saveOwners(entry);
      connection = await deps.scopedAttach(entry.anchor, created.targetId);
    }
    entry.authorized = true;
    entry.owned.add(connection.targetId);
    await saveOwners(entry); // Never navigate before registering ownership.
    const page = deps.ui.createNativePage(connection.client);
    await page.goto(url);
    return connection;
  }
  async function withConnection(connection, action) {
    try { return await action(deps.ui.createNativePage(connection.client), connection); }
    finally { await connection.close({ preserveTarget: true }).catch(() => {}); }
  }
  async function setupConnection(entry, create) {
    if (entry.uiTarget && entry.owned.has(entry.uiTarget)) {
      try { return await attachOwned(entry, entry.uiTarget); }
      catch { if (!create) return null; }
    }
    if (!create) return null;
    const connection = await newOwned(entry);
    entry.uiTarget = connection.targetId;
    await saveOwners(entry);
    return connection;
  }
  async function login(request) {
    let entry = connections.get(request.state_dir);
    if (!entry?.authorized) {
      const endpoint = await deps.discover(request.state_dir);
      const saved = await readJson(path.join(request.state_dir, 'native-browser.json')).catch(() => null);
      const sameBrowser = saved?.version === 2 && saved.browser_ws === endpoint.browserWSEndpoint;
      entry = { stateDir: request.state_dir, endpoint, authorized: false,
        owned: new Set(sameBrowser && Array.isArray(saved.owned_targets) ? saved.owned_targets : []),
        uiTarget: sameBrowser ? saved.ui_tab : null };
      // Only explicit login may open a browser WebSocket. A saved target may be
      // resumed only when its browser WebSocket identity is unchanged.
      let first;
      if (entry.uiTarget && entry.owned.has(entry.uiTarget)) {
        try {
          first = await deps.attach(endpoint.host, endpoint.port, quiet, {
            browserWSEndpoint: endpoint.browserWSEndpoint, targetId: entry.uiTarget,
            closeTargetOnDispose: false, approvalWaitMs: 180000,
          });
          entry.authorized = true;
        } catch (error) {
          if (!/No target|target.*(?:not found|closed|does not exist)/i.test(error.message || '')) throw error;
        }
      }
      if (!first) first = await newOwned(entry, 'https://chatgpt.com/', true);
      entry.uiTarget = first.targetId;
      await saveOwners(entry);
      // Retain one page session so the approved shared browser connection stays
      // available to Oracle's session-scoped helpers in this broker process.
      entry.anchor = first;
      first.client.on?.('disconnect', () => { entry.authorized = false; });
      first.client._ws?.on?.('close', () => { entry.authorized = false; });
      connections.set(request.state_dir, entry);
      return deps.ui.prepareLoginPage(deps.ui.createNativePage(first.client));
    }
    const connection = await setupConnection(entry, true);
    return withConnection(connection, page => deps.ui.prepareLoginPage(page));
  }

  async function migrateLegacy(request, journal) {
    if (journal.version >= 2) return;
    const base = path.join(request.state_dir, 'oracle/sessions');
    const names = journal.session_id ? [journal.session_id] :
      (await fs.readdir(base).catch(() => [])).filter(name => journal.slug && (name === journal.slug || name.startsWith(`${journal.slug}-`)));
    for (const name of names) {
      if (path.basename(name) !== name) continue;
      const meta = await readJson(path.join(base, name, 'meta.json')).catch(() => null);
      if (!meta) continue;
      journal.session_id = name;
      const runtime = meta.browser?.runtime;
      const url = runtime?.tabUrl || runtime?.conversationUrl || meta.browser?.harvest?.url;
      if (conversationURL(url)) journal.conversation_url = url;
      journal.submitted_prompt_hash = runtime?.submittedPromptHash || journal.submitted_prompt_hash;
      break;
    }
  }
  async function completed(request, journal) {
    if (!journal.output_file || !path.isAbsolute(journal.output_file)) return null;
    const answer = await fs.readFile(journal.output_file, 'utf8').catch(() => '');
    if (!answer.trim()) return null;
    // Legacy detached CLI writes are not atomic; do not read its partial output.
    if (journal.version < 2) {
      if (alive(journal.oracle_pid)) return null;
      await pause(120);
      if (await fs.readFile(journal.output_file, 'utf8').catch(() => '') !== answer) return null;
    } else if (journal.status !== 'complete') return null;
    journal.status = 'complete';
    await writeJson(path.join(request.request_dir, 'journal.json'), journal);
    return { ok: true, status: 'complete', answer, model: journal.model, effort: journal.effort, ...identity(journal) };
  }
  async function persistAnswer(request, journal, answer) {
    if (typeof answer !== 'string' || !answer.trim()) throw coded('EMPTY_RESPONSE', 'ChatGPT 没有返回可用译文。原批次已保留，不会重发。');
    const temp = `${journal.output_file}.${process.pid}.tmp`;
    await fs.mkdir(path.dirname(journal.output_file), { recursive: true });
    await fs.writeFile(temp, answer, { mode: 0o600 });
    await fs.rename(temp, journal.output_file);
    journal.status = 'complete';
    await writeJson(path.join(request.request_dir, 'journal.json'), journal);
    return completed(request, journal);
  }
  async function capture(request, journal, connection, save) {
    const Runtime = connection.client.Runtime;
    const before = await deps.userTurn(Runtime);
    if (conversationURL(before.url)) journal.conversation_url = before.url;
    const matches = before.messageId && typeof before.text === 'string' && journal.submitted_prompt_hash &&
      browserPromptFingerprint(before.text, before.messageId) === journal.submitted_prompt_hash &&
      (!journal.prompt_normalized_hash || (before.count === 1 && hash(normalizedPrompt(before.text)) === journal.prompt_normalized_hash));
    if (!matches) throw coded('RECOVERY_IDENTITY_UNVERIFIED', '无法确认该页面最后一条请求属于本批次，已暂停；不会重发或读取其他会话的回答。');
    await save();
    const conversationId = journal.conversation_url?.split('/').at(-1);
    const answer = await deps.waitAnswer(Runtime, (request.timeout_seconds || 900) * 1000, quiet, journal.baseline_turns ?? 0, conversationId);
    const after = await deps.userTurn(Runtime);
    if (after.messageId !== before.messageId || browserPromptFingerprint(after.text, after.messageId) !== journal.submitted_prompt_hash ||
        (journal.conversation_url && after.url !== journal.conversation_url)) {
      throw coded('RECOVERY_IDENTITY_UNVERIFIED', '等待回答时页面会话发生变化，已暂停。');
    }
    return persistAnswer(request, journal, answer.text);
  }
  async function waitForRecoveryIdentity(request, journal, page, runtime) {
    const deadline = deps.recoveryNow() + Math.min(30000, Math.max(1, request.timeout_seconds || 900) * 1000);
    let lastModeError;
    let sawUserTurn = false;
    do {
      let turn;
      try { turn = await deps.userTurn(runtime); }
      catch (error) {
        // A navigation can briefly destroy the old execution context. Other
        // transport failures must escape immediately, never reconnect or resend.
        if (!/Cannot find (?:default )?(?:execution )?context|Execution context (?:was destroyed|is not available)/i.test(error.message || '')) throw error;
      }
      if (turn) {
        if (journal.conversation_url && conversationURL(turn.url) && turn.url !== journal.conversation_url) {
          throw coded('RECOVERY_IDENTITY_UNVERIFIED', '恢复页面跳转到了其他会话，已暂停；不会读取该会话的回答。');
        }
        const matchingUrl = !journal.conversation_url || turn.url === journal.conversation_url;
        sawUserTurn ||= Boolean(turn.messageId);
        const matchingTurn = matchingUrl && turn.messageId && typeof turn.text === 'string' &&
          browserPromptFingerprint(turn.text, turn.messageId) === journal.submitted_prompt_hash &&
          (!journal.prompt_normalized_hash || (turn.count === 1 && hash(normalizedPrompt(turn.text)) === journal.prompt_normalized_hash));
        if (matchingTurn) {
          try {
            // Sidebar/mode controls may hydrate after the message and composer.
            // Only a still-unverified mode is retried; auth/challenge errors stop.
            await deps.ui.ensureChat(page);
            return;
          } catch (error) {
            if (error.code !== 'CHAT_MODE_UNVERIFIED') throw error;
            lastModeError = error;
          }
        }
      }
      const remaining = deadline - deps.recoveryNow();
      if (remaining <= 0) break;
      await deps.recoveryPause(Math.min(250, remaining));
    } while (deps.recoveryNow() <= deadline);
    if (lastModeError) throw lastModeError;
    if (sawUserTurn) throw coded('RECOVERY_IDENTITY_UNVERIFIED', '已等待原会话加载，但仍无法确认最后一条请求属于本批次；本批不会重发。');
    throw coded('RECOVERY_NOT_READY', '原会话的已提交消息尚未加载完成，请稍后恢复同一文件；本批不会重发。');
  }
  async function recover(request) {
    const journal = await readJournal(request.request_dir);
    if (!journal?.dispatch_started) throw coded('NOT_SUBMITTED', '上次请求尚未提交，可以继续。', { not_submitted: true });
    await migrateLegacy(request, journal);
    const ready = await completed(request, journal);
    if (ready) return ready;
    if (journal.version < 2 && alive(journal.oracle_pid)) throw coded('BROWSER_BUSY', '旧版翻译进程仍在收取原回答，请等待后继续原文件；本批没有重发。');
    if (!journal.submitted_prompt_hash) throw coded('RECOVERY_UNAVAILABLE', '本批已开始提交，但缺少可核验的请求记录。已保留原任务，不会再次发送。');
    const entry = connected(request.state_dir);
    let connection;
    if (journal.browser_ws === entry.endpoint.browserWSEndpoint && entry.owned.has(journal.target_id)) {
      connection = await attachOwned(entry, journal.target_id).catch(() => null);
    }
    if (!connection && conversationURL(journal.conversation_url)) connection = await newOwned(entry, journal.conversation_url);
    if (!connection) throw coded('RECOVERY_UNAVAILABLE', '未能找回本批的软件标签页或已保存会话地址，已暂停以避免重复提交。');
    journal.target_id = connection.targetId;
    journal.browser_ws = entry.endpoint.browserWSEndpoint;
    const save = () => writeJson(path.join(request.request_dir, 'journal.json'), journal);
    return withConnection(connection, async page => {
      await deps.promptReady(connection.client.Runtime, 30000, quiet);
      await waitForRecoveryIdentity(request, journal, page, connection.client.Runtime);
      return capture(request, journal, connection, save);
    });
  }
  async function translate(request) {
    const existing = await readJournal(request.request_dir);
    if (existing?.dispatch_started) return recover(request);
    const entry = connected(request.state_dir);
    const prompt = await fs.readFile(localPath(request.prompt_file, 'prompt_file'), 'utf8');
    if (!prompt.trim()) throw coded('INVALID_REQUEST', 'Empty translation prompt.');
    const output = localPath(request.output_file, 'output_file');
    const prior = await readJson(path.join(request.state_dir, 'active-request.json'));
    if (prior?.request_dir && prior.request_dir !== request.request_dir) {
      const legacy = await readJournal(prior.request_dir);
      if (legacy?.dispatch_started && legacy.status !== 'complete' && alive(legacy.oracle_pid)) throw coded('BROWSER_BUSY', '上一批旧版翻译仍在运行，请先恢复原文件。');
    }
    const connection = await newOwned(entry);
    return withConnection(connection, async page => {
      const { Runtime, Input } = connection.client;
      await deps.promptReady(Runtime, 30000, quiet);
      const selection = await deps.ui.catalog(page, request.model || undefined, request.effort || undefined);
      await deps.ui.ensureChat(page);
      const previousIds = await deps.readUserIds(Runtime, 5000);
      const baseline = await evaluation(Runtime, buildConversationTurnCountExpression());
      // Every submission uses an empty software-owned chat. A user moving that
      // tab to another conversation must never append a translation there.
      if (!Array.isArray(previousIds) || previousIds.length || baseline !== 0) throw coded('NEW_CHAT_REQUIRED', '翻译标签页不是空白新聊天，请重新连接后重试。');
      const journal = { version: 2, transport: 'native-page', status: 'dispatching', dispatch_started: true,
        created_at: new Date().toISOString(), session_id: `pdf-${randomUUID()}`, target_id: connection.targetId,
        browser_ws: entry.endpoint.browserWSEndpoint, port: entry.endpoint.port, prompt_hash: hash(prompt), prompt_normalized_hash: hash(normalizedPrompt(prompt)),
        output_file: output, model: selection.model, effort: selection.effort, baseline_turns: baseline, prompt_submitted: false };
      const save = () => writeJson(path.join(request.request_dir, 'journal.json'), journal);
      await save(); // A crash from here is ambiguous: never resubmit.
      await writeJson(path.join(request.state_dir, 'active-request.json'), { request_dir: request.request_dir, controller_pid: process.pid });
      const rememberSubmission = async timeout => {
        const fingerprint = await deps.readFingerprint(Runtime, previousIds, timeout);
        const current = await deps.userTurn(Runtime);
        if (current.count !== 1 || typeof current.text !== 'string' || !current.messageId ||
            hash(normalizedPrompt(current.text)) !== journal.prompt_normalized_hash ||
            browserPromptFingerprint(current.text, current.messageId) !== fingerprint ||
            browserPromptFingerprint(prompt, current.messageId) !== fingerprint) {
          throw coded('RECOVERY_IDENTITY_UNVERIFIED', '网页上的已发送文字与本批翻译不匹配，已暂停且不会重发。');
        }
        journal.submitted_prompt_hash = fingerprint;
        if (conversationURL(current.url)) journal.conversation_url = current.url;
      };
      try {
        // Use only Oracle's page-bound helpers. runBrowserMode performs global
        // cookie cleanup and tab discovery; neither belongs in a shared browser.
        // This call is intentionally never wrapped in a submission retry.
        // No Page argument: Oracle otherwise calls bringToFront for each batch.
        await deps.submit({ runtime: Runtime, input: Input, baselineTurns: baseline,
          inputTimeoutMs: 30000, attachmentNames: [], webSearch: false,
          onPromptSubmitted: async () => { journal.prompt_submitted = true; journal.status = 'running'; await save(); },
        }, prompt, quiet);
        await rememberSubmission(30000);
        await save();
        return await capture(request, journal, connection, save);
      } catch (error) {
        // The callback may run before the user turn commits. Harvest its identity
        // after a failed wait when possible, but never issue another submission.
        if (!journal.submitted_prompt_hash) await rememberSubmission(0).catch(() => {});
        const current = await deps.userTurn(Runtime).catch(() => null);
        if (conversationURL(current?.url)) journal.conversation_url = current.url;
        journal.status = 'paused';
        await save();
        throw error;
      }
    });
  }
  async function dispatch(request) {
    if (!request || typeof request !== 'object') throw coded('INVALID_REQUEST', 'JSON request object required.');
    request.state_dir = localPath(request.state_dir, 'state_dir');
    if (['translate', 'recover'].includes(request.op)) request.request_dir = localPath(request.request_dir, 'request_dir');
    const ongoing = active.get(request.state_dir);
    if (ongoing) {
      if (ongoing === request.request_dir && pending.has(ongoing)) return pending.get(ongoing);
      throw coded('BROWSER_BUSY', '上一批仍在浏览器中运行或收取回答，请先继续原文件。');
    }
    if (request.op === 'login') return login(request);
    if (['translate', 'recover'].includes(request.op)) {
      active.set(request.state_dir, request.request_dir);
      const work = (request.op === 'translate' ? translate(request) : recover(request));
      pending.set(request.request_dir, work);
      try { return await work; }
      finally { pending.delete(request.request_dir); active.delete(request.state_dir); }
    }
    if (!['status', 'catalog', 'select'].includes(request.op)) throw coded('INVALID_REQUEST', 'Unknown operation.');
    const entry = connected(request.state_dir);
    const connection = await setupConnection(entry, request.op !== 'status');
    if (!connection) return { ok: true, status: 'needs_login', message: '翻译软件的标签页已关闭，请点击“连接已登录的 Chrome”重新打开。' };
    return withConnection(connection, async page => {
      if (request.op === 'status') return deps.ui.connectionStatus(await deps.ui.inspectPage(page));
      const result = await deps.ui.catalog(page, request.model || undefined, request.effort || undefined);
      return { ok: true, status: 'ready', message: '已从已登录账号读取可用模型和推理强度。', ...result };
    });
  }
  async function run(request) {
    try { return await dispatch({ ...request }); }
    catch (error) {
      let unreadable = false;
      const journal = request?.request_dir ? await readJournal(request.request_dir).catch(() => { unreadable = true; return null; }) : null;
      return { ok: false, status: error.status || 'paused', error_code: error.code || 'BROWSER_ERROR', message: error.message,
        ...(error.diagnostic ? { diagnostic: error.diagnostic } : {}),
        ...(journal ? identity(journal) : {}),
        not_submitted: !unreadable && (error.not_submitted === true || (['translate', 'recover'].includes(request?.op) && !journal?.dispatch_started)) };
    }
  }
  return { run };
}

export const run = createBridge().run;

async function main() {
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    const request = JSON.parse(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/, ''));
    const { invokeBroker } = await import('./broker.mjs');
    process.stdout.write(`${JSON.stringify(await invokeBroker(request))}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ ok: false, status: 'paused', error_code: error.code || 'BROKER_ERROR', message: error.message, not_submitted: false })}\n`);
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
