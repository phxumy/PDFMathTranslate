import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright-core';
import { userTurn, readAssistantCompletionDiagnostic } from '../bridge.mjs';
import { browserPromptFingerprint } from '../node_modules/@steipete/oracle/dist/src/browser/promptFingerprint.js';

const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync);
const digest = value => createHash('sha256').update(value).digest('hex');

test('assistant diagnostic is read-only, scoped to the verified turn, separates code controls and never returns body text', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `
      <aside><button data-testid="copy-turn-action-button">PRIVATE SIDEBAR</button></aside>
      <article data-testid="conversation-turn-0"><div data-message-author-role="user" data-message-id="user-id">Known prompt.</div></article>
      <article data-testid="conversation-turn-1">
        <div data-message-author-role="assistant" data-message-id="assistant-id">
          <div class="markdown"><p>SECRET ANSWER BODY</p><div class="code-block"><div><button data-testid="copy-code-button" aria-label="複製程式碼">Copy code</button></div><pre><code>{"private":"answer"}</code></pre></div></div>
          <div aria-busy="true" role="status" style="display:none">SECRET STATUS</div>
        </div>
        <div class="action-bar"><button data-testid="copy-turn-action-button" aria-label="複製">Copy</button><button data-testid="good-response-turn-action-button" aria-label="好回覆">Good</button></div>
      </article>
      <footer class="answer-footer"><button data-testid="bad-response-turn-action-button" aria-label="不好回覆">Bad</button></footer>
      <form><button data-testid="stop-button" style="display:none">Stop</button><button data-testid="send-button">Send</button></form>
      <div aria-hidden="true" inert style="opacity:0"><button data-testid="composer-stop-button" aria-label="Stop generating" disabled>Stop</button></div>
      <script>window.clicked=0;document.addEventListener('click',()=>window.clicked++);</script>` }));
    await page.goto('https://chatgpt.com/c/owned-fixture');
    const session = await page.context().newCDPSession(page);
    const runtime = { evaluate: parameters => session.send('Runtime.evaluate', parameters) };
    const turn = await userTurn(runtime);
    const journal = { submitted_prompt_hash: browserPromptFingerprint(turn.text, turn.messageId), conversation_url: turn.url,
      prompt_normalized_hash: digest('Known prompt.'), previous_user_ids: [], baseline_turns: 0 };
    const result = await readAssistantCompletionDiagnostic(runtime, turn, journal);
    assert.equal(result.status, 'inspected'); assert.equal(result.turn_count, 2); assert.equal(result.assistant_role_count, 1);
    assert.equal(result.last_assistant_index, 1); assert.equal(result.last_assistant_id_present, true);
    assert.equal(result.buttons_total, 3); assert.equal(result.code_button_count, 1);
    assert.equal(result.oracle_completion_count, 2); assert.equal(result.oracle_completion_outside_code_count, 2);
    assert.equal(result.buttons[0].in_code, true); assert.equal(result.buttons[1].in_code, false);
    assert.equal(result.buttons[0].aria_label, '複製程式碼'); assert.equal(result.buttons[1].aria_label, '複製');
    assert.equal(result.body_selectors.pre, 1); assert.equal(result.body_selectors.markdown, 1);
    // Preserve Oracle's current node-only visibility result while exposing that
    // the second control is hidden by an ancestor; diagnostics do not fix it.
    assert.equal(result.visible_stop, true); assert.equal(result.visible_aria_busy_count, 0); assert.equal(result.visible_role_status_count, 0);
    assert.equal(result.stop_nodes_total, 2);
    assert.equal(result.stop_nodes[0].visible, false); assert.equal(result.stop_nodes[0].display, 'none');
    assert.equal(result.stop_nodes[1].disabled, true); assert.equal(result.stop_nodes[1].aria_label, 'Stop generating');
    assert.equal(result.stop_nodes[1].ancestor_hidden, true); assert.equal(result.stop_nodes[1].ancestor_aria_hidden, true); assert.equal(result.stop_nodes[1].ancestor_inert, true);
    assert.ok(result.stop_nodes[1].rect.width > 0); assert.ok(result.stop_nodes[1].ancestors.length <= 5);
    assert.equal(result.page_completion_count, 4);
    const footer = result.page_completion_controls.find(node => node.data_testid === 'bad-response-turn-action-button');
    assert.equal(footer.nearest_turn_index, null); assert.equal(footer.preceding_turn_index, 1); assert.equal(footer.inside_last_assistant_turn, false);
    const inside = result.page_completion_controls.find(node => node.data_testid === 'good-response-turn-action-button');
    assert.equal(inside.nearest_turn_index, 1); assert.equal(inside.inside_last_assistant_turn, true);
    assert.equal(result.last_assistant_ancestors[0].tag, 'article'); assert.equal(result.last_assistant_ancestors[0].data_testid, 'conversation-turn-1');
    assert.ok(result.last_assistant_ancestors.length <= 5);
    assert.doesNotMatch(JSON.stringify(result), /SECRET|PRIVATE|Known prompt|private.*answer|user-id|assistant-id|https:\/\//);
    assert.equal(await page.evaluate(() => window.clicked), 0);
    await page.evaluate(() => { document.querySelector('[aria-busy]').style.display = ''; document.querySelector('[data-testid="stop-button"]').style.display = ''; });
    const active = await readAssistantCompletionDiagnostic(runtime, turn, journal);
    assert.equal(active.visible_stop, true); assert.equal(active.visible_aria_busy_count, 1); assert.equal(active.visible_role_status_count, 1);
    await page.evaluate(() => { document.querySelector('[data-message-author-role="user"]').textContent = 'Different request'; });
    assert.deepEqual(await readAssistantCompletionDiagnostic(runtime, turn, journal), { status: 'identity_changed' });
    assert.equal(await page.evaluate(() => window.clicked), 0);
  } finally { await browser.close(); }
});

test('assistant diagnostic refuses before evaluating when the saved URL, raw fingerprint or prior baseline is unverified', async () => {
  const turn = { url: 'https://chatgpt.com/c/owned-fixture', count: 1, messageId: 'user-id', text: 'Known prompt.', userIds: ['user-id'] };
  const journal = { submitted_prompt_hash: browserPromptFingerprint(turn.text, turn.messageId), conversation_url: turn.url,
    prompt_normalized_hash: digest(turn.text), previous_user_ids: [], baseline_turns: 0 };
  let reads = 0;
  const runtime = { evaluate: async () => { reads++; throw new Error('must not inspect assistant'); } };
  for (const invalid of [{ ...journal, submitted_prompt_hash: null }, { ...journal, conversation_url: 'https://chatgpt.com/c/different' },
    { ...journal, submitted_prompt_hash: 'wrong' }, { ...journal, previous_user_ids: ['earlier'], baseline_turns: 2 }]) {
    assert.deepEqual(await readAssistantCompletionDiagnostic(runtime, turn, invalid), { status: 'request_identity_unverified' });
  }
  assert.equal(reads, 0);
});
