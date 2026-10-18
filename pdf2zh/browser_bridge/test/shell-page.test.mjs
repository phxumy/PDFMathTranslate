import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright-core';
import { shellSnapshot, shellSubmit, shellWaitAnswer } from '../shell-page.mjs';
import { userTurn } from '../bridge.mjs';

const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync);
const html = `<main><div data-chatgpt-conversation-selection-target><div data-turn-key="u1">
 <div data-chatgpt-search-unit-key="0:user" data-chatgpt-search-message-ids="u1">
  <h4 data-conversation-role="user">你说：</h4><div data-user-message-bubble><div data-search-result-target><p>Translate <code>text</code>.</p></div><button>显示更多</button></div>
  <div class="turn-action-controls"><button aria-label="复制"></button><button aria-label="评价回复"></button></div>
 </div><div data-chatgpt-search-unit-key="1:assistant" data-chatgpt-search-message-ids="a1 a1">
  <h4 data-conversation-role="assistant">ChatGPT说：</h4><div data-chatgpt-selection-message-id="a1" data-chatgpt-selection-conversation-id="owned">
   <div data-markdown-text-style="assistant-message"><p>{"translations":["译文"]}</p><div class="turn-action-controls"><button aria-label="复制"></button><button aria-label="评价回复"></button></div></div>
  </div></div><div class="turn-action-controls" id="footer"><button aria-label="复制">Copy</button><button aria-label="重新生成回复">Retry</button></div>
 </div></div><form data-chatgpt-composer><div data-composer-markdown contenteditable="true" role="textbox"></div><button type="submit">发送</button></form></main>`;

function snapshots(states) {
 const sequence = [...states];
 return { evaluate: async () => ({ result: { value: sequence.length > 1 ? sequence.shift() : sequence[0] } }) };
}
const submitted = { shell: true, url: 'https://chatgpt.com/', count: 1, messageId: 'u1', text: 'The exact submitted prompt.', userIds: ['u1'], turnCount: 1, assistant: null };
const answered = { ...submitted, url: 'https://chatgpt.com/c/created', turnCount: 2, assistant: { id: 'a1', text: '{"translations":["译文"]}', complete: true } };

test('first shell reply permits its own home-to-conversation transition without changing the submitted turn', async () => {
 const answer = await shellWaitAnswer(snapshots([submitted, answered]), 2500, 0);
 assert.equal(answer.text, answered.assistant.text);
});

test('first shell reply survives a local-chatgpt client URL and composer remount before the permanent URL', async () => {
 const pending = { ...submitted, url: 'https://chatgpt.com/c/local-chatgpt%3Aclient-id' };
 assert.equal((await shellWaitAnswer(snapshots([pending, null, answered]), 2500, 0)).text, answered.assistant.text);
 assert.equal((await shellWaitAnswer(snapshots([submitted, pending, answered]), 2500, 0)).text, answered.assistant.text);
 await assert.rejects(shellWaitAnswer(snapshots([pending, { ...pending, url: 'https://chatgpt.com/c/local-chatgpt%3Aanother-client' }]), 100, 0), { code: 'RECOVERY_IDENTITY_UNVERIFIED' });
 await assert.rejects(shellWaitAnswer(snapshots([answered, pending]), 100, 0), { code: 'RECOVERY_IDENTITY_UNVERIFIED' });
});

test('shell reply remains bound to the turn verified by the caller across transient missing DOM', async () => {
 const answer = await shellWaitAnswer(snapshots([null, answered]), 2500, 0, undefined, submitted);
 assert.equal(answer.messageId, 'a1');
 await assert.rejects(shellWaitAnswer(snapshots([{ ...answered, userIds: ['other'], messageId: 'other' }]), 10, 0, undefined, submitted), { code: 'RECOVERY_IDENTITY_UNVERIFIED' });
});

test('shell reply still rejects changed requests, existing conversation navigation, and a second new URL', async () => {
 for (const state of [{ ...answered, text: 'Changed prompt.' }, { ...answered, userIds: ['other'], messageId: 'other' }, { ...answered, count: 2 }, { ...answered, url: 'https://example.com/c/created' }]) {
  await assert.rejects(shellWaitAnswer(snapshots([submitted, state]), 100, 0), { code: 'RECOVERY_IDENTITY_UNVERIFIED' });
 }
 await assert.rejects(shellWaitAnswer(snapshots([{ ...answered, url: 'https://chatgpt.com/c/original' }, answered]), 100, 0), { code: 'RECOVERY_IDENTITY_UNVERIFIED' });
 await assert.rejects(shellWaitAnswer(snapshots([submitted, answered, { ...answered, url: 'https://chatgpt.com/c/another' }]), 1200, 0), { code: 'RECOVERY_IDENTITY_UNVERIFIED' });
});

test('shell reports a completed empty reply without resending and allows delayed rendered text', async () => {
 const empty = { ...answered, assistant: { ...answered.assistant, text: '' } };
 await assert.rejects(shellWaitAnswer(snapshots([empty]), 4000, 0, 'created'), { code: 'EMPTY_RESPONSE' });
 assert.equal((await shellWaitAnswer(snapshots([empty, empty, answered]), 2500, 0, 'created')).text, answered.assistant.text);
 await assert.rejects(shellWaitAnswer(snapshots([{ ...empty, assistant: { ...empty.assistant, complete: false } }]), 100, 0, 'created'), { code: 'ASSISTANT_TIMEOUT' });
});

test('new shell reads exact user content and only completed assistant action bars; rejects streaming and wrong conversation', { skip: !chrome }, async () => {
 const browser = await chromium.launch({ executablePath: chrome, headless: true });
 try {
  const page = await browser.newPage();
  await page.route('**/*', r => r.fulfill({ contentType: 'text/html; charset=utf-8', body: html }));
  await page.goto('https://chatgpt.com/c/owned');
  const cdp = await page.context().newCDPSession(page);
  const runtime = { evaluate: p => cdp.send('Runtime.evaluate', p) };
  const turn = await userTurn(runtime);
  assert.deepEqual(turn.userIds, ['u1']); assert.equal(turn.inlineCodeText.trim(), 'Translate `text`.');
  assert.equal(turn.text, 'Translate text.'); assert.equal(turn.assistant.complete, true);
  assert.equal((await shellWaitAnswer(runtime, 2500, 0, 'owned')).text, '{"translations":["译文"]}');
  await page.evaluate(() => document.querySelector('#footer').remove());
  assert.equal((await shellSnapshot(runtime)).assistant.complete, false, 'user/code copy controls do not mark completion');
  await assert.rejects(shellWaitAnswer(runtime, 10, 0, 'owned'), { code: 'ASSISTANT_TIMEOUT' });
  await page.evaluate(() => { document.querySelector('form').insertAdjacentHTML('beforeend', '<button aria-label="停止">Stop</button>'); });
  assert.equal((await shellSnapshot(runtime)).generating, true);
  await assert.rejects(shellSubmit({ runtime, input: {} }, 'new'), { code: 'COMPOSER_NOT_EMPTY' });
  await page.evaluate(() => document.querySelector('[data-chatgpt-selection-conversation-id]').setAttribute('data-chatgpt-selection-conversation-id', 'another'));
  assert.equal((await shellSnapshot(runtime)).assistant, null);
 } finally { await browser.close(); }
});

test('shell binds a captured local client ID to its permanent URL while rejecting other conversation IDs', { skip: !chrome }, async () => {
 const browser = await chromium.launch({ executablePath: chrome, headless: true });
 try {
  const page = await browser.newPage();
  const local = 'local-chatgpt:client-id';
  await page.route('**/*', r => r.fulfill({ contentType: 'text/html; charset=utf-8', body: html.replace('data-chatgpt-selection-conversation-id="owned"', `data-chatgpt-selection-conversation-id="${local}"`) }));
  await page.goto('https://chatgpt.com/c/owned');
  const cdp = await page.context().newCDPSession(page);
  const runtime = { evaluate: p => cdp.send('Runtime.evaluate', p) };
  const turn = await userTurn(runtime);
  assert.equal((await shellSnapshot(runtime)).assistant, null);
  assert.equal((await shellSnapshot(runtime, undefined, 'local-chatgpt:another')).assistant, null);
  assert.equal((await shellSnapshot(runtime, undefined, local)).assistant.complete, true);
  const first = await shellWaitAnswer(runtime, 2500, 0, undefined, { ...turn, url: 'https://chatgpt.com/c/local-chatgpt%3Aclient-id' });
  assert.equal(first.localConversationId, local);
  assert.equal(first.text, '{"translations":["译文"]}');
  assert.equal((await shellWaitAnswer(runtime, 2500, 0, 'owned', { ...turn, localConversationId: local })).text, first.text);
  await page.evaluate(() => document.querySelector('[data-chatgpt-selection-conversation-id]').setAttribute('data-chatgpt-selection-conversation-id', 'another-permanent-chat'));
  assert.equal((await shellSnapshot(runtime, undefined, local)).assistant, null);
 } finally { await browser.close(); }
});

test('new shell sends exactly once in the existing conversation, preserves other drafts, refuses changed input', { skip: !chrome }, async () => {
 const browser = await chromium.launch({ executablePath: chrome, headless: true });
 try {
  const page = await browser.newPage();
  await page.route('**/*', r => r.fulfill({ contentType: 'text/html; charset=utf-8', body: html }));
  await page.goto('https://chatgpt.com/c/owned');
  const cdp = await page.context().newCDPSession(page);
  const runtime = { evaluate: p => cdp.send('Runtime.evaluate', p) };
  await page.evaluate(() => { window.sent = 0; document.querySelector('form').addEventListener('submit', e => { e.preventDefault(); window.sent++; }); });
  let callbacks = 0;
  const input = { insertText: p => cdp.send('Input.insertText', p) };
  await shellSubmit({ runtime, input, onPromptSubmitted: () => callbacks++ }, 'Second batch\nTranslate this.');
  assert.equal(await page.evaluate(() => window.sent), 1); assert.equal(callbacks, 1);
  assert.equal(page.url(), 'https://chatgpt.com/c/owned');
  await assert.rejects(shellSubmit({ runtime, input }, 'duplicate'), { code: 'COMPOSER_NOT_EMPTY' });
  assert.equal(await page.evaluate(() => window.sent), 1);
  await page.evaluate(() => { document.querySelector('[contenteditable]').textContent = ''; });
  await assert.rejects(shellSubmit({ runtime, input: { insertText: () => input.insertText({ text: 'changed' }) } }, 'expected'), { code: 'COMPOSER_CHANGED' });
  assert.equal(await page.evaluate(() => window.sent), 1);
 } finally { await browser.close(); }
});
