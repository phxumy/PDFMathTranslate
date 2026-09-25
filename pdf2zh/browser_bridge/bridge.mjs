import fs from 'node:fs/promises';
import { openSync, closeSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { chromium } from 'playwright-core';
import { catalog, coded, connectionStatus, ensureChat, inspectPage, prepareLoginPage } from './ui.mjs';
import { browserEndpoint } from './endpoint.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ORACLE = path.join(HERE, 'node_modules', '@steipete', 'oracle');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const alive = pid => { if (!Number.isInteger(pid) || pid <= 0) return false; try { process.kill(pid, 0); return true; } catch { return false; } };
const hash = value => createHash('sha256').update(value).digest('hex');
let browser;
let dispatchLock;

async function readJson(filename, fallback = null) {
  try { return JSON.parse(await fs.readFile(filename, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return fallback; throw error; }
}
async function readJournal(requestDir) {
  const missing = Symbol('missing-journal');
  let journal;
  try { journal = await readJson(path.join(requestDir, 'journal.json'), missing); }
  catch { throw coded('JOURNAL_UNREADABLE', '本批任务记录损坏或无法读取，已暂停以避免重复提交。'); }
  if (journal === missing) return null;
  if (!journal || typeof journal !== 'object' || Array.isArray(journal) || typeof journal.dispatch_started !== 'boolean') {
    throw coded('JOURNAL_UNREADABLE', '本批任务记录不完整，无法确认是否提交；已暂停以避免重复提交。');
  }
  return journal;
}
async function writeJson(filename, value) {
  await fs.mkdir(path.dirname(filename), { recursive: true });
  const temp = `${filename}.${process.pid}.tmp`;
  await fs.writeFile(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  await fs.rename(temp, filename);
}
function assertLocalPath(value, field) {
  if (!value || !path.isAbsolute(value)) throw coded('INVALID_REQUEST', `${field} must be an absolute local path.`);
  return path.resolve(value);
}
function chromeCandidates(request) {
  return [request.chrome_path, process.env.PDF2ZH_CHATGPT_CHROME_PATH,
    path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe'),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium',
  ].filter(Boolean);
}
async function connect(stateDir) {
  const live = await browserEndpoint(stateDir);
  if (!live) throw coded('BROWSER_CLOSED', '翻译专用浏览器已关闭。请点“登录 / 打开浏览器”，然后继续原文件以恢复；不会自动重发。');
  browser = await chromium.connectOverCDP(live.endpoint, { timeout: 10000 });
  return { ...live, context: browser.contexts()[0] };
}
async function targetId(page) {
  const session = await page.context().newCDPSession(page);
  try { return (await session.send('Target.getTargetInfo')).targetInfo.targetId; }
  finally { await session.detach(); }
}
async function findOwnedTab(context, id) {
  if (!id) return null;
  for (const page of context.pages()) {
    if (await targetId(page).catch(() => null) === id) return page;
  }
  return null;
}
export function setupTabCandidate(pages) {
  const candidates = pages.filter(page => {
    try {
      const url = new URL(page.url());
      return url.protocol === 'https:' && (['auth.openai.com', 'auth0.openai.com'].includes(url.hostname) ||
        (url.hostname === 'chatgpt.com' && url.pathname === '/'));
    } catch { return false; }
  });
  return candidates.length === 1 ? candidates[0] : null;
}
export async function uiTab(stateDir, context, create = true) {
  const statePath = path.join(stateDir, 'browser.json');
  const state = await readJson(statePath, {});
  let page = await findOwnedTab(context, state.ui_tab);
  if (!page) {
    // A restarted browser changes target IDs. Only a unique setup/login page in
    // our dedicated profile can be adopted, never an existing /c/ conversation.
    page = setupTabCandidate(context.pages());
    if (page && create) await writeJson(statePath, { ...state, ui_tab: await targetId(page) });
  }
  if (!page && create) {
    page = await context.newPage();
    await writeJson(statePath, { ...state, ui_tab: await targetId(page) });
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  }
  return page;
}
async function login(request) {
  if (!await browserEndpoint(request.state_dir)) {
    const chrome = chromeCandidates(request).find(existsSync);
    if (!chrome) throw coded('CHROME_NOT_FOUND', '未找到 Google Chrome。请先安装 Chrome 或设置 PDF2ZH_CHATGPT_CHROME_PATH。');
    const profile = path.join(request.state_dir, 'chrome-profile');
    await fs.mkdir(profile, { recursive: true });
    const child = spawn(chrome, [`--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--no-first-run', '--no-default-browser-check', '--disable-background-mode', '--new-window', 'about:blank'], {
      detached: true, stdio: 'ignore', windowsHide: false,
    });
    let launchError;
    child.on('error', error => { launchError = error; });
    child.unref();
    for (let i = 0; i < 50 && !await browserEndpoint(request.state_dir); i++) {
      if (launchError) throw launchError;
      await pause(200);
    }
  }
  const { context } = await connect(request.state_dir);
  const page = await uiTab(request.state_dir, context);
  return prepareLoginPage(page);
}

function oracleEnvironment(stateDir) {
  // A user's unrelated Oracle configuration and API keys must never select another transport.
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^ORACLE_/.test(key) || /^(OPENAI|AZURE|ANTHROPIC|GEMINI|GOOGLE|OPENROUTER).*API_KEY$/.test(key)) delete env[key];
  }
  return { ...env, ORACLE_HOME_DIR: path.join(stateDir, 'oracle'), ORACLE_ENGINE: 'browser', NO_COLOR: '1', FORCE_COLOR: '0' };
}

export function oracleArguments({ port, target, output, slug, timeout = 900 }) {
  return [path.join(ORACLE, 'dist/bin/oracle-cli.js'), '--engine', 'browser', '--model', 'gpt-5.5',
    '--remote-chrome', `127.0.0.1:${port}`, '--browser-tab', target, '--browser-model-strategy', 'current',
    '--browser-attachments', 'never', '--browser-research', 'off', '--browser-archive', 'never',
    '--no-browser-capture-provider-native', '--browser-keep-browser', '--browser-timeout', `${timeout}s`,
    '--browser-input-timeout', '30s', '--browser-recheck-delay', '0', '--browser-auto-reattach-delay', '0',
    '--wait', '--slug', slug, '--write-output', output, '--prompt', '-'];
}

async function sessionMetadata(stateDir, journal) {
  const base = path.join(stateDir, 'oracle/sessions');
  const entries = await fs.readdir(base).catch(() => []);
  // Exact known session first; otherwise only the unique request slug (never other chats).
  const names = journal.session_id ? [journal.session_id] : entries.filter(name => name === journal.slug || name.startsWith(`${journal.slug}-`));
  for (const name of names) {
    const meta = await readJson(path.join(base, name, 'meta.json')).catch(() => null);
    if (meta) return { ...meta, id: name };
  }
  return null;
}
function identityFields(journal) {
  return { session_id: journal.session_id || null, conversation_url: journal.conversation_url || null };
}
async function updateSession(request, journal) {
  const meta = await sessionMetadata(request.state_dir, journal);
  if (meta) {
    journal.session_id = meta.id;
    const url = meta.browser?.runtime?.tabUrl || meta.browser?.runtime?.conversationUrl || meta.browser?.harvest?.url;
    if (isConversationUrl(url)) journal.conversation_url = url;
    journal.submitted_prompt_hash = meta.browser?.runtime?.submittedPromptHash ?? journal.submitted_prompt_hash;
    journal.oracle_status = meta.status;
  }
  return meta;
}
function isConversationUrl(value) {
  try { const url = new URL(value); return url.origin === 'https://chatgpt.com' && /^\/c\/[a-zA-Z0-9-]+$/.test(url.pathname); } catch { return false; }
}
async function completedFile(request, journal) {
  const answer = await fs.readFile(journal.output_file, 'utf8').catch(() => '');
  if (!answer.trim()) return null;
  await pause(120);
  if (await fs.readFile(journal.output_file, 'utf8').catch(() => '') !== answer) return null;
  journal.status = 'complete';
  await writeJson(path.join(request.request_dir, 'journal.json'), journal);
  return { ok: true, status: 'complete', answer, ...identityFields(journal), model: journal.model, effort: journal.effort };
}

async function awaitOracle(request, journal) {
  const deadline = Date.now() + (request.timeout_seconds || 900) * 1000;
  const journalPath = path.join(request.request_dir, 'journal.json');
  while (Date.now() < deadline) {
    await updateSession(request, journal);
    const result = await completedFile(request, journal);
    if (result) return result;
    await writeJson(journalPath, journal);
    if (!await browserEndpoint(request.state_dir)) {
      throw Object.assign(coded('BROWSER_CLOSED', '专用浏览器已关闭；本批可能已提交。重新打开专用浏览器后继续原文件，将先读取原会话，不会重发。'), identityFields(journal));
    }
    if (!alive(journal.oracle_pid)) break;
    await pause(900);
  }
  await updateSession(request, journal);
  await writeJson(journalPath, journal);
  const result = await completedFile(request, journal);
  if (result) return result;
  throw Object.assign(coded('RECOVERY_REQUIRED', '本批尚未取得完整回答，已保存进度。请继续原文件恢复读取；不会自动重复提交。'), identityFields(journal));
}

async function translate(request) {
  request.request_dir = assertLocalPath(request.request_dir, 'request_dir');
  const journalPath = path.join(request.request_dir, 'journal.json');
  const existing = await readJournal(request.request_dir);
  if (existing?.dispatch_started) return recover(request);
  const activePath = path.join(request.state_dir, 'active-request.json');
  const lockPath = path.join(request.state_dir, 'dispatch.lock');
  try {
    const fd = await fs.open(lockPath, 'wx');
    await fd.writeFile(JSON.stringify({ pid: process.pid }));
    await fd.close();
    dispatchLock = lockPath;
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const lock = await readJson(lockPath).catch(() => ({}));
    if (lock.pid && !alive(lock.pid)) {
      await fs.unlink(lockPath);
      return translate(request);
    }
    throw coded('BROWSER_BUSY', '翻译浏览器已有操作正在进行，请等待该操作完成。');
  }
  const active = await readJson(activePath);
  if (active?.request_dir !== request.request_dir && active?.request_dir) {
    const prior = await readJson(path.join(active.request_dir, 'journal.json'));
    if (prior?.status !== 'complete' && (alive(prior?.oracle_pid) || alive(active.controller_pid))) {
      throw coded('BROWSER_BUSY', '上一批仍在专用浏览器中运行或收取回答。请先继续原文件恢复，完成后再开始其他翻译。');
    }
  }
  await writeJson(activePath, { request_dir: request.request_dir, controller_pid: process.pid });
  const promptFile = assertLocalPath(request.prompt_file, 'prompt_file');
  const outputFile = assertLocalPath(request.output_file, 'output_file');
  const prompt = await fs.readFile(promptFile, 'utf8');
  if (!prompt.trim()) throw coded('INVALID_REQUEST', 'Empty translation prompt.');
  const { context, port } = await connect(request.state_dir);
  const page = await context.newPage();
  await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.locator('#prompt-textarea,textarea[name="prompt-textarea"]').first().waitFor({ state: 'visible', timeout: 30000 });
  const options = await catalog(page, request.model || undefined, request.effort || undefined);
  await ensureChat(page);
  const target = await targetId(page);
  const requestToken = hash(request.request_id || `${request.request_dir}:${randomUUID()}`).slice(0, 16);
  const journal = { version: 1, status: 'dispatching', dispatch_started: true, created_at: new Date().toISOString(),
    slug: `pdf-${requestToken.slice(0, 8)}-${requestToken.slice(8)}`, target_id: target, port,
    prompt_hash: hash(prompt), output_file: outputFile, model: options.model, effort: options.effort };
  await fs.mkdir(path.dirname(outputFile), { recursive: true });
  await fs.mkdir(path.join(request.state_dir, 'oracle'), { recursive: true });
  // Persist before spawn; a crash from here is ambiguous and must NEVER send again.
  await writeJson(journalPath, journal);
  const log = openSync(path.join(request.request_dir, 'oracle.log'), 'a');
  let child;
  try {
    child = spawn(process.execPath, oracleArguments({ port, target, output: outputFile, slug: journal.slug, timeout: request.timeout_seconds || 900 }), {
      cwd: request.state_dir, env: oracleEnvironment(request.state_dir), detached: true,
      stdio: ['pipe', log, log], windowsHide: true,
    });
    await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
    journal.oracle_pid = child.pid;
    journal.status = 'running';
    await writeJson(journalPath, journal);
    child.stdin.on('error', () => {});
    child.stdin.end(prompt);
    child.unref();
  } catch (error) {
    // Only spawn failure proves that the prompt has never left this process.
    if (!child?.pid) {
      journal.dispatch_started = false;
      journal.status = 'not_submitted';
      await writeJson(journalPath, journal);
      throw Object.assign(error, { not_submitted: true });
    }
    throw error;
  } finally { closeSync(log); }
  return awaitOracle(request, journal);
}

async function recover(request) {
  request.request_dir = assertLocalPath(request.request_dir, 'request_dir');
  const journalPath = path.join(request.request_dir, 'journal.json');
  const journal = await readJournal(request.request_dir);
  if (!journal?.dispatch_started) throw Object.assign(coded('NOT_SUBMITTED', '上次请求尚未提交，可以继续。'), { not_submitted: true });
  await updateSession(request, journal);
  const ready = await completedFile(request, journal);
  if (ready) return ready;
  const { context, port } = await connect(request.state_dir);
  if (journal.oracle_pid && alive(journal.oracle_pid) && journal.port === port) return awaitOracle(request, journal);
  let page = await findOwnedTab(context, journal.target_id);
  if (!page && isConversationUrl(journal.conversation_url)) {
    page = await context.newPage();
    await page.goto(journal.conversation_url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(1200);
    journal.target_id = await targetId(page);
  }
  if (!page) throw Object.assign(coded('RECOVERY_UNAVAILABLE', '未能找回已提交批次的会话地址。为避免重复扣额，本批没有重发；详情保存在本地 oracle.log。'), identityFields(journal));
  const { harvestChatGptTab } = await import(pathToFileURL(path.join(ORACLE, 'dist/src/browser/liveTabs.js')).href);
  const { browserPromptFingerprint } = await import(pathToFileURL(path.join(ORACLE, 'dist/src/browser/promptFingerprint.js')).href);
  const deadline = Date.now() + (request.timeout_seconds || 900) * 1000;
  while (Date.now() < deadline) {
    const harvested = await harvestChatGptTab({ host: '127.0.0.1', port, ref: journal.target_id });
    if (isConversationUrl(harvested.url)) journal.conversation_url = harvested.url;
    const answer = harvested.lastAssistantMarkdown || harvested.lastAssistantText;
    const matching = journal.submitted_prompt_hash && harvested.lastUserMessageId &&
      [harvested.lastUserTextRaw, harvested.lastUserContentText].some(text => typeof text === 'string' && browserPromptFingerprint(text, harvested.lastUserMessageId) === journal.submitted_prompt_hash);
    if (!harvested.stopExists && harvested.assistantFollowsLatestUser && answer?.trim() && matching) {
      await fs.writeFile(journal.output_file, answer, { mode: 0o600 });
      return completedFile(request, journal);
    }
    if (!harvested.authenticated || harvested.loginButtonExists) throw coded('LOGIN_REQUIRED', '登录已失效，请重新登录后恢复原批次。');
    if (!harvested.stopExists && answer?.trim() && !matching) throw Object.assign(coded('RECOVERY_IDENTITY_UNVERIFIED', '原回答与本批的对应关系无法确认，已暂停；不会重发或采用其他会话的回答。'), identityFields(journal));
    await writeJson(journalPath, journal);
    await pause(1500);
  }
  throw Object.assign(coded('RECOVERY_REQUIRED', '原会话仍未完成，请稍后继续恢复。'), identityFields(journal));
}

export async function run(request) {
  request.state_dir = assertLocalPath(request.state_dir, 'state_dir');
  await fs.mkdir(request.state_dir, { recursive: true });
  if (request.op === 'login') return login(request);
  if (request.op === 'translate') return translate(request);
  if (request.op === 'recover') return recover(request);
  if (!['status', 'catalog', 'select'].includes(request.op)) throw coded('INVALID_REQUEST', 'Unknown operation.');
  const { context } = await connect(request.state_dir);
  const page = await uiTab(request.state_dir, context, request.op !== 'status');
  if (request.op === 'status') {
    if (!page) return { ok: true, status: 'needs_login', message: '专用登录标签页已关闭。请点“登录 ChatGPT / 打开登录窗口”重新打开。' };
    const evidence = await inspectPage(page);
    return connectionStatus(evidence);
  }
  const result = await catalog(page, request.model || undefined, request.effort || undefined);
  return { ok: true, status: 'ready', message: '已从当前登录账号的网页读取可用模型和推理强度。', ...result };
}

async function main() {
  let request;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    request = JSON.parse(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/, ''));
    const result = await run(request);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    let journalUnreadable = false;
    const journal = request?.request_dir ? await readJournal(request.request_dir).catch(() => { journalUnreadable = true; return null; }) : null;
    process.stdout.write(`${JSON.stringify({ ok: false, status: error.status || 'paused', error_code: error.code || 'BROWSER_ERROR', message: error.message,
      ...(journal ? identityFields(journal) : {}), ...(error.session_id ? { session_id: error.session_id } : {}),
      ...(error.conversation_url ? { conversation_url: error.conversation_url } : {}),
      not_submitted: !journalUnreadable && (error.not_submitted === true || ((request?.op === 'translate' || request?.op === 'recover') && !journal?.dispatch_started)),
    })}\n`);
  } finally {
    // For a CDP connection Playwright closes its connection only, not the user's browser.
    if (browser) await browser.close().catch(() => {});
    if (dispatchLock) await fs.unlink(dispatchLock).catch(() => {});
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
