import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import { connectToRemoteChromeTarget } from './node_modules/@steipete/oracle/dist/src/browser/chromeLifecycle.js';
import { submitPrompt, waitForAssistantResponse, ensurePromptReady } from './node_modules/@steipete/oracle/dist/src/browser/pageActions.js';
import { browserPromptFingerprint, readUserMessageIds, readSubmittedPromptFingerprint } from './node_modules/@steipete/oracle/dist/src/browser/promptFingerprint.js';
import { buildConversationTurnCountExpression } from './node_modules/@steipete/oracle/dist/src/browser/conversationTurns.js';
import * as nativeUI from './native-ui.mjs';
import { effortKey } from './ui.mjs';
import { shellSnapshot, shellPromptReady, shellFingerprint, shellSubmit, shellWaitAnswer } from './shell-page.mjs';
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
async function atomicRename(source, destination) {
  for (let attempt = 0; ; attempt++) {
    try { await fs.rename(source, destination); return; }
    catch (error) {
      // Windows readers and virus scanners can briefly prevent replacement.
      // Keep the previous journal intact; removing it would falsely imply
      // that a submitted request never existed and risk another submission.
      if (process.platform !== 'win32' || !['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt >= 6) throw error;
      await pause(20 * 2 ** attempt);
    }
  }
}
async function writeJson(filename, value) {
  await fs.mkdir(path.dirname(filename), { recursive: true });
  const temp = `${filename}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  await atomicRename(temp, filename);
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
function localConversationId(value) {
  try {
    const url = new URL(value);
    const id = decodeURIComponent(url.pathname.slice(3));
    return url.origin === 'https://chatgpt.com' && !url.search && !url.hash && url.pathname.startsWith('/c/') && /^local-chatgpt:[a-zA-Z0-9-]+$/.test(id) ? id : null;
  } catch { return null; }
}
async function evaluation(runtime, expression) {
  const result = await runtime.evaluate({ expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw coded('PAGE_SCRIPT_ERROR', 'ChatGPT 页面状态读取失败。');
  return result.result?.value;
}
async function promptReadyForLayout(runtime, timeout, log) {
  const deadline = Date.now() + timeout;
  do {
    let layout;
    try { layout = await evaluation(runtime, `document.querySelector('form[data-chatgpt-composer]') ? 'shell' : document.querySelector('#prompt-textarea,textarea[name="prompt-textarea"]') ? 'legacy' : null`); }
    catch (error) {
      if (!/Cannot find (?:default )?(?:execution )?context|Execution context (?:was destroyed|is not available)/i.test(error.message || '')) throw error;
    }
    const remaining = Math.max(1, deadline - Date.now());
    if (layout === 'shell') return shellPromptReady(runtime, remaining);
    if (layout === 'legacy') return ensurePromptReady(runtime, remaining, log);
    await pause(Math.min(200, remaining));
  } while (Date.now() < deadline);
  throw coded('COMPOSER_UNAVAILABLE', '原聊天输入框尚未就绪，请稍后恢复。');
}
export function structuredUserContent(root) {
  const tagCounts = { br: 0, p: 0, div: 0, li: 0, pre: 0, code: 0, other: 0, text_nodes: 0 };
  let inlineCodeCount = 0;
  let buttonCount = 0;
  let controlCount = 0;
  const blocks = new Set(['P', 'DIV', 'LI', 'PRE', 'BLOCKQUOTE', 'SECTION', 'ARTICLE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6']);
  const controls = new Set(['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);
  const read = (node, inPre = false) => {
    if (node?.nodeType === 3) { tagCounts.text_nodes++; return { text: node.nodeValue || '', marked: node.nodeValue || '' }; }
    if (!node || node.nodeType !== 1) return { text: '', marked: '' };
    const tag = String(node.tagName || '').toUpperCase();
    const key = tag.toLowerCase();
    if (Object.hasOwn(tagCounts, key)) tagCounts[key]++; else tagCounts.other++;
    // Native UI controls (such as the user-message expansion button) are not
    // prompt content. Do not strip labels or generic inline/link elements.
    if (controls.has(tag)) {
      controlCount++;
      if (tag === 'BUTTON') buttonCount++;
      return { text: '', marked: '' };
    }
    if (tag === 'BR') return { text: '\n', marked: '\n' };
    const result = { text: '', marked: '' };
    for (const child of node.childNodes || []) {
      const block = child.nodeType === 1 && blocks.has(String(child.tagName || '').toUpperCase());
      const content = read(child, inPre || tag === 'PRE');
      for (const field of ['text', 'marked']) {
        if (block && result[field] && !result[field].endsWith('\n')) result[field] += '\n';
        result[field] += content[field];
        if (block && content[field] && !result[field].endsWith('\n')) result[field] += '\n';
      }
    }
    if (tag === 'CODE' && !inPre) { inlineCodeCount++; result.marked = '`' + result.marked + '`'; }
    return result;
  };
  const content = read(root);
  return { text: content.text, inlineCodeText: inlineCodeCount ? content.marked : null, inlineCodeCount, buttonCount, controlCount, tagCounts };
}

export async function userTurn(runtime) {
  const shell = await shellSnapshot(runtime, structuredUserContent);
  if (shell) return shell;
  return evaluation(runtime, `(() => {
    const users = [...document.querySelectorAll('[data-message-author-role="user"]')];
    const last = users.at(-1);
    const structure = (${structuredUserContent.toString()})(last);
    const editors = [...document.querySelectorAll('#prompt-textarea,textarea[name="prompt-textarea"]')];
    return { url: location.href, count: users.length, messageId: last?.getAttribute('data-message-id'), text: last?.textContent,
      renderedText: last?.innerText, structuredText: structure.text, inlineCodeText: structure.inlineCodeText,
      inlineCodeCount: structure.inlineCodeCount, buttonCount: structure.buttonCount, controlCount: structure.controlCount, tagCounts: structure.tagCounts,
      userIds: users.map(node => node.getAttribute('data-message-id')),
      hasDraft: editors.some(node => String(node.value ?? node.textContent ?? '').trim()),
      generating: !!document.querySelector('[data-testid="stop-button"],button[aria-label="Stop generating"]') };
  })()`);
}

export function assistantCompletionDOM(expected) {
  const users = [...document.querySelectorAll('[data-message-author-role="user"]')];
  const latestUser = users.at(-1);
  if (location.href !== expected.url || users.length !== expected.count ||
      latestUser?.getAttribute('data-message-id') !== expected.messageId || latestUser?.textContent !== expected.text ||
      users.some((node, index) => node.getAttribute('data-message-id') !== expected.userIds[index])) {
    return { status: 'identity_changed' };
  }
  const assistantSelector = '[data-message-author-role="assistant"],[data-turn="assistant"]';
  const containers = [...document.querySelectorAll('[data-testid^="conversation-turn"]')];
  const turns = containers.length ? containers : [...document.querySelectorAll('article[data-message-author-role],div[data-message-author-role],section[data-message-author-role],article[data-turn],div[data-turn],section[data-turn]')];
  const isAssistant = node => node.matches(assistantSelector) || node.getAttribute('data-testid')?.includes('assistant') || !!node.querySelector(assistantSelector + ',[data-testid*="assistant"]');
  const lastIndex = turns.findLastIndex(isAssistant);
  const turn = turns[lastIndex];
  const message = turn?.querySelector(assistantSelector) || turn;
  const visible = node => {
    const rect = node.getBoundingClientRect(); const style = window.getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && !(style.opacity !== '' && Number(style.opacity) === 0);
  };
  const finished = 'button[data-testid="copy-turn-action-button"],button[data-testid="good-response-turn-action-button"],button[data-testid="bad-response-turn-action-button"],button[aria-label="Share"]';
  const stop = '[data-testid="stop-button"],[data-testid="composer-stop-button"],form button[aria-label*="stop" i]:not([aria-label*="dictat" i]):not([aria-label*="voice" i]):not([aria-label*="read" i])';
  const short = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const nodeMetadata = node => {
    const rect = node.getBoundingClientRect(); const style = window.getComputedStyle(node);
    return { tag: node.tagName.toLowerCase(), role: short(node.getAttribute('role')),
      data_testid: short(node.getAttribute('data-testid')), aria_label: short(node.getAttribute('aria-label')),
      classes: short(node.getAttribute('class')), disabled: !!node.disabled,
      aria_disabled: short(node.getAttribute('aria-disabled')), aria_hidden: short(node.getAttribute('aria-hidden')),
      hidden: !!node.hidden, inert: !!node.inert,
      rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
      display: short(style.display), visibility: short(style.visibility), opacity: short(style.opacity), visible: visible(node) };
  };
  const ancestors = node => {
    const result = [];
    for (let parent = node?.parentElement; parent && result.length < 5; parent = parent.parentElement) result.push(nodeMetadata(parent));
    return result;
  };
  const turnAssociation = node => {
    const containingIndex = turns.findIndex(candidate => candidate === node || candidate.contains(node));
    const precedingIndex = turns.findLastIndex(candidate => !!(candidate.compareDocumentPosition(node) & 4));
    return { nearest_turn_index: containingIndex >= 0 ? containingIndex : null,
      preceding_turn_index: precedingIndex >= 0 ? precedingIndex : null,
      inside_last_assistant_turn: !!turn?.contains(node) };
  };
  const stopNodes = [...document.querySelectorAll(stop)];
  const pageCompletionNodes = [...document.querySelectorAll(finished)];
  const buttons = [...(turn?.querySelectorAll('button,[role="button"]') || [])];
  const inCode = button => {
    if (button.closest('pre,code,[data-testid*="code-block"],[data-testid*="codeblock"],[class*="code-block"],[class*="codeblock"]')) return true;
    // Code-copy controls often sit in a header immediately beside PRE. Stop
    // at the assistant body/turn so an answer containing code does not make
    // its separate turn action bar appear to be a code toolbar.
    let parent = button.parentElement;
    for (let depth = 0; parent && parent !== turn && parent !== message && depth < 3; depth++, parent = parent.parentElement) {
      if (parent.matches('.markdown,[data-message-content],.prose')) break;
      if (parent.querySelector('pre')) return true;
    }
    return false;
  };
  const buttonMetadata = buttons.slice(0, 40).map(button => ({
    data_testid: short(button.getAttribute('data-testid')), aria_label: short(button.getAttribute('aria-label')),
    label: short(button.innerText || button.textContent), in_code: inCode(button), visible: visible(button),
    oracle_completion_match: button.matches(finished),
  }));
  const counts = {};
  for (const [name, selector] of Object.entries({ markdown: '.markdown', message_content: '[data-message-content]', message_testid: '[data-testid*="message"]', assistant_testid: '[data-testid*="assistant"]', prose: '.prose', markdown_class: '[class*="markdown"]', pre: 'pre', code: 'code' })) {
    counts[name] = (message?.matches(selector) ? 1 : 0) + (message?.querySelectorAll(selector).length || 0);
  }
  const visibleCount = (root, selector) => [...(root?.querySelectorAll(selector) || [])].filter(visible).length;
  return {
    status: 'inspected', turn_count: turns.length, assistant_role_count: document.querySelectorAll(assistantSelector).length,
    last_assistant_index: lastIndex, last_assistant_id_present: !!message?.getAttribute('data-message-id'),
    last_assistant_text_length: message?.textContent?.length ?? null, last_assistant_rendered_length: message?.innerText?.length ?? null,
    body_selectors: counts, buttons_total: buttons.length, buttons: buttonMetadata,
    oracle_completion_count: turn?.querySelectorAll(finished).length || 0,
    oracle_completion_outside_code_count: buttons.filter(button => button.matches(finished) && !inCode(button)).length,
    code_button_count: buttons.filter(inCode).length,
    visible_stop: stopNodes.some(visible), stop_nodes_total: stopNodes.length,
    stop_nodes: stopNodes.slice(0, 20).map(node => {
      let ancestorHidden = false; let ancestorAriaHidden = false; let ancestorInert = false;
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        const style = window.getComputedStyle(parent);
        ancestorHidden ||= !!parent.hidden || style.display === 'none' || style.visibility === 'hidden' || (style.opacity !== '' && Number(style.opacity) === 0);
        ancestorAriaHidden ||= parent.getAttribute('aria-hidden') === 'true'; ancestorInert ||= !!parent.inert;
      }
      return { ...nodeMetadata(node), ...turnAssociation(node), ancestor_hidden: ancestorHidden,
        ancestor_aria_hidden: ancestorAriaHidden, ancestor_inert: ancestorInert, ancestors: ancestors(node) };
    }),
    page_completion_count: pageCompletionNodes.length,
    page_completion_controls: pageCompletionNodes.slice(0, 40).map(node => ({ ...nodeMetadata(node), ...turnAssociation(node), in_code: inCode(node) })),
    last_assistant_ancestors: ancestors(message),
    visible_thinking_count: visibleCount(turn, '[data-testid*="thinking"],[data-testid*="reasoning"]'),
    visible_aria_busy_count: visibleCount(turn, '[aria-busy="true"]'),
    visible_role_status_count: visibleCount(turn, '[role="status"]'),
    visible_loading_count: visibleCount(turn, '.loading-shimmer,[class*="loading-shimmer"]'),
    visible_progress_count: visibleCount(turn, 'progress,[role="progressbar"]'),
  };
}

export async function readAssistantCompletionDiagnostic(runtime, turn, journal) {
  if (!journal.submitted_prompt_hash || !journal.conversation_url || turn.url !== journal.conversation_url ||
      !turn.messageId || typeof turn.text !== 'string' || browserPromptFingerprint(turn.text, turn.messageId) !== journal.submitted_prompt_hash ||
      !normalizedTurnMatches(turn, journal) || !newTurnMatchesBaseline(turn, journal)) {
    return { status: 'request_identity_unverified' };
  }
  if (turn.shell) {
    const current = await shellSnapshot(runtime, undefined, journal.local_conversation_id);
    if (!current || current.url !== turn.url || current.messageId !== turn.messageId || current.text !== turn.text || JSON.stringify(current.userIds) !== JSON.stringify(turn.userIds)) return { status: 'identity_changed' };
    return { status: 'inspected', layout: 'browser-shell', turn_count: current.turnCount,
      assistant_associated: !!current.assistant, assistant_complete: !!current.assistant?.complete,
      assistant_text_length: current.assistant?.text?.length ?? null, generating: current.generating };
  }
  const expected = { url: turn.url, count: turn.count, messageId: turn.messageId, text: turn.text, userIds: idsForTurn(turn) };
  return evaluation(runtime, `(${assistantCompletionDOM.toString()})(${JSON.stringify(expected)})`);
}

function requestTaskId(request, journal) {
  const supplied = request.task_id;
  const valid = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(value);
  if ((supplied !== undefined && !valid(supplied)) || (journal?.task_id !== undefined && !valid(journal.task_id))) {
    throw coded('INVALID_TASK_ID', 'task_id 必须是 1–128 位安全英文字母、数字、下划线或短横线标识。');
  }
  if (supplied && journal?.task_id && supplied !== journal.task_id) throw coded('TASK_OWNERSHIP_MISMATCH', '本批次已属于另一个翻译任务，不能转移会话。');
  return supplied || journal?.task_id || `legacy-${hash(request.request_dir).slice(0, 40)}`;
}
function previousUserIds(journal) {
  if (Array.isArray(journal.previous_user_ids) && journal.previous_user_ids.every(id => typeof id === 'string' && id)) return journal.previous_user_ids;
  // The old backend created a fresh page for each batch; only a recorded zero
  // baseline proves that an unbound legacy journal started with no user turns.
  if (journal.baseline_turns === 0) return [];
  return null;
}
function idsForTurn(turn) {
  return Array.isArray(turn.userIds) ? turn.userIds : turn.count === 1 && turn.messageId ? [turn.messageId] : turn.count === 0 ? [] : null;
}
function newTurnMatchesBaseline(turn, journal) {
  const previous = previousUserIds(journal);
  const current = idsForTurn(turn);
  return !!previous && !!current && current.length === previous.length + 1 && turn.count === current.length &&
    current.at(-1) === turn.messageId && typeof turn.messageId === 'string' && !previous.includes(turn.messageId) &&
    previous.every((id, index) => current[index] === id);
}
function normalizedTurnMatches(turn, journal) {
  return !journal.prompt_normalized_hash || [turn.text, turn.renderedText, turn.structuredText, turn.inlineCodeText].some(text => typeof text === 'string' && hash(normalizedPrompt(text)) === journal.prompt_normalized_hash);
}
function urlCategory(value) {
  try { const url = new URL(value); if (conversationURL(value)) return 'conversation'; if (url.origin === 'https://chatgpt.com' && url.pathname === '/') return 'chat_home'; if (['auth.openai.com', 'auth0.openai.com'].includes(url.hostname)) return 'authentication'; return 'other'; } catch { return 'unavailable'; }
}
export function submissionDiagnostic(journal, turn, prompt, observedFingerprint) {
  const raw = typeof turn?.text === 'string' ? turn.text : '';
  const rendered = typeof turn?.renderedText === 'string' ? turn.renderedText : '';
  const structured = typeof turn?.structuredText === 'string' ? turn.structuredText : null;
  const inlineCode = typeof turn?.inlineCodeText === 'string' ? turn.inlineCodeText : null;
  const original = typeof prompt === 'string' ? prompt : null;
  let mismatch = null;
  if (original !== null) { let index = 0; while (index < original.length && index < raw.length && original[index] === raw[index]) index++; mismatch = index === original.length && index === raw.length ? -1 : index; }
  const rawFingerprint = turn?.messageId && typeof turn?.text === 'string' ? browserPromptFingerprint(raw, turn.messageId) : null;
  const difference = candidate => {
    if (original === null || candidate === null) return null;
    const expected = normalizedPrompt(original); const actual = normalizedPrompt(candidate);
    let index = 0; while (index < expected.length && index < actual.length && expected[index] === actual[index]) index++;
    if (index === expected.length && index === actual.length) return { index: -1, expected_codepoints: [], actual_codepoints: [] };
    const start = Math.max(0, index - 2);
    // At most five characters from either side, represented only as numbers.
    return { index, window_start: start, expected_codepoints: Array.from(expected.slice(start, index + 3), ch => ch.codePointAt(0)),
      actual_codepoints: Array.from(actual.slice(start, index + 3), ch => ch.codePointAt(0)) };
  };
  const newlines = text => typeof text === 'string' ? (text.match(/\r\n|\r|\n/g) || []).length : null;
  const tagCounts = Object.fromEntries(['br', 'p', 'div', 'li', 'pre', 'code', 'other', 'text_nodes'].map(key => [key,
    Number.isInteger(turn?.tagCounts?.[key]) && turn.tagCounts[key] >= 0 ? turn.tagCounts[key] : null]));
  return { user_count: Number.isInteger(turn?.count) ? turn.count : null, previous_user_count: previousUserIds(journal)?.length ?? null,
    has_message_id: !!turn?.messageId, new_user_matches_baseline: !!turn && newTurnMatchesBaseline(turn, journal),
    original_length: original?.length ?? null, raw_length: raw.length, rendered_length: rendered.length, structural_length: structured?.length ?? null,
    inline_code_length: inlineCode?.length ?? null, inline_code_count: Number.isInteger(turn?.inlineCodeCount) ? turn.inlineCodeCount : null,
    button_count: Number.isInteger(turn?.buttonCount) ? turn.buttonCount : null,
    control_count: Number.isInteger(turn?.controlCount) ? turn.controlCount : null,
    original_lf_normalized_length: original?.replace(/\r\n?/g, '\n').length ?? null,
    original_newline_count: newlines(original), raw_newline_count: newlines(raw), rendered_newline_count: newlines(rendered), structural_newline_count: newlines(structured),
    tag_counts: tagCounts, br_count: tagCounts.br,
    original_file_hash_equal: original !== null && hash(original) === journal.prompt_hash,
    raw_hash_equal: hash(raw) === journal.prompt_hash,
    normalized_hash_equal: hash(normalizedPrompt(raw)) === journal.prompt_normalized_hash,
    rendered_normalized_hash_equal: hash(normalizedPrompt(rendered)) === journal.prompt_normalized_hash,
    structural_normalized_hash_equal: structured !== null && hash(normalizedPrompt(structured)) === journal.prompt_normalized_hash,
    inline_code_normalized_hash_equal: inlineCode !== null && hash(normalizedPrompt(inlineCode)) === journal.prompt_normalized_hash,
    normalized_mismatches: { raw: difference(raw), rendered: difference(rendered), structural: difference(structured), inline_code: difference(inlineCode) },
    observed_fingerprint_present: !!observedFingerprint, observed_fingerprint_matches_raw: !!rawFingerprint && observedFingerprint === rawFingerprint,
    saved_fingerprint_matches_raw: !!rawFingerprint && journal.submitted_prompt_hash === rawFingerprint,
    first_mismatch: mismatch, url_category: urlCategory(turn?.url), expected_url_equal: !journal.conversation_url || turn?.url === journal.conversation_url };
}

// Standard page translation hints, scoped to our ChatGPT main documents. They
// leave Chrome's language preferences, login state and browser identity alone.
export const OWNED_PAGE_NOTRANSLATE_SCRIPT = `(() => {
  if (window.top !== window || location.origin !== 'https://chatgpt.com') return;
  const apply = () => {
    const root = document.documentElement;
    if (root) { root.setAttribute('translate', 'no'); root.classList.add('notranslate'); }
    const head = document.head;
    if (head) {
      let meta = head.querySelector('meta[name="google"][content="notranslate"]');
      if (!meta) { meta = document.createElement('meta'); meta.name = 'google'; meta.content = 'notranslate'; head.appendChild(meta); }
    }
    return !!(root && head);
  };
  if (!apply()) {
    const observer = new MutationObserver(() => { if (apply()) observer.disconnect(); });
    observer.observe(document, { childList: true, subtree: true });
  }
})()`;

export async function installOwnedPageTranslationHints(client) {
  await client.Page.addScriptToEvaluateOnNewDocument({ source: OWNED_PAGE_NOTRANSLATE_SCRIPT });
  await evaluation(client.Runtime, OWNED_PAGE_NOTRANSLATE_SCRIPT);
}

export async function translatedHomeState(runtime) {
  return evaluation(runtime, `(() => {
    const root = document.documentElement;
    const home = location.origin === 'https://chatgpt.com' && location.pathname === '/' && !location.search && !location.hash;
    const messages = document.querySelectorAll('[data-message-author-role],[data-message-id],[data-testid*="conversation-turn"]').length;
    const editors = [...document.querySelectorAll('#prompt-textarea,textarea[name="prompt-textarea"]')];
    const draft = editors.some(node => String(node.value ?? node.textContent ?? '').trim());
    const generating = !!document.querySelector('[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="停止生成"]');
    return { emptyHome: home && messages === 0 && editors.length > 0 && !draft && !generating,
      translated: !!root && (root.classList.contains('translated-ltr') || root.classList.contains('translated-rtl')) };
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

export function textSubmissionInput(input) {
  // Oracle chooses its built-in DOM click path when dispatchMouseEvent is absent.
  // Choose this before the only submission attempt; never retry an ambiguous click.
  return {
    insertText: params => input.insertText(params),
    dispatchKeyEvent: params => input.dispatchKeyEvent(params),
  };
}

// Dependency injection keeps lifecycle and no-resubmission tests entirely offline.
export function createBridge(overrides = {}) {
  const deps = { discover: browserEndpoint, attach: connectToRemoteChromeTarget, scopedAttach: connectOwnedPage, ui: nativeUI,
    submit: async (options, ...args) => (await shellSnapshot(options.runtime)) ? shellSubmit(options, ...args) : submitPrompt(options, ...args),
    waitAnswer: async (runtime, timeout, log, baseline, conversationId, submittedTurn) => (submittedTurn?.shell || await shellSnapshot(runtime)) ? shellWaitAnswer(runtime, timeout, baseline, conversationId, submittedTurn) : waitForAssistantResponse(runtime, timeout, log, baseline, conversationId),
    promptReady: promptReadyForLayout,
    readUserIds: async (runtime, timeout) => (await shellSnapshot(runtime))?.userIds ?? readUserMessageIds(runtime, timeout),
    readFingerprint: async (runtime, previous, timeout) => (await shellSnapshot(runtime)) ? shellFingerprint(runtime, previous, timeout) : readSubmittedPromptFingerprint(runtime, previous, timeout),
    userTurn, readCompletionDiagnostic: readAssistantCompletionDiagnostic,
    recoveryNow: Date.now, recoveryPause: pause,
    installTranslationHints: installOwnedPageTranslationHints, translatedHomeState,
    ...overrides };
  const connections = new Map();
  const pending = new Map();
  const active = new Map();
  const activeTasks = new Map();

  function connected(stateDir) {
    const entry = connections.get(stateDir);
    if (entry?.anchor?.client?._ws && entry.anchor.client._ws.readyState !== 1) entry.authorized = false;
    if (!entry?.authorized) throw coded('CONNECTION_REQUIRED', CONNECTION_GUIDANCE, { status: 'needs_connection' });
    return entry;
  }
  async function saveOwners(entry) {
    await writeJson(path.join(entry.stateDir, 'native-browser.json'), { version: 2,
      browser_ws: entry.endpoint.browserWSEndpoint, owned_targets: [...entry.owned], ui_tab: entry.uiTarget || null,
      notranslate_reloaded_targets: [...entry.translationReloaded], notranslate_targets: [...entry.translationProtected] });
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
    await deps.installTranslationHints(connection.client);
    entry.translationProtected.add(connection.targetId);
    await saveOwners(entry);
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
  async function prepareOwnedHome(entry, connection) {
    if (!entry.owned.has(connection.targetId)) throw coded('TAB_NOT_OWNED', '该标签页不属于翻译软件，已拒绝访问。');
    const state = await deps.translatedHomeState(connection.client.Runtime);
    if (!state.emptyHome) return;
    const protectedBeforeLoad = entry.translationProtected.has(connection.targetId);
    await deps.installTranslationHints(connection.client);
    entry.translationProtected.add(connection.targetId);
    await saveOwners(entry);
    if (protectedBeforeLoad || entry.translationReloaded.has(connection.targetId)) return;
    // Record the one-shot attempt before reloading, including across broker restarts.
    entry.translationReloaded.add(connection.targetId);
    await saveOwners(entry);
    const current = await deps.translatedHomeState(connection.client.Runtime);
    if (!current.emptyHome) return;
    await connection.client.Page.reload({ ignoreCache: false });
    await deps.promptReady(connection.client.Runtime, 30000, quiet);
  }
  async function login(request) {
    let entry = connections.get(request.state_dir);
    if (!entry?.authorized) {
      const endpoint = await deps.discover(request.state_dir);
      const saved = await readJson(path.join(request.state_dir, 'native-browser.json')).catch(() => null);
      const sameBrowser = saved?.version === 2 && saved.browser_ws === endpoint.browserWSEndpoint;
      entry = { stateDir: request.state_dir, endpoint, authorized: false,
        owned: new Set(sameBrowser && Array.isArray(saved.owned_targets) ? saved.owned_targets : []),
        translationReloaded: new Set(sameBrowser && Array.isArray(saved.notranslate_reloaded_targets) ? saved.notranslate_reloaded_targets : []),
        translationProtected: new Set(sameBrowser && Array.isArray(saved.notranslate_targets) ? saved.notranslate_targets : []),
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
      await prepareOwnedHome(entry, first);
      return deps.ui.prepareLoginPage(deps.ui.createNativePage(first.client));
    }
    const connection = await setupConnection(entry, true);
    return withConnection(connection, async page => { await prepareOwnedHome(entry, connection); return deps.ui.prepareLoginPage(page); });
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
  const taskPath = (request, id) => path.join(request.state_dir, 'tasks', `${id}.json`);
  async function saveTask(request, task) { await writeJson(taskPath(request, task.task_id), task); }
  async function loadTask(request, journal) {
    const id = requestTaskId(request, journal);
    let task;
    try { task = await readJson(taskPath(request, id)); }
    catch { throw coded('TASK_RECORD_UNREADABLE', 'PDF 会话记录损坏，已停止以避免另开聊天。'); }
    if (task && (task.version !== 1 || task.task_id !== id)) throw coded('TASK_RECORD_UNREADABLE', 'PDF 会话归属记录不完整，不能继续。');
    if (journal && !journal.task_id && task && task.pending_request_dir !== request.request_dir && task.last_request_dir !== request.request_dir) {
      throw coded('TASK_OWNERSHIP_MISMATCH', '旧批次不能并入已经开始的其他 PDF 会话。');
    }
    if (!task) task = { version: 1, task_id: id, created_at: new Date().toISOString(),
      model: journal?.model || null, effort: journal?.effort ?? null,
      target_id: journal?.target_id || null, browser_ws: journal?.browser_ws || null,
      conversation_url: conversationURL(journal?.conversation_url) ? journal.conversation_url : null,
      pending_request_dir: journal?.dispatch_started ? request.request_dir : null };
    if (journal && !journal.task_id) {
      journal.task_id = id; // Claim first so a crash cannot permit cross-task adoption.
      await writeJson(path.join(request.request_dir, 'journal.json'), journal);
    }
    if ((request.model && task.model && request.model !== task.model) || (request.effort && task.effort !== null && effortKey(request.effort) !== effortKey(task.effort))) {
      throw coded('TASK_SETTINGS_CHANGED', '本 PDF 已固定模型和推理强度，不能在原任务中更换。');
    }
    await saveTask(request, task);
    return task;
  }
  async function acceptTaskBatch(request, journal, task) {
    if (task.pending_request_dir && task.pending_request_dir !== request.request_dir) throw coded('TASK_PENDING_BATCH', '该 PDF 的上一批尚未完成，请先恢复上一批。');
    if (task.conversation_url && journal.conversation_url !== task.conversation_url) throw coded('TASK_CONVERSATION_MISMATCH', '本批会话与 PDF 已保存会话不一致，不能接受回答。');
    Object.assign(task, { target_id: journal.target_id, browser_ws: journal.browser_ws,
      conversation_url: journal.conversation_url || task.conversation_url,
      local_conversation_id: journal.local_conversation_id || task.local_conversation_id || null,
      last_user_id: journal.submitted_user_id || null, last_user_fingerprint: journal.submitted_prompt_hash || null,
      last_user_ids: journal.accepted_user_ids || null, last_user_count: journal.submitted_user_count ?? null,
      last_request_dir: request.request_dir, pending_request_dir: null });
    await saveTask(request, task);
  }
  async function assertNoPendingBatch(request, task) {
    if (!task.pending_request_dir || task.pending_request_dir === request.request_dir) return;
    const previous = await readJournal(task.pending_request_dir);
    if (previous?.task_id === task.task_id && previous.status === 'complete') {
      await acceptTaskBatch({ ...request, request_dir: task.pending_request_dir }, previous, task);
      return;
    }
    throw coded('TASK_PENDING_BATCH', '同一 PDF 的上一批尚未确认完成，已阻止后续批次。请恢复原批次；不会另开聊天。');
  }
  async function promptForJournal(request, journal) {
    const filename = localPath(request.prompt_file || path.join(request.request_dir, 'prompt.txt'), 'prompt_file');
    let prompt;
    try { prompt = await fs.readFile(filename, 'utf8'); }
    catch { throw coded('PROMPT_RECORD_UNAVAILABLE', '原批次提示文件无法读取，不能重新核验；不会重发。'); }
    if (hash(prompt) !== journal.prompt_hash || hash(normalizedPrompt(prompt)) !== journal.prompt_normalized_hash) {
      throw coded('PROMPT_RECORD_CHANGED', '原批次提示文件与提交记录不一致，已停止恢复。');
    }
    return prompt;
  }
  async function bindSubmission(request, journal, runtime, prompt, timeout = 0) {
    const previousIds = previousUserIds(journal);
    if (!previousIds) throw coded('RECOVERY_UNAVAILABLE', '缺少提交前消息基线，不能重新绑定原请求。');
    const fingerprint = await deps.readFingerprint(runtime, previousIds, timeout);
    const turn = await deps.userTurn(runtime);
    const diagnostic = submissionDiagnostic(journal, turn, prompt, fingerprint);
    const urlMatches = !journal.conversation_url || turn.url === journal.conversation_url;
    if (!newTurnMatchesBaseline(turn, journal) || !normalizedTurnMatches(turn, journal) || !urlMatches ||
        !fingerprint || browserPromptFingerprint(turn.text, turn.messageId) !== fingerprint) {
      throw coded('RECOVERY_IDENTITY_UNVERIFIED', '网页上的已发送文字尚不能与本批翻译对应，已暂停且不会重发。', { diagnostic });
    }
    Object.assign(journal, { submitted_prompt_hash: fingerprint, submitted_user_id: turn.messageId,
      submitted_user_count: turn.count, accepted_user_ids: idsForTurn(turn),
      matched_text_source: hash(normalizedPrompt(turn.text)) === journal.prompt_normalized_hash ? 'textContent' :
        hash(normalizedPrompt(turn.renderedText)) === journal.prompt_normalized_hash ? 'innerText' :
          hash(normalizedPrompt(turn.structuredText)) === journal.prompt_normalized_hash ? 'domStructure' : 'domInlineCode' });
    const localId = localConversationId(turn.url) || (/^local-chatgpt:[a-zA-Z0-9-]+$/.test(turn.localConversationId || '') ? turn.localConversationId : null);
    if (localId) journal.local_conversation_id = localId;
    if (conversationURL(turn.url)) journal.conversation_url = turn.url;
    await writeJson(path.join(request.request_dir, 'journal.json'), journal);
  }
  async function diagnoseRequest(request) {
    const journal = await readJournal(request.request_dir);
    if (!journal?.dispatch_started) throw coded('NOT_SUBMITTED', '本批没有已开始提交的记录。', { not_submitted: true });
    requestTaskId(request, journal); // Validate ownership without creating or claiming a task.
    const entry = connected(request.state_dir);
    if (journal.browser_ws !== entry.endpoint.browserWSEndpoint || !entry.owned.has(journal.target_id)) {
      throw coded('RECOVERY_UNAVAILABLE', '诊断仅允许原浏览器中本批保存的软件标签页；没有打开其他页面。');
    }
    const connection = await attachOwned(entry, journal.target_id);
    return withConnection(connection, async page => {
      const turn = await deps.userTurn(connection.client.Runtime);
      if (request.show_request === true && turn.url === journal.conversation_url) await page.bringToFront();
      const previous = previousUserIds(journal);
      const observed = previous ? await deps.readFingerprint(connection.client.Runtime, previous, 0) : null;
      const prompt = await fs.readFile(request.prompt_file || path.join(request.request_dir, 'prompt.txt'), 'utf8').catch(() => null);
      const diagnostic = submissionDiagnostic(journal, turn, prompt, observed);
      if (turn.url === journal.conversation_url) diagnostic.page_state = await evaluation(connection.client.Runtime, `(() => {
        const main = document.querySelector('main,[role="main"]');
        const copy = main?.cloneNode(true);
        copy?.querySelectorAll('[data-message-author-role],[data-message-id],[data-testid*="conversation-turn"],[data-turn-key],[data-chatgpt-search-unit-key],aside,nav,[role="navigation"],input,textarea,[contenteditable="true"]').forEach(n => n.remove());
        return { ready_state: document.readyState, visibility: document.visibilityState,
          has_main: !!main, has_composer: !!document.querySelector('#prompt-textarea,textarea[name="prompt-textarea"],form[data-chatgpt-composer] [data-composer-markdown][contenteditable="true"]'),
          page_notice: (copy?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 600),
          page_tail: (copy?.textContent || '').replace(/\\s+/g, ' ').trim().slice(-800),
          layout: [...(main?.querySelectorAll('section,article,textarea,[contenteditable],iframe') || [])].slice(0, 20).map(n => ({
            tag: n.tagName, role: n.getAttribute('role'), testid: n.getAttribute('data-testid'),
            turn: n.getAttribute('data-turn'), classes: String(n.className || '').slice(0, 160) })) };
      })()`);
      if (request.capture_page === true && turn.url === journal.conversation_url) {
        const clip = await evaluation(connection.client.Runtime, `(() => { const r = document.querySelector('main,[role="main"]')?.getBoundingClientRect(); const x = Math.max(0, Math.min(r?.x || 0, innerWidth - 1)); return {x, y:0, width:Math.min(1600, innerWidth-x), height:Math.min(1100, innerHeight), scale:1}; })()`);
        const capture = await connection.client.Page.captureScreenshot({ format: 'png', captureBeyondViewport: false, clip });
        const filename = path.join(request.request_dir, 'diagnostic-page.png');
        await fs.writeFile(filename, Buffer.from(capture.data, 'base64'));
        diagnostic.screenshot_file = filename;
        const markup = await evaluation(connection.client.Runtime, `(() => { const m = document.querySelector('main,[role="main"]')?.cloneNode(true); m?.querySelectorAll('input,textarea,[contenteditable="true"]').forEach(n => { n.removeAttribute('value'); n.textContent=''; }); return m?.outerHTML || ''; })()`);
        await fs.writeFile(path.join(request.request_dir, 'diagnostic-layout.html'), markup);
      }
      diagnostic.assistant_completion = await deps.readCompletionDiagnostic(connection.client.Runtime, turn, journal)
        .catch(() => ({ status: 'diagnostic_read_failed' }));
      return { ok: true, status: 'diagnostic', diagnostic, not_submitted: false };
    });
  }
  async function completed(request, journal, task) {
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
    if (task && (!task.last_request_dir || task.pending_request_dir === request.request_dir)) await acceptTaskBatch(request, journal, task);
    return { ok: true, status: 'complete', answer, model: journal.model, effort: journal.effort, ...identity(journal) };
  }
  async function persistAnswer(request, journal, answer, task) {
    if (typeof answer !== 'string' || !answer.trim()) throw coded('EMPTY_RESPONSE', 'ChatGPT 没有返回可用译文。原批次已保留，不会重发。');
    const temp = `${journal.output_file}.${process.pid}.tmp`;
    await fs.mkdir(path.dirname(journal.output_file), { recursive: true });
    await fs.writeFile(temp, answer, { mode: 0o600 });
    await atomicRename(temp, journal.output_file);
    journal.status = 'complete';
    await writeJson(path.join(request.request_dir, 'journal.json'), journal);
    return completed(request, journal, task);
  }
  async function capture(request, journal, connection, save, task) {
    const Runtime = connection.client.Runtime;
    const before = await deps.userTurn(Runtime);
    if (journal.conversation_url && before.url !== journal.conversation_url) throw coded('RECOVERY_IDENTITY_UNVERIFIED', '当前页面不是本批已保存会话，已停止读取回答。');
    const matches = before.messageId && typeof before.text === 'string' && journal.submitted_prompt_hash &&
      browserPromptFingerprint(before.text, before.messageId) === journal.submitted_prompt_hash &&
      normalizedTurnMatches(before, journal) && (!previousUserIds(journal) || newTurnMatchesBaseline(before, journal));
    if (!matches) throw coded('RECOVERY_IDENTITY_UNVERIFIED', '无法确认该页面最后一条请求属于本批次，已暂停；不会重发或读取其他会话的回答。');
    if (conversationURL(before.url)) journal.conversation_url = before.url;
    if (!journal.local_conversation_id) journal.local_conversation_id = localConversationId(before.url) ||
      (/^local-chatgpt:[a-zA-Z0-9-]+$/.test(before.localConversationId || '') ? before.localConversationId : null);
    await save();
    const conversationId = journal.conversation_url?.split('/').at(-1);
    const answer = await deps.waitAnswer(Runtime, (request.timeout_seconds || 900) * 1000, quiet, journal.baseline_turns ?? 0, conversationId,
      { ...before, localConversationId: journal.local_conversation_id || localConversationId(before.url) });
    const after = await deps.userTurn(Runtime);
    if (after.messageId !== before.messageId || browserPromptFingerprint(after.text, after.messageId) !== journal.submitted_prompt_hash ||
        after.count !== before.count || (journal.conversation_url && after.url !== journal.conversation_url)) {
      throw coded('RECOVERY_IDENTITY_UNVERIFIED', '等待回答时页面会话发生变化，已暂停。');
    }
    if (!conversationURL(after.url)) throw coded('RECOVERY_NOT_READY', '本批回答已出现，但网页尚未给出可保存的会话地址；已暂停，稍后恢复原批次。');
    Object.assign(journal, { conversation_url: after.url, submitted_user_id: after.messageId, submitted_user_count: after.count, accepted_user_ids: idsForTurn(after),
      ...(answer.localConversationId ? { local_conversation_id: answer.localConversationId } : {}) });
    return persistAnswer(request, journal, answer.text, task);
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
        // A website rollout changed message wrappers, not the saved request.
        // Rebind only with the exact server message ID, full original prompt,
        // saved conversation URL and ordered user-message baseline all proven.
        if (turn.shell && matchingUrl && journal.conversation_url && journal.submitted_user_id === turn.messageId &&
            journal.prompt_normalized_hash && normalizedTurnMatches(turn, journal) && newTurnMatchesBaseline(turn, journal) &&
            browserPromptFingerprint(turn.text, turn.messageId) !== journal.submitted_prompt_hash) {
          await promptForJournal(request, journal);
          await deps.ui.ensureChat(page);
          journal.submitted_prompt_hash = browserPromptFingerprint(turn.text, turn.messageId);
          journal.matched_text_source = 'shellDomVerified';
          await writeJson(path.join(request.request_dir, 'journal.json'), journal);
        }
        const matchingTurn = matchingUrl && turn.messageId && typeof turn.text === 'string' &&
          browserPromptFingerprint(turn.text, turn.messageId) === journal.submitted_prompt_hash &&
          normalizedTurnMatches(turn, journal) && (!previousUserIds(journal) || newTurnMatchesBaseline(turn, journal));
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
    const task = await loadTask(request, journal);
    await assertNoPendingBatch(request, task);
    const ready = await completed(request, journal, task);
    if (ready) return ready;
    if (journal.version < 2 && alive(journal.oracle_pid)) throw coded('BROWSER_BUSY', '旧版翻译进程仍在收取原回答，请等待后继续原文件；本批没有重发。');
    const entry = connected(request.state_dir);
    const needsBinding = !journal.submitted_prompt_hash;
    const refreshConversation = request.refresh_conversation === true;
    if (refreshConversation && (needsBinding || !conversationURL(journal.conversation_url))) {
      throw coded('RECOVERY_IDENTITY_UNVERIFIED', '刷新原会话需要已保存的请求指纹和确切会话地址；没有刷新或重发。');
    }
    if (needsBinding && (!previousUserIds(journal) || journal.browser_ws !== entry.endpoint.browserWSEndpoint || !entry.owned.has(journal.target_id))) {
      throw coded('RECOVERY_UNAVAILABLE', '缺少请求指纹时只能核验原浏览器中的原软件标签页，不能另开聊天或重发。');
    }
    let connection;
    if (journal.browser_ws === entry.endpoint.browserWSEndpoint && entry.owned.has(journal.target_id)) {
      connection = await attachOwned(entry, journal.target_id).catch(() => null);
    }
    if (!connection && !needsBinding && !refreshConversation && conversationURL(journal.conversation_url)) connection = await newOwned(entry, journal.conversation_url);
    if (!connection) throw coded('RECOVERY_UNAVAILABLE', '未能找回本批的软件标签页或已保存会话地址，已暂停以避免重复提交。');
    journal.target_id = connection.targetId;
    journal.browser_ws = entry.endpoint.browserWSEndpoint;
    const save = () => writeJson(path.join(request.request_dir, 'journal.json'), journal);
    return withConnection(connection, async page => {
      await deps.promptReady(connection.client.Runtime, 30000, quiet);
      if (needsBinding) {
        const prompt = await promptForJournal(request, journal);
        const deadline = deps.recoveryNow() + Math.min(30000, Math.max(1, request.timeout_seconds || 900) * 1000);
        let bindingError;
        do {
          try { await bindSubmission(request, journal, connection.client.Runtime, prompt, 0); bindingError = null; break; }
          catch (error) { if (error.code !== 'RECOVERY_IDENTITY_UNVERIFIED') throw error; bindingError = error; }
          const remaining = deadline - deps.recoveryNow(); if (remaining <= 0) break;
          await deps.recoveryPause(Math.min(250, remaining));
        } while (deps.recoveryNow() <= deadline);
        if (bindingError) throw bindingError;
      }
      await waitForRecoveryIdentity(request, journal, page, connection.client.Runtime);
      if (refreshConversation) {
        const current = await deps.userTurn(connection.client.Runtime);
        if (current.url !== journal.conversation_url || !current.messageId || typeof current.text !== 'string' ||
            browserPromptFingerprint(current.text, current.messageId) !== journal.submitted_prompt_hash ||
            !normalizedTurnMatches(current, journal) || !newTurnMatchesBaseline(current, journal)) {
          throw coded('RECOVERY_IDENTITY_UNVERIFIED', '刷新前原请求身份发生变化，已停止；没有刷新或重发。');
        }
        if (current.hasDraft) throw coded('RECOVERY_DRAFT_PRESENT', '原会话输入框有未发送草稿，已停止刷新以保留内容。');
        // An explicit recovery action may refresh this verified owned page once
        // to restore a stalled connection. It never submits or changes chats.
        await page.goto(journal.conversation_url);
        await deps.promptReady(connection.client.Runtime, 30000, quiet);
        await waitForRecoveryIdentity(request, journal, page, connection.client.Runtime);
      }
      return capture(request, journal, connection, save, task);
    });
  }
  async function translate(request) {
    const existing = await readJournal(request.request_dir);
    if (existing?.dispatch_started) return recover(request);
    const task = await loadTask(request, existing);
    await assertNoPendingBatch(request, task);
    const entry = connected(request.state_dir);
    const prompt = await fs.readFile(localPath(request.prompt_file, 'prompt_file'), 'utf8');
    if (!prompt.trim()) throw coded('INVALID_REQUEST', 'Empty translation prompt.');
    const output = localPath(request.output_file, 'output_file');
    const prior = await readJson(path.join(request.state_dir, 'active-request.json'));
    if (prior?.request_dir && prior.request_dir !== request.request_dir) {
      const legacy = await readJournal(prior.request_dir);
      if (legacy?.dispatch_started && legacy.status !== 'complete' && alive(legacy.oracle_pid)) throw coded('BROWSER_BUSY', '上一批旧版翻译仍在运行，请先恢复原文件。');
    }
    let connection;
    if (task.target_id && task.browser_ws === entry.endpoint.browserWSEndpoint && entry.owned.has(task.target_id)) {
      connection = await attachOwned(entry, task.target_id).catch(() => null);
    }
    if (!connection && conversationURL(task.conversation_url)) connection = await newOwned(entry, task.conversation_url);
    if (!connection) {
      // Settings are saved before the first dispatch journal. A task that never
      // got that far may retain a closed tab from a failed picker in an older
      // version; replacing it is safe only while all submission markers are absent.
      const pristine = task.model === null && task.effort === null && !task.conversation_url && !task.last_request_dir && !task.pending_request_dir &&
        !task.last_user_id && !task.last_user_fingerprint && !task.last_user_ids && !task.last_user_count && !existing;
      if (!pristine) throw coded('TASK_CONVERSATION_UNAVAILABLE', '本 PDF 的原聊天暂不可用且没有可恢复地址，不能改为新聊天继续。');
      connection = await newOwned(entry);
    }
    task.target_id = connection.targetId;
    task.browser_ws = entry.endpoint.browserWSEndpoint;
    await saveTask(request, task);
    return withConnection(connection, async page => {
      const { Runtime, Input } = connection.client;
      await deps.promptReady(Runtime, 30000, quiet);
      if (task.last_request_dir) {
        if (!task.last_user_fingerprint || !conversationURL(task.conversation_url)) throw coded('TASK_PREDECESSOR_UNVERIFIED', '上一批缺少可核验的会话和消息记录，不能在不确定的聊天中继续。');
        const priorJournal = await readJournal(task.last_request_dir);
        const priorIdentity = { ...(priorJournal?.task_id === task.task_id ? priorJournal : {}),
          submitted_prompt_hash: task.last_user_fingerprint, conversation_url: task.conversation_url,
          ...(Array.isArray(task.last_user_ids) ? { previous_user_ids: task.last_user_ids.slice(0, -1) } : {}) };
        await waitForRecoveryIdentity({ ...request, request_dir: task.last_request_dir, prompt_file: path.join(task.last_request_dir, 'prompt.txt') }, priorIdentity, page, Runtime);
        if (task.last_user_fingerprint !== priorIdentity.submitted_prompt_hash) {
          task.last_user_fingerprint = priorIdentity.submitted_prompt_hash;
          await saveTask(request, task);
        }
      }
      const selection = task.model
        ? await deps.ui.catalog(page)
        : await deps.ui.catalog(page, request.model || undefined, request.effort || undefined);
      if (task.model && (selection.model !== task.model || effortKey(selection.effort || '') !== effortKey(task.effort || ''))) {
        throw coded('TASK_SETTINGS_CHANGED', '网页模型或推理强度与本 PDF 固定设置不一致，已暂停发送。');
      }
      if (!task.model) { task.model = selection.model; task.effort = selection.effort || ''; await saveTask(request, task); }
      await deps.ui.ensureChat(page);
      const previousIds = await deps.readUserIds(Runtime, 5000);
      const baseline = (await shellSnapshot(Runtime))?.turnCount ?? await evaluation(Runtime, buildConversationTurnCountExpression());
      const priorTurn = await deps.userTurn(Runtime);
      if (!Array.isArray(previousIds) || previousIds.some(id => typeof id !== 'string' || !id) || new Set(previousIds).size !== previousIds.length ||
          !Number.isInteger(baseline) || baseline < 0 || priorTurn.count !== previousIds.length || priorTurn.hasDraft || priorTurn.generating) {
        throw coded('TASK_PREDECESSOR_UNVERIFIED', '提交前消息基线或输入框状态不明确，已暂停发送。');
      }
      if (task.last_request_dir) {
        if (priorTurn.url !== task.conversation_url || priorTurn.messageId !== previousIds.at(-1) ||
            browserPromptFingerprint(priorTurn.text, priorTurn.messageId) !== task.last_user_fingerprint ||
            (Array.isArray(task.last_user_ids) && JSON.stringify(previousIds) !== JSON.stringify(task.last_user_ids))) {
          throw coded('TASK_PREDECESSOR_UNVERIFIED', '原聊天在上一批完成后发生变化，不能附加下一批。');
        }
      } else if (previousIds.length || baseline !== 0) throw coded('NEW_CHAT_REQUIRED', '首次批次的软件标签页不是空白新聊天，已暂停。');
      const journal = { version: 3, transport: 'native-page', task_id: task.task_id, status: 'dispatching', dispatch_started: true,
        created_at: new Date().toISOString(), session_id: `pdf-${randomUUID()}`, target_id: connection.targetId,
        browser_ws: entry.endpoint.browserWSEndpoint, port: entry.endpoint.port, prompt_hash: hash(prompt), prompt_normalized_hash: hash(normalizedPrompt(prompt)),
        output_file: output, model: selection.model, effort: selection.effort, baseline_turns: baseline,
        previous_user_ids: previousIds, previous_user_count: previousIds.length,
        conversation_url: task.conversation_url, local_conversation_id: task.local_conversation_id || null, prompt_submitted: false };
      const save = () => writeJson(path.join(request.request_dir, 'journal.json'), journal);
      task.pending_request_dir = request.request_dir;
      await saveTask(request, task);
      await save(); // A crash from here is ambiguous: never resubmit.
      await writeJson(path.join(request.state_dir, 'active-request.json'), { request_dir: request.request_dir, controller_pid: process.pid });
      try {
        // Use only Oracle's page-bound helpers. runBrowserMode performs global
        // cookie cleanup and tab discovery; neither belongs in a shared browser.
        // This call is intentionally never wrapped in a submission retry.
        // No Page argument: Oracle otherwise calls bringToFront for each batch.
        await deps.submit({ runtime: Runtime, input: textSubmissionInput(Input), baselineTurns: baseline,
          inputTimeoutMs: 30000, attachmentNames: [], webSearch: false,
          onPromptSubmitted: async () => { journal.prompt_submitted = true; journal.status = 'running'; await save(); },
        }, prompt, quiet);
        await bindSubmission(request, journal, Runtime, prompt, 30000);
        await save();
        return await capture(request, journal, connection, save, task);
      } catch (error) {
        // The callback may run before the user turn commits. Harvest its identity
        // after a failed wait when possible, but never issue another submission.
        if (!journal.submitted_prompt_hash) await bindSubmission(request, journal, Runtime, prompt, 0).catch(bindingError => { if (bindingError.diagnostic) error.diagnostic = bindingError.diagnostic; });
        const current = await deps.userTurn(Runtime).catch(() => null);
        if (!journal.conversation_url && journal.submitted_prompt_hash && conversationURL(current?.url) &&
            browserPromptFingerprint(current.text, current.messageId) === journal.submitted_prompt_hash && newTurnMatchesBaseline(current, journal)) {
          journal.conversation_url = current.url;
        }
        journal.status = 'paused';
        await save();
        throw error;
      }
    });
  }
  async function dispatch(request) {
    if (!request || typeof request !== 'object') throw coded('INVALID_REQUEST', 'JSON request object required.');
    request.state_dir = localPath(request.state_dir, 'state_dir');
    if (['translate', 'recover', 'diagnose_request'].includes(request.op)) request.request_dir = localPath(request.request_dir, 'request_dir');
    const ongoing = active.get(request.state_dir);
    if (ongoing) {
      if (ongoing === request.request_dir && pending.has(ongoing)) {
        // The original caller can complete and clear these maps while the
        // journal is read. Keep the exact work and owner we joined, then
        // validate against that owner before returning its result.
        const work = pending.get(ongoing);
        const taskId = activeTasks.get(ongoing);
        const requestedTask = requestTaskId(request, await readJournal(request.request_dir));
        if (taskId !== requestedTask) throw coded('TASK_OWNERSHIP_MISMATCH', '该批次正在另一个 PDF 任务中执行，不能复用其结果。');
        return work;
      }
      throw coded('BROWSER_BUSY', '上一批仍在浏览器中运行或收取回答，请先继续原文件。');
    }
    if (request.op === 'login') return login(request);
    if (request.op === 'diagnose_request') return diagnoseRequest(request);
    if (['translate', 'recover'].includes(request.op)) {
      const taskId = requestTaskId(request, await readJournal(request.request_dir));
      // The journal read yields. Recheck before taking ownership so two callers
      // that arrived together cannot both start a submission after that await.
      const raced = active.get(request.state_dir);
      if (raced) {
        if (raced === request.request_dir && pending.has(raced)) {
          if (activeTasks.get(raced) !== taskId) throw coded('TASK_OWNERSHIP_MISMATCH', '该批次正在另一个 PDF 任务中执行。');
          return pending.get(raced);
        }
        throw coded('BROWSER_BUSY', '上一批仍在运行，请先恢复原批次。');
      }
      active.set(request.state_dir, request.request_dir);
      activeTasks.set(request.request_dir, taskId);
      const work = (request.op === 'translate' ? translate(request) : recover(request));
      pending.set(request.request_dir, work);
      try { return await work; }
      finally { pending.delete(request.request_dir); active.delete(request.state_dir); activeTasks.delete(request.request_dir); }
    }
    if (!['status', 'catalog', 'select'].includes(request.op)) throw coded('INVALID_REQUEST', 'Unknown operation.');
    const entry = connected(request.state_dir);
    const connection = await setupConnection(entry, request.op !== 'status');
    if (!connection) return { ok: true, status: 'needs_login', message: '翻译软件的标签页已关闭，请点击“连接已登录的 Chrome”重新打开。' };
    return withConnection(connection, async page => {
      if (request.op === 'status') return deps.ui.connectionStatus(await deps.ui.inspectPage(page));
      await prepareOwnedHome(entry, connection);
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
