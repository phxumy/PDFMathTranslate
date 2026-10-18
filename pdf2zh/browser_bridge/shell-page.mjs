// Page-bound adapter for the ChatGPT browser app shell. No hidden API calls.
import { browserPromptFingerprint } from './node_modules/@steipete/oracle/dist/src/browser/promptFingerprint.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const fail = (code, message) => Object.assign(new Error(message), { code });
// The shell virtualizes old turns. Only a contiguous suffix of a verified
// history may disappear; inserted, reordered, duplicate or unknown IDs fail.
export function userIdsAreSuffix(observed, expected) {
  const valid = ids => Array.isArray(ids) && ids.every(id => typeof id === 'string' && id) && new Set(ids).size === ids.length;
  return valid(observed) && valid(expected) && observed.length <= expected.length &&
    (observed.length > 0 || expected.length === 0) &&
    observed.every((id, index) => id === expected[expected.length - observed.length + index]);
}
async function evaluate(runtime, expression) {
  const result = await runtime.evaluate({ expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw fail('PAGE_SCRIPT_ERROR', 'ChatGPT 页面状态读取失败。');
  return result.result?.value;
}
export function shellDOM(serialize, localConversationId = null) {
  const form = document.querySelector('form[data-chatgpt-composer]');
  if (!form) return null;
  const editor = form.querySelector('[data-composer-markdown][contenteditable="true"]');
  const scope = document.querySelector('[data-chatgpt-conversation-selection-target]');
  const units = [...(scope?.querySelectorAll('[data-chatgpt-search-unit-key]') || [])];
  const role = node => node.querySelector('[data-conversation-role]')?.getAttribute('data-conversation-role') || node.getAttribute('data-chatgpt-search-unit-key')?.split(':').at(-1);
  const id = node => {
    const ids = [...new Set((node?.getAttribute('data-chatgpt-search-message-ids') || '').split(/\s+/).filter(Boolean))];
    return ids.length === 1 ? ids[0] : null;
  };
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    for (let p = node; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (p.hidden || p.inert || p.getAttribute('aria-hidden') === 'true' || s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    }
    return true;
  };
  const users = units.filter(node => role(node) === 'user');
  const last = users.at(-1);
  const body = last?.querySelector('[data-user-message-bubble] [data-search-result-target]');
  const structure = serialize ? serialize(body) : {};
  const buttons = [...form.querySelectorAll('button')];
  const stop = buttons.some(b => visible(b) && (/^stop(?:\s|$)|^停止/i.test(b.getAttribute('aria-label') || '') || /stop/.test(b.getAttribute('data-testid') || '')));
  const assistant = units.at(-1);
  const assistantBody = role(assistant || document.createElement('div')) === 'assistant' ? assistant?.querySelector('[data-markdown-text-style="assistant-message"]') : null;
  const group = last?.closest('[data-turn-key]');
  const assistantId = id(assistant);
  const selection = assistantBody?.closest('[data-chatgpt-selection-message-id]');
  const route = location.pathname.match(/^\/c\/([^/]+)$/)?.[1];
  let conversation;
  try { conversation = route && decodeURIComponent(route); } catch { /* Unrecognized routes cannot bind an answer. */ }
  const selectedConversation = selection?.getAttribute('data-chatgpt-selection-conversation-id');
  const matchingConversation = selectedConversation === conversation ||
    (/^local-chatgpt:[a-zA-Z0-9-]+$/.test(localConversationId || '') && selectedConversation === localConversationId &&
      location.origin === 'https://chatgpt.com' && /^\/c\/[a-zA-Z0-9-]+$/.test(location.pathname));
  const sameTurn = !!(last && assistantBody && assistantId && group?.contains(assistant) && (last.compareDocumentPosition(assistant) & 4) &&
    selection?.getAttribute('data-chatgpt-selection-message-id') === assistantId);
  const observedLocalConversation = sameTurn && /^local-chatgpt:[a-zA-Z0-9-]+$/.test(selectedConversation || '') ? selectedConversation : null;
  const associated = sameTurn && matchingConversation;
  const bars = associated ? [...group.querySelectorAll('.turn-action-controls')].filter(bar =>
    !bar.closest('[data-chatgpt-search-unit-key],pre,code') && (assistant.compareDocumentPosition(bar) & 4)) : [];
  const complete = bars.some(bar => {
    const labels = [...bar.querySelectorAll('button')].filter(visible).map(b => b.getAttribute('aria-label') || '');
    return labels.some(s => /^(copy|复制|複製)$/i.test(s)) && labels.some(s => /^(retry|regenerate|rate response)|^(重新|重试|重試|评价|評價)/i.test(s));
  });
  const busy = [...(group?.querySelectorAll('[aria-busy="true"]') || []), ...form.querySelectorAll('[aria-busy="true"]')].some(visible);
  return { shell: true, url: location.href, count: users.length, messageId: id(last), text: body?.textContent,
    renderedText: body?.innerText, structuredText: structure.text, inlineCodeText: structure.inlineCodeText,
    ...Object.fromEntries(['inlineCodeCount', 'buttonCount', 'controlCount', 'tagCounts'].map(k => [k, structure[k]])),
    userIds: users.map(id), localConversationId: observedLocalConversation, hasDraft: !!editor?.textContent?.trim(), generating: stop || busy,
    editorReady: !!editor && visible(editor), turnCount: units.length,
    assistant: associated ? { id: assistantId, text: assistantBody.innerText || assistantBody.textContent, complete: complete && !stop && !busy } : null };
}
export const shellSnapshot = (runtime, serialize, localConversationId) => evaluate(runtime, `(${shellDOM.toString()})(${serialize ? serialize.toString() : 'undefined'},${JSON.stringify(localConversationId || null)})`);
export async function shellPromptReady(runtime, timeout) {
  const deadline = Date.now() + timeout;
  do {
    if ((await shellSnapshot(runtime))?.editorReady) return;
    await sleep(200);
  } while (Date.now() < deadline);
  throw fail('COMPOSER_UNAVAILABLE', '原聊天输入框尚未就绪，请稍后恢复。');
}
export async function shellFingerprint(runtime, previous, timeout = 0) {
  const deadline = Date.now() + timeout;
  do {
    const turn = await shellSnapshot(runtime);
    if (turn?.messageId && typeof turn.text === 'string' && !previous?.includes(turn.messageId)) return browserPromptFingerprint(turn.text, turn.messageId);
    if (Date.now() >= deadline) return undefined;
    await sleep(100);
  } while (true);
}
export async function shellSubmit({ runtime, input, onPromptSubmitted }, prompt) {
  const state = await shellSnapshot(runtime);
  if (!state?.editorReady || state.hasDraft || state.generating) throw fail('COMPOSER_NOT_EMPTY', '原聊天有草稿或仍在生成，已停止发送。');
  const originalIds = JSON.stringify(state.userIds);
  await evaluate(runtime, `document.querySelector('form[data-chatgpt-composer] [data-composer-markdown][contenteditable="true"]').focus()`);
  await input.insertText({ text: prompt });
  const deadline = Date.now() + 5000;
  let clicked = false;
  do {
    const result = await evaluate(runtime, `(() => {
      const state = (${shellDOM.toString()})();
      if (location.href !== ${JSON.stringify(state.url)} || JSON.stringify(state?.userIds) !== ${JSON.stringify(originalIds)} || state.generating) return 'changed';
      const form = document.querySelector('form[data-chatgpt-composer]');
      const editor = form.querySelector('[data-composer-markdown][contenteditable="true"]');
      const normalized = s => s.replace(/\\s+/g, ' ').trim();
      if (normalized(editor.innerText || editor.textContent) !== normalized(${JSON.stringify(prompt)})) return 'draft';
      const send = form.querySelector('button[type="submit"]');
      if (!send || send.disabled || send.getAttribute('aria-disabled') === 'true') return 'waiting';
      send.click(); return 'sent';
    })()`);
    if (result === 'sent') { clicked = true; break; }
    if (result !== 'waiting') throw fail('COMPOSER_CHANGED', '发送前页面或草稿发生变化，已暂停；不会重复发送。');
    await sleep(100);
  } while (Date.now() < deadline);
  if (!clicked) throw fail('SEND_UNAVAILABLE', '发送按钮尚未就绪，已保留草稿；不会重复发送。');
  await onPromptSubmitted?.();
}
export async function shellWaitAnswer(runtime, timeout, baseline, conversationId, submittedTurn) {
  // Start with the turn already verified by capture, so a remount between the
  // two reads cannot replace the request that this wait is allowed to accept.
  const initial = submittedTurn || await shellSnapshot(runtime);
  const verifiedIds = initial?.verifiedUserIds || initial?.userIds;
  const sameTurn = state => state.messageId === initial.messageId && state.text === initial.text &&
    state.count === state.userIds?.length && userIdsAreSuffix(state.userIds, verifiedIds);
  const home = url => url === 'https://chatgpt.com/';
  const local = url => /^https:\/\/chatgpt\.com\/c\/local-chatgpt(?::|%3a)[a-zA-Z0-9-]+$/i.test(url || '');
  const conversation = url => /^https:\/\/chatgpt\.com\/c\/[a-zA-Z0-9-]+$/.test(url || '');
  if (!initial?.messageId || typeof initial.text !== 'string' || !Array.isArray(initial.userIds) ||
      !initial.userIds.length || initial.count !== initial.userIds.length || initial.userIds.at(-1) !== initial.messageId || initial.userIds.some(id => typeof id !== 'string' || !id) ||
      !userIdsAreSuffix(initial.userIds, verifiedIds) || (!home(initial.url) && !local(initial.url) && !conversation(initial.url))) {
    throw fail('RECOVERY_IDENTITY_UNVERIFIED', '无法确认等待回答的原请求身份，已停止读取。');
  }
  let expectedUrl = conversationId ? `https://chatgpt.com/c/${conversationId}` : initial.url;
  let localConversationId = initial.localConversationId || (local(initial.url) ? decodeURIComponent(new URL(initial.url).pathname.slice(3)) : null);
  if (localConversationId && !/^local-chatgpt:[a-zA-Z0-9-]+$/.test(localConversationId)) throw fail('RECOVERY_IDENTITY_UNVERIFIED', '临时聊天标识无法确认，已停止读取。');
  if (conversationId && initial.url !== expectedUrl) throw fail('RECOVERY_IDENTITY_UNVERIFIED', '原请求不在已保存的聊天地址，已停止读取。');
  let prior = null, since = 0, polls = 0;
  const deadline = Date.now() + timeout;
  do {
    const state = await shellSnapshot(runtime, undefined, localConversationId);
    // New chats can render at home, then at a local-chatgpt: client URL, before
    // receiving their permanent address. Every transition must retain the exact
    // submitted turn; a permanent conversation can never change again.
    const canAdvance = url => !conversationId &&
      ((home(expectedUrl) && local(url)) || ((home(expectedUrl) || local(expectedUrl)) && conversation(url)));
    if (state && state.url !== expectedUrl && !canAdvance(state.url)) {
      throw fail('RECOVERY_IDENTITY_UNVERIFIED', '等待回答时原聊天发生变化，已停止读取。');
    }
    if (!state || (!state.userIds?.length && !state.messageId && !state.text)) {
      prior = null; polls = 0;
      await sleep(300);
      continue;
    }
    if (!sameTurn(state)) throw fail('RECOVERY_IDENTITY_UNVERIFIED', '等待回答时原聊天发生变化，已停止读取。');
    if (state.url !== expectedUrl) {
      if (canAdvance(state.url)) expectedUrl = state.url;
      else throw fail('RECOVERY_IDENTITY_UNVERIFIED', '等待回答时原聊天发生变化，已停止读取。');
    }
    if (local(state.url)) {
      const observed = decodeURIComponent(new URL(state.url).pathname.slice(3));
      if (localConversationId && observed !== localConversationId) throw fail('RECOVERY_IDENTITY_UNVERIFIED', '临时聊天发生变化，已停止读取。');
      localConversationId = observed;
    }
    const answer = state.assistant;
    // The answer is bound to the exact submitted user turn by shellDOM. Total
    // mounted nodes need not grow when the oldest turn leaves the viewport.
    if (conversation(state.url) && answer?.complete) {
      const signature = JSON.stringify([state.url, answer]);
      if (signature !== prior) { prior = signature; since = Date.now(); polls = 0; }
      if (++polls >= 3 && Date.now() - since >= 1200) {
        if (answer.text?.trim()) return { text: answer.text, messageId: answer.id, localConversationId };
        if (Date.now() - since >= 3000) throw fail('EMPTY_RESPONSE', '原聊天的回答已结束但正文为空，请在原聊天点击“重新生成”后再恢复此 PDF。本批不会重复发送。');
      }
    } else { prior = null; polls = 0; }
    await sleep(300);
  } while (Date.now() < deadline);
  throw fail('ASSISTANT_TIMEOUT', '等待原回答完成超时，已保留本批，下次只恢复原回答。');
}
