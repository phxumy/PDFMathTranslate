// Page-bound adapter for the ChatGPT browser app shell. No hidden API calls.
import { browserPromptFingerprint } from './node_modules/@steipete/oracle/dist/src/browser/promptFingerprint.js';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const fail = (code, message) => Object.assign(new Error(message), { code });
async function evaluate(runtime, expression) {
  const result = await runtime.evaluate({ expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw fail('PAGE_SCRIPT_ERROR', 'ChatGPT 页面状态读取失败。');
  return result.result?.value;
}
export function shellDOM(serialize) {
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
  const conversation = location.pathname.match(/^\/c\/([a-zA-Z0-9-]+)$/)?.[1];
  const associated = !!(last && assistantBody && assistantId && group?.contains(assistant) && (last.compareDocumentPosition(assistant) & 4) &&
    selection?.getAttribute('data-chatgpt-selection-message-id') === assistantId && selection?.getAttribute('data-chatgpt-selection-conversation-id') === conversation);
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
    userIds: users.map(id), hasDraft: !!editor?.textContent?.trim(), generating: stop || busy,
    editorReady: !!editor && visible(editor), turnCount: units.length,
    assistant: associated ? { id: assistantId, text: assistantBody.innerText || assistantBody.textContent, complete: complete && !stop && !busy } : null };
}
export const shellSnapshot = (runtime, serialize) => evaluate(runtime, `(${shellDOM.toString()})(${serialize ? serialize.toString() : ''})`);
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
export async function shellWaitAnswer(runtime, timeout, baseline, conversationId) {
  const initial = await shellSnapshot(runtime);
  const identity = state => JSON.stringify([state.url, state.userIds, state.text]);
  let prior = null, since = 0, polls = 0;
  const deadline = Date.now() + timeout;
  do {
    const state = await shellSnapshot(runtime);
    if (!state || identity(state) !== identity(initial) || (conversationId && state.url !== `https://chatgpt.com/c/${conversationId}`)) throw fail('RECOVERY_IDENTITY_UNVERIFIED', '等待回答时原聊天发生变化，已停止读取。');
    const answer = state.assistant;
    if (state.turnCount > baseline && answer?.complete && answer.text?.trim()) {
      const signature = JSON.stringify(answer);
      if (signature !== prior) { prior = signature; since = Date.now(); polls = 0; }
      if (++polls >= 3 && Date.now() - since >= 1200) return { text: answer.text, messageId: answer.id };
    } else { prior = null; polls = 0; }
    await sleep(300);
  } while (Date.now() < deadline);
  throw fail('ASSISTANT_TIMEOUT', '等待原回答完成超时，已保留本批，下次只恢复原回答。');
}
