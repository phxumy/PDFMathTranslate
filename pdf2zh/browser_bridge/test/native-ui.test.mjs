import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright-core';
import { createNativePage, inspectPage, inspectCompatibility, connectionStatus, ensureChat, catalog, readChoices, modeFromEvidence } from '../native-ui.mjs';
import { PICKER, effortKey } from '../ui.mjs';

const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync);

async function fixture(html, run, pathname = '/') {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const source = await browser.newPage();
    // Serve all URLs locally: no account, cookie or real ChatGPT request is used.
    await source.route('**/*', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: html }));
    await source.goto(`https://chatgpt.com${pathname}`);
    const session = await source.context().newCDPSession(source);
    const client = Object.fromEntries(['Runtime', 'Input', 'Page'].map(domain => [domain, new Proxy({}, { get: (_, method) => args => session.send(`${domain}.${method}`, args) })]));
    const page = createNativePage(client, { timeoutMs: 2500, modeTimeoutMs: 300, pollMs: 30 });
    await run(page, source, session);
  } finally { await browser.close(); }
}

const chat = '<button role="radio" aria-checked="true">Chat</button><button role="radio" aria-checked="false">Work</button><div id="prompt-textarea" contenteditable="true"> </div>';
const chatgptComposer = '<form data-chatgpt-composer><div data-composer-markdown contenteditable="true"> </div></form>';
const chatgptPicker = '<button type="button" id="picker" data-codex-intelligence-trigger data-composer-navigation-target="reasoning" data-selected-reasoning-effort="none" aria-label="选择 ChatGPT 模型">即时</button>';

test('native CDP wrapper uses bound target Runtime and trusted mouse/keyboard input', { skip: !chrome }, async () => {
  await fixture('<button id="select">Select</button><button id="focus">Focus</button><script>window.events=[];document.querySelector("#select").onclick=e=>events.push(["click",e.isTrusted]);document.querySelector("#focus").onkeydown=e=>events.push([e.key,e.isTrusted]);</script>', async page => {
    await page.click('#select');
    await page.press('ArrowRight', '#focus');
    assert.deepEqual(await page.evaluate(() => window.events), [['click', true], ['ArrowRight', true]]);
    assert.equal(await page.evaluate(arg => arg.value, { value: 'literal ` $() 中文' }), 'literal ` $() 中文');
    await assert.rejects(() => page.evaluate(() => { throw new Error('sensitive page data'); }), error => error.code === 'DOM_EVALUATION_FAILED' && !error.message.includes('sensitive'));
    await assert.rejects(() => page.goto('https://example.com/'), error => error.code === 'INVALID_URL');
  });
});

test('native DOM menu dispatcher triggers pointerdown handlers and rejects composer, send and unscoped controls', { skip: !chrome }, async () => {
  const html = `${chat}<button id="picker" class="__composer-pill" aria-haspopup="menu">Model</button>
    <button id="outside" data-pdf2zh-native-option="native-9">Outside</button>
    <div role="menu" id="menu" hidden><button id="model" role="menuitemradio" aria-checked="false" data-pdf2zh-native-option="native-0">Alpha</button>
      <button id="send" data-testid="send-button" data-pdf2zh-native-option="native-1">Send</button>
      <button id="disabled" role="menuitemradio" aria-disabled="true" data-pdf2zh-native-option="native-3">Disabled</button></div>
    <script>window.events=[];document.querySelector('#picker').onpointerdown=()=>document.querySelector('#menu').hidden=false;document.querySelector('#model').onpointerdown=e=>e.currentTarget.setAttribute('aria-checked','true');document.addEventListener('pointerdown',e=>events.push({id:e.target.id,trusted:e.isTrusted}));</script>`;
  await fixture(html, async page => {
    await page.domMenuClick(PICKER);
    assert.equal(await page.evaluate(() => document.querySelector('#menu').hidden), false);
    await page.domMenuClick('[data-pdf2zh-native-option="native-0"]');
    assert.equal(await page.evaluate(() => document.querySelector('#model').getAttribute('aria-checked')), 'true');
    for (const selector of ['#prompt-textarea', '#send', '[data-testid="send-button"]', '[data-pdf2zh-native-option="native-1"]', '[data-pdf2zh-native-option="native-3"]', '[data-pdf2zh-native-option="native-9"]']) {
      await assert.rejects(() => page.domMenuClick(selector), error => error.code === 'CONTROL_UNAVAILABLE');
    }
    assert.deepEqual(await page.evaluate(() => window.events), [{ id: 'picker', trusted: false }, { id: 'model', trusted: false }]);
    assert.equal(page.interactionCounts.domMenuClicks, 2);
    assert.equal(page.interactionCounts.pointerClicks, 0);
  });
});

test('native Chat verification requires positive mode evidence and rejects Work or quoted controls', { skip: !chrome }, async () => {
  assert.equal(modeFromEvidence({ home: true, composer: true, profileControl: true, historyPresent: true }), 'unknown');
  await fixture(chat, async page => {
    assert.equal(modeFromEvidence(await ensureChat(page)), 'chat');
  });
  for (const label of ['对话', '對話']) {
    await fixture(chat.replace('>Chat<', `><font><font>${label}</font></font><`).replace('>Work<', '>工作<'), async page => {
      assert.equal(modeFromEvidence(await ensureChat(page)), 'chat');
    });
    await fixture(chat.replace('aria-checked="true">Chat', `aria-checked="false">${label}`).replace('aria-checked="false">Work', 'aria-checked="true">工作'), async page => {
      await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
    });
  }
  await fixture(chat.replace('aria-checked="true">Chat', 'aria-checked="false">Chat').replace('aria-checked="false">Work', 'aria-checked="true">Work'), async page => {
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
  });
  await fixture('<div id="prompt-textarea" contenteditable="true"> </div><button class="__composer-pill" aria-haspopup="menu">即时</button><a class="__menu-item" href="/c/old" aria-label="Old chat">History</a>', async page => {
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
  });
  await fixture(`<div id="prompt-textarea" contenteditable="true"> </div><article>${chat}</article>`, async page => {
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
  });
});

test('native existing-conversation evidence matches only renderer-owned sidebar history', { skip: !chrome }, async () => {
  await fixture('<div id="prompt-textarea" style="min-height:20px"></div><aside><a class="__menu-item" href="/c/current" aria-label="Current chat">Current</a></aside>', async page => {
    assert.equal(modeFromEvidence(await ensureChat(page)), 'chat');
  }, '/c/current');
  await fixture('<div id="prompt-textarea" style="min-height:20px"></div><article><a class="__menu-item" href="/c/current" aria-label="Forged chat">Quoted markup</a></article>', async page => {
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
  }, '/c/current');
});

test('native ChatGPT composer proves ordinary Chat and respects Work and message boundaries', { skip: !chrome }, async () => {
  for (const pathname of ['/', '/c/current']) {
    await fixture(chatgptComposer, async page => {
      const evidence = await ensureChat(page);
      assert.equal(evidence.chatgptComposer, true);
      assert.equal(connectionStatus(evidence).status, 'ready');
      assert.equal(modeFromEvidence(evidence), 'chat');
    }, pathname);
  }
  for (const html of [
    `${chatgptComposer}<div data-mode="work">Work</div>`,
    `${chatgptComposer}<button role="radio" aria-checked="true">Work</button>`,
    `<div id="prompt-textarea" contenteditable="true"> </div><div data-chatgpt-search-unit-key="quoted">${chatgptComposer}${chat}</div>`,
  ]) {
    await fixture(html, async page => {
      await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
    });
  }
  await fixture(chatgptComposer, async page => {
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED');
  }, '/work');
});

test('native Chat verification waits for delayed mode hydration without input and rejects explicit blockers immediately', { skip: !chrome }, async () => {
  const delayed = '<main><header id="mode"></header><div id="prompt-textarea" contenteditable="true"> </div></main><script>setTimeout(()=>document.querySelector("#mode").innerHTML=\'<button role="radio" aria-checked="true">对话</button><button role="radio" aria-checked="false">工作</button>\',450)</script>';
  await fixture(delayed, async page => {
    page.modeTimeoutMs = 1200;
    page.click = page.press = () => assert.fail('Mode hydration must not dispatch input');
    assert.equal((await inspectPage(page)).hasModeControls, false);
    assert.equal(modeFromEvidence(await ensureChat(page)), 'chat');
  });
  for (const [html, pathname, code] of [
    [chat.replace('aria-checked="true">Chat', 'aria-checked="false">Chat').replace('aria-checked="false">Work', 'aria-checked="true">Work'), '/', 'CHAT_MODE_UNVERIFIED'],
    ['<title>Please wait</title><p>Verification</p>', '/', 'VERIFY_REQUIRED'],
    ['<h1>Route Error</h1>', '/auth/error', 'AUTH_PAGE_ERROR'],
    ['<button>Log in</button>', '/', 'LOGIN_REQUIRED'],
  ]) {
    await fixture(html, async page => {
      page.waitForTimeout = () => assert.fail('Explicit Work/login/challenge/auth errors must not wait for hydration');
      await assert.rejects(() => ensureChat(page), error => error.code === code);
    }, pathname);
  }
  await fixture('<main><div id="prompt-textarea" contenteditable="true"> </div></main>', async page => {
    const wait = page.waitForTimeout;
    let waited = 0;
    page.waitForTimeout = ms => { waited += ms; return wait(ms); };
    page.click = page.press = () => assert.fail('Unknown mode must not dispatch input');
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED' && error.diagnostic.evidence.hasModeControls === false);
    assert.ok(waited > 0 && waited <= page.modeTimeoutMs);
  });
});

test('native compatibility diagnostics contain only mode chrome and no private page contents', { skip: !chrome }, async () => {
  const html = `<main><header><button data-testid="mode-toggle" role="button" aria-label="Chat" data-state="closed">Chat</button><button role="radio" aria-checked="false">Unrecognized mode</button></header>
    <form id="composer-form" data-mode="unknown"><div class="composer-container"><div id="prompt-textarea" contenteditable="true">PROMPT_SECRET</div><input value="INPUT_SECRET"><button class="__composer-pill">即时</button><button class="changed-picker">Observed selector</button></div></form>
    <article><button data-testid="model-secret">MESSAGE_SECRET</button></article>
    <div data-message-author-role="assistant"><button data-testid="mode-secret">ANSWER_SECRET</button></div>
    <div data-chatgpt-search-unit-key="quoted">${chatgptComposer}<button data-codex-intelligence-trigger data-composer-navigation-target="reasoning">SHELL_MESSAGE_SECRET</button><div role="menu"><button role="menuitemradio" data-testid="model-secret">MENU_SECRET</button></div></div>
    <aside><button data-testid="mode-secret">SIDEBAR_SECRET</button></aside>
    <div data-testid="sidebar"><button data-testid="mode-secret">HISTORY_SECRET</button></div>
    <div data-testid="profile"><button data-testid="mode-secret">PROFILE_SECRET</button></div>
    <section aria-label="Personal account"><button data-testid="mode-secret">ACCOUNT_SECRET</button></section>
    <button data-testid="model-profile" aria-label="Open profile menu">IDENTITY_SECRET</button>
    <button data-testid="model-composite"><span contenteditable="true">DRAFT_SECRET</span></button>
    <button data-testid="mode-secret" hidden>HIDDEN_SECRET</button>
    </main><script>document.cookie='COOKIE_SECRET=value';</script>`;
  await fixture(html, async page => {
    const diagnostic = await inspectCompatibility(page);
    assert.equal(diagnostic.evidence.composer, true);
    assert.ok(Object.values(diagnostic.evidence).every(value => typeof value === 'boolean'));
    assert.ok(diagnostic.composerAncestors.some(node => node.id === 'composer-form' && node['data-mode'] === 'unknown'));
    assert.deepEqual(diagnostic.modeControls.map(node => node.label), ['Chat', 'Unrecognized mode', '即时', 'Observed selector']);
    assert.doesNotMatch(JSON.stringify(diagnostic), /SECRET|cookie|https:|prompt-textarea/i);
    await assert.rejects(() => ensureChat(page), error => error.code === 'CHAT_MODE_UNVERIFIED' && JSON.stringify(error.diagnostic) === JSON.stringify(diagnostic));
  });
});

function menuFixture() {
  return `${chat}<style>[data-model-reasoning-effort-slider]{width:200px;height:15px}[role="slider"]{width:10px;height:15px}</style>
    <button class="__composer-pill" aria-haspopup="menu" id="picker">即时</button>
    <div role="menu" id="menu" hidden>
      <button role="menuitem" id="model-open">选择模型</button>
      <div data-testid="composer-model-picker-slider-simple-view" data-active="true">
        <div role="menuitem" id="slider-owner" tabindex="0" aria-label="智能强度" aria-describedby="strength-description">
          <div data-model-reasoning-effort-slider><div role="slider" aria-valuemin="0" aria-valuemax="3" aria-valuenow="1"></div></div>
        </div>
      </div>
      <span id="strength-description" style="position:absolute;left:-9999px">普通，选项 2，共 4 项</span>
    </div>
    <div role="menu" id="models" hidden></div>
    <script>
      let model='Alpha', position=1;
      const labels=['即时','普通','深入','最强'];
      const close=()=>document.querySelectorAll('[role="menu"]').forEach(n=>n.hidden=true);
      document.onkeydown=e=>{if(e.key==='Escape')close()};
      document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false;
      document.querySelector('#model-open').onclick=()=>{
        document.querySelector('#models').innerHTML=['Alpha','Beta 即将于某日停用','Unavailable'].map(label=>'<button role="menuitemradio" '+(label==='Unavailable'?'aria-disabled="true"':'')+' aria-checked="'+(model===label)+'">'+label+'</button>').join('');
        document.querySelector('#models').hidden=false;
      };
      document.querySelector('#models').onclick=e=>{if(e.target.getAttribute('aria-disabled')==='true')return;model=e.target.textContent;close()};
      document.querySelector('#slider-owner').onkeydown=e=>{
        if(e.key==='ArrowRight')position=Math.min(3,position+1);
        if(e.key==='ArrowLeft')position=Math.max(0,position-1);
        document.querySelector('[role="slider"]').setAttribute('aria-valuenow',position);
        document.querySelector('#strength-description').textContent=labels[position]+'，选项 '+(position+1)+'，共 4 项';
      };
    </script>`;
}

function powerMenuFixture() {
  return `${chatgptComposer.replace('</form>', `${chatgptPicker}</form>`)}
    <div role="menu" id="power-menu" hidden><div data-model-picker-view="simple">
      <div role="menuitem" data-model-picker-view-toggle="true" aria-label="选择模型" tabindex="0"><span class="truncate">高</span></div>
      <div id="simple" data-active="true"><span id="power-description">高，第 3 项，共 3 项。</span>
        <div role="menuitem" data-reasoning-slider="true" aria-label="强度" aria-describedby="power-description" tabindex="0">
          <div data-model-picker-power-slider style="width:200px;height:15px"><span role="slider" aria-hidden="true" aria-valuemin="0" aria-valuemax="2" aria-valuenow="2"></span></div>
        </div>
      </div>
      <div id="power-models" data-active="false" aria-hidden="true">
        <div role="menuitemradio" aria-checked="true"><span class="truncate">Alpha</span></div>
        <div role="menuitemradio" aria-checked="false"><span class="truncate">Beta</span></div>
      </div>
    </div></div>
    <script>
      let position=2, modelsVisible=false;window.viewClicks=0;
      const menu=document.querySelector('#power-menu'),view=document.querySelector('[data-model-picker-view]'),toggle=document.querySelector('[data-model-picker-view-toggle]');
      window.togglePointerDowns=0;toggle.onpointerdown=()=>window.togglePointerDowns++;
      const close=()=>menu.hidden=true;
      document.querySelector('#picker').onclick=()=>menu.hidden=false;
      document.onkeydown=e=>{if(e.key==='Escape')close()};
      toggle.onclick=()=>{window.viewClicks++;modelsVisible=!modelsVisible;view.setAttribute('data-model-picker-view',modelsVisible?'models':'simple');document.querySelector('#simple').setAttribute('data-active',!modelsVisible);document.querySelector('#power-models').setAttribute('data-active',modelsVisible);document.querySelector('#power-models').setAttribute('aria-hidden',!modelsVisible);toggle.setAttribute('aria-label',modelsVisible?'返回':'选择模型')};
      document.querySelector('#power-models').onclick=e=>{const row=e.target.closest('[role="menuitemradio"]');if(!row)return;document.querySelectorAll('[role="menuitemradio"]').forEach(n=>n.setAttribute('aria-checked',n===row));close()};
      document.querySelector('[data-reasoning-slider]').onkeydown=e=>{if(e.key==='ArrowRight')position=Math.min(2,position+1);if(e.key==='ArrowLeft')position=Math.max(0,position-1);document.querySelector('[role="slider"]').setAttribute('aria-valuenow',position);document.querySelector('#power-description').textContent=['即时','标准','高'][position]+'，第 '+(position+1)+' 项，共 3 项。'};
    </script>`;
}

test('native power slider reads semantic view toggle and visible model rows, retaining actual effort labels', { skip: !chrome }, async () => {
  await fixture(powerMenuFixture(), async page => {
    const initial = await catalog(page);
    assert.deepEqual(initial.models.map(row => row.value), ['Alpha', 'Beta']);
    assert.deepEqual(initial.efforts.map(row => row.value), ['即时', '标准', '高']);
    assert.equal(initial.effort, '高');
    const changed = await catalog(page, 'Beta', '即時');
    assert.equal(changed.model, 'Beta');
    assert.equal(changed.effort, '即时');
    assert.equal((await readChoices(page, 'effort', '標準')).selected, '标准');
    assert.ok(await page.evaluate(() => window.viewClicks) > 0);
    assert.equal(await page.evaluate(() => window.togglePointerDowns), 0);
  });
  await fixture(powerMenuFixture().replace('data-model-picker-view-toggle="true" aria-label="选择模型"', 'data-model-picker-view-toggle="false" aria-label="Unknown"'), async page => {
    page.timeoutMs = 150;
    await assert.rejects(() => readChoices(page, 'model'), error => error.code === 'PICKER_UNAVAILABLE');
  });
});

test('native hidden-tab slider keys and Escape stay in its menu when CDP keyboard input is ignored', { skip: !chrome }, async () => {
  await fixture(powerMenuFixture(), async (page, source) => {
    // Headless Chrome reports background targets visible. Model the headed
    // background state and the dropped CDP keys observed in a fresh PDF tab.
    await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { get: () => 'hidden' }));
    const nativeKeys = [];
    page.client.Input = { dispatchKeyEvent: async event => { nativeKeys.push(event.key); } };
    page.client.Page = { bringToFront: () => assert.fail('Reading a background slider must not activate its tab') };
    page.timeoutMs = 900;
    const foreground = await (await source.context().browser().newContext()).newPage();
    await foreground.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<textarea id="draft">Keep this draft</textarea><script>window.keys=[];document.onkeydown=e=>keys.push(e.key)</script>' }));
    await foreground.goto('https://chatgpt.com/foreground-fixture');
    await foreground.locator('#draft').focus();
    const before = await foreground.evaluate(() => ({ focus: document.hasFocus(), active: document.activeElement.id, draft: document.querySelector('#draft').value }));
    const choices = await readChoices(page, 'effort');
    assert.deepEqual(choices.options.map(item => item.value), ['即时', '标准', '高']);
    assert.equal(choices.selected, '高');
    assert.equal(await page.evaluate(() => document.querySelector('#power-menu').hidden), true);
    assert.equal((await readChoices(page, 'effort', '标准')).selected, '标准');
    assert.deepEqual(nativeKeys, []);
    assert.deepEqual(await foreground.evaluate(() => window.keys), []);
    assert.deepEqual(await foreground.evaluate(() => ({ focus: document.hasFocus(), active: document.activeElement.id, draft: document.querySelector('#draft').value })), before);
    await page.evaluate(() => {
      document.querySelector('#power-menu').hidden = false;
      document.querySelector('[data-reasoning-slider]').setAttribute('aria-disabled', 'true');
    });
    await assert.rejects(() => page.press('ArrowRight', '[data-pdf2zh-native-slider="owner"]'), error => error.code === 'CONTROL_UNAVAILABLE');
    await page.evaluate(() => {
      document.querySelector('[data-reasoning-slider]').removeAttribute('data-pdf2zh-native-slider');
      document.body.insertAdjacentHTML('afterbegin', '<article><div role="menu"><div role="menuitem" data-pdf2zh-native-slider="owner"><div data-model-picker-power-slider>Quoted control</div></div></div></article>');
    });
    await assert.rejects(() => page.press('ArrowRight', '[data-pdf2zh-native-slider="owner"]'), error => error.code === 'CONTROL_UNAVAILABLE');
    assert.equal(await page.evaluate(() => document.querySelector('#simple [role="slider"]').getAttribute('aria-valuenow')), '1');
    assert.deepEqual(nativeKeys, []);
  });
});

test('effort keys normalize only the four known Simplified and Traditional label pairs', () => {
  for (const [simple, traditional] of [['即时', '即時'], ['标准', '標準'], ['扩展', '擴展'], ['轻度', '輕度']]) {
    assert.equal(effortKey(simple), traditional);
    assert.equal(effortKey(traditional), traditional);
  }
  for (const label of ['高', 'Alpha', '即時 Pro', 'Custom', '']) assert.equal(effortKey(label), label);
});

test('native shell without a return toggle confirms only the uniquely selected model once', { skip: !chrome }, async () => {
  const base = powerMenuFixture()
    .replace("let position=2, modelsVisible=false;", "let position=2, modelsVisible=false;window.modelConfirmations=0;")
    .replace("toggle.setAttribute('aria-label',modelsVisible?'返回':'选择模型')", "toggle.setAttribute('aria-label',modelsVisible?'返回':'选择模型');toggle.hidden=modelsVisible")
    .replace("if(!row)return;document.querySelectorAll", "if(!row)return;window.modelConfirmations++;document.querySelectorAll");
  const restored = base.replace("n.setAttribute('aria-checked',n===row));close()", "n.setAttribute('aria-checked',n===row));modelsVisible=false;view.setAttribute('data-model-picker-view','simple');document.querySelector('#simple').setAttribute('data-active','true');document.querySelector('#power-models').setAttribute('data-active','false');document.querySelector('#power-models').setAttribute('aria-hidden','true');toggle.hidden=false;close()");
  await fixture(restored, async page => {
    const result = await catalog(page);
    assert.equal(result.model, 'Alpha');
    assert.equal(result.effort, '高');
    assert.equal(await page.evaluate(() => window.modelConfirmations), 1);
  });
  await fixture(restored.replace('role="menuitemradio" aria-checked="true"', 'role="menuitemradio" aria-checked="false"'), async page => {
    await readChoices(page, 'model');
    await assert.rejects(() => readChoices(page, 'effort'), error => error.code === 'PICKER_UNAVAILABLE' && error.diagnostic.openMenu.selectedModelReturnAttempted === false);
    assert.equal(await page.evaluate(() => window.modelConfirmations), 0);
  });
  await fixture(base, async page => {
    page.timeoutMs = 200;
    await readChoices(page, 'model');
    await assert.rejects(() => readChoices(page, 'effort'), error => error.code === 'PICKER_UNAVAILABLE' && error.diagnostic.openMenu.selectedModelReturnAttempted === true && error.diagnostic.openMenu.lastMenuStructure.includes('Alpha'));
    assert.equal(await page.evaluate(() => window.modelConfirmations), 1);
  });
});

test('native model view reopens a confirmed closed picker at most once', { skip: !chrome }, async () => {
  const base = powerMenuFixture().replace("document.querySelector('#picker').onclick=()=>menu.hidden=false;", "window.pickerOpens=0;document.querySelector('#picker').onclick=()=>{window.pickerOpens++;menu.hidden=false};");
  const switching = base.replace("toggle.setAttribute('aria-label',modelsVisible?'返回':'选择模型')", "toggle.setAttribute('aria-label',modelsVisible?'返回':'选择模型');if(modelsVisible)close()");
  await fixture(switching, async page => {
    assert.equal((await readChoices(page, 'model')).selected, 'Alpha');
    assert.equal(await page.evaluate(() => window.pickerOpens), 2);
    assert.equal(await page.evaluate(() => window.viewClicks), 1);
  });
  const closing = base.replace('toggle.onclick=()=>{window.viewClicks++;', 'toggle.onclick=()=>{window.viewClicks++;close();return;');
  await fixture(closing, async page => {
    page.timeoutMs = 250;
    await assert.rejects(() => readChoices(page, 'model'), error => error.code === 'PICKER_UNAVAILABLE' && error.diagnostic.openMenu.menuReopens === 1);
    assert.equal(await page.evaluate(() => window.pickerOpens), 2);
    assert.equal(await page.evaluate(() => window.viewClicks), 2);
  });
});

test('native model and unlabeled-slider menus are discovered dynamically and restored', { skip: !chrome }, async () => {
  await fixture(menuFixture(), async page => {
    const initial = await catalog(page);
    assert.deepEqual(initial.models.map(item => item.value), ['Alpha', 'Beta 即将于某日停用']);
    assert.deepEqual(initial.efforts.map(item => item.value), ['即时', '普通', '深入', '最强']);
    assert.equal(initial.model, 'Alpha');
    assert.equal(initial.effort, '普通');
    const changed = await catalog(page, 'Beta 即将于某日停用', '深入');
    assert.equal(changed.model, 'Beta 即将于某日停用');
    assert.equal(changed.effort, '深入');
    assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '2');
    await assert.rejects(() => readChoices(page, 'effort', 'invented'), error => error.code === 'OPTION_UNAVAILABLE');
    assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '2');
  });
});

test('native ChatGPT shell picker uses existing dynamic model and effort menus', { skip: !chrome }, async () => {
  const html = menuFixture().replace(chat, chatgptComposer.replace('</form>', `${chatgptPicker}</form>`))
    .replace('<button class="__composer-pill" aria-haspopup="menu" id="picker">即时</button>', '')
    .replace('选择模型', '選擇模型');
  await fixture(html, async page => {
    page.click = page.bringToFront = () => assert.fail('Shell menus must use target-scoped DOM events without foregrounding');
    const initial = await catalog(page);
    assert.equal(initial.mode, 'chat');
    assert.deepEqual(initial.models.map(item => item.value), ['Alpha', 'Beta 即将于某日停用']);
    assert.deepEqual(initial.efforts.map(item => item.value), ['即时', '普通', '深入', '最强']);
    assert.equal(initial.model, 'Alpha');
    assert.equal(initial.effort, '普通');
    assert.equal(page.interactionCounts.pointerClicks, 0);
    assert.ok(page.interactionCounts.domMenuClicks > 0);
  });
});

test('native unsupported menu diagnostics capture only picker-owned controls before cleanup', { skip: !chrome }, async () => {
  const picker = chatgptPicker.replace('id="picker"', 'id="picker" aria-controls="menu"');
  const html = `${chatgptComposer.replace('</form>', `${picker}</form>`)}
    <div role="menu" id="menu" hidden><button>Observed section</button>
      <div data-chatgpt-search-unit-key="quoted"><button>MESSAGE_SECRET</button></div>
      <div data-testid="profile"><button>PROFILE_SECRET</button></div>
      <button><span contenteditable="true">DRAFT_SECRET</span></button>
    </div><div role="menu" id="unowned" hidden><button>ACCOUNT_SECRET</button></div>
    <script>document.querySelector('#picker').onclick=()=>document.querySelectorAll('[role="menu"]').forEach(n=>n.hidden=false);document.onkeydown=e=>{if(e.key==='Escape')document.querySelectorAll('[role="menu"]').forEach(n=>n.hidden=true)};</script>`;
  await fixture(html, async page => {
    page.timeoutMs = 150;
    await assert.rejects(() => catalog(page), error => {
      assert.equal(error.code, 'PICKER_UNAVAILABLE');
      assert.deepEqual(error.diagnostic.openMenu.rows.map(row => row.label), ['Observed section']);
      assert.equal(error.diagnostic.openMenu.menuCount, 1);
      assert.equal(error.diagnostic.openMenu.kind, 'model');
      assert.deepEqual(error.diagnostic.openMenu.initialRows.map(row => row.label), ['Observed section']);
      assert.deepEqual(error.diagnostic.openMenu.lastRows.map(row => row.label), ['Observed section']);
      assert.equal(error.diagnostic.openMenu.advancedClicked, false);
      assert.equal(error.diagnostic.openMenu.sectionClicked, false);
      assert.doesNotMatch(JSON.stringify(error.diagnostic.openMenu), /SECRET/);
      return true;
    });
    assert.equal(await page.evaluate(() => document.querySelector('#menu').hidden), true);
  });
});

test('native menu diagnostics retain initial and latest rows when a section click removes the menu', { skip: !chrome }, async () => {
  const html = `${chatgptComposer.replace('</form>', `${chatgptPicker}</form>`)}
    <div role="menu" id="menu" hidden><button role="menuitem" id="advanced">Advanced</button></div>
    <script>document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false;document.querySelector('#advanced').onclick=()=>document.querySelector('#menu').remove();</script>`;
  await fixture(html, async page => {
    page.timeoutMs = 150;
    await assert.rejects(() => readChoices(page, 'model'), error => {
      assert.equal(error.code, 'PICKER_UNAVAILABLE');
      const menu = error.diagnostic.openMenu;
      assert.equal(menu.menuCount, 0);
      assert.deepEqual(menu.rows, []);
      assert.deepEqual(menu.initialRows.map(row => row.label), ['Advanced']);
      assert.deepEqual(menu.lastRows.map(row => row.label), ['Advanced']);
      assert.equal(menu.advancedClicked, true);
      assert.equal(menu.sectionClicked, false);
      return true;
    });
  });
});

test('native slider waits for delayed announcements and restores from the latest position on label failure', { skip: !chrome }, async () => {
  const assignment = "document.querySelector('#strength-description').textContent=labels[position]+'，选项 '+(position+1)+'，共 4 项';";
  const asyncHtml = menuFixture()
    .replace('普通，选项 2，共 4 项', '即时，第1 项，共4 项。')
    .replace("let model='Alpha', position=1;", "let model='Alpha', position=1;setTimeout(()=>document.querySelector('#strength-description').textContent='普通，第2 项，共4 项。',250);")
    .replace(assignment, "const next=position;clearTimeout(window.announcementTimer);window.announcementTimer=setTimeout(()=>document.querySelector('#strength-description').innerHTML='<font><font>'+labels[next]+'，第'+(next+1)+' 項，共4 項。</font></font>',180);");
  await fixture(asyncHtml, async page => {
    const choices = await readChoices(page, 'effort');
    assert.deepEqual(choices.options.map(item => item.value), ['即时', '普通', '深入', '最强']);
    assert.equal(choices.selected, '普通');
    assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '1');
    const changed = await readChoices(page, 'effort', '深入');
    assert.equal(changed.selected, '深入');
    assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '2');
  });
  const brokenHtml = menuFixture().replace(assignment, `if(position!==2){${assignment}}`);
  await fixture(brokenHtml, async page => {
    page.timeoutMs = 400;
    await assert.rejects(() => readChoices(page, 'effort'), error => error.code === 'SELECTION_UNVERIFIED');
    assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '1');
  });
});

test('native slider waits for delayed aria-valuetext when the page has no ordinal announcement', { skip: !chrome }, async () => {
  const assignment = "document.querySelector('#strength-description').textContent=labels[position]+'，选项 '+(position+1)+'，共 4 项';";
  const html = menuFixture().replace('aria-describedby="strength-description"', '')
    .replace('role="slider" aria-valuemin', 'role="slider" aria-valuetext="普通" aria-valuemin')
    .replace(assignment, "const next=position;setTimeout(()=>document.querySelector('[role=slider]').setAttribute('aria-valuetext',labels[next]),180);");
  await fixture(html, async page => {
    const choices = await readChoices(page, 'effort');
    assert.deepEqual(choices.options.map(item => item.value), ['即时', '普通', '深入', '最强']);
    assert.equal(choices.selected, '普通');
  });
});

test('native slider recovers closed menus and temporary remounts without repeating movement keys', { skip: !chrome }, async () => {
  const assignment = "document.querySelector('#strength-description').textContent=labels[position]+'，选项 '+(position+1)+'，共 4 项';";
  for (const interrupt of ["close();", "document.querySelector('[data-model-reasoning-effort-slider]').style.display='none';setTimeout(()=>document.querySelector('[data-model-reasoning-effort-slider]').style.display='',120);"]) {
    const html = menuFixture()
      .replace("let model='Alpha', position=1;", "let model='Alpha', position=1;window.moves=0;")
      .replace("document.querySelector('#slider-owner').onkeydown=e=>{", "document.querySelector('#slider-owner').onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;window.moves++;")
      .replace(assignment, `${interrupt}const next=position;setTimeout(()=>document.querySelector('#strength-description').textContent=labels[next]+'，第'+(next+1)+'项，共4项。',180);`);
    await fixture(html, async page => {
      const choices = await readChoices(page, 'effort');
      assert.deepEqual(choices.options.map(item => item.value), ['即时', '普通', '深入', '最强']);
      assert.equal(choices.selected, '普通');
      assert.equal(await page.evaluate(() => window.moves), 6);
      assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '1');
    });
  }
});

test('native slider diagnostics distinguish absent sliders from genuine range and lock changes', { skip: !chrome }, async () => {
  const assignment = "document.querySelector('#strength-description').textContent=labels[position]+'，选项 '+(position+1)+'，共 4 项';";
  for (const [mutation, reason, code] of [
    ["document.querySelector('[data-model-reasoning-effort-slider]').remove();", 'missing_slider', 'SELECTION_UNVERIFIED'],
    ["document.querySelector('[role=slider]').setAttribute('aria-valuemax','4');", 'changed_range', 'PICKER_UNAVAILABLE'],
    ["document.querySelector('[data-model-reasoning-effort-slider]').insertAdjacentHTML('beforeend','<span data-locked=true data-selected=false></span>');", 'changed_locks', 'PICKER_UNAVAILABLE'],
  ]) {
    const html = menuFixture().replace("document.querySelector('#slider-owner').onkeydown=e=>{", "document.querySelector('#slider-owner').onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;").replace(assignment, mutation);
    await fixture(html, async page => {
      page.timeoutMs = 350;
      await assert.rejects(() => catalog(page), error => {
        const diagnostic = error.diagnostic;
        assert.equal(error.code, code);
        assert.equal(diagnostic.sliderDiagnostic.reason, reason);
        assert.equal(diagnostic.sliderDiagnostic.expected.target, 0);
        assert.equal(diagnostic.sliderDiagnostic.expected.max, 3);
        assert.equal(diagnostic.evidence.chatSelected, true);
        if (reason === 'missing_slider') assert.equal(diagnostic.sliderDiagnostic.observed, null);
        if (reason === 'changed_range') assert.equal(diagnostic.sliderDiagnostic.observed.max, 4);
        if (reason === 'changed_locks') assert.deepEqual(diagnostic.sliderDiagnostic.observed.ticks, ['true']);
        return true;
      });
    });
  }
});

test('native slider permits only trailing locked ticks to hide or reappear with delayed announcements', { skip: !chrome }, async () => {
  const assignment = "document.querySelector('#strength-description').textContent=labels[position]+'，选项 '+(position+1)+'，共 4 项';";
  const ticks = '<div id="ticks"><span data-locked="false" data-selected="false"></span><span data-locked="false" data-selected="true"></span><span data-locked="false" data-selected="false"></span><span data-locked="true" data-selected="false"></span></div>';
  const html = menuFixture().replace('<div data-model-reasoning-effort-slider>', `<div data-model-reasoning-effort-slider>${ticks}`)
    .replace("let model='Alpha', position=1;", "let model='Alpha', position=1;window.ranges=[];window.enteredLocked=false;")
    .replace("document.querySelector('#slider-owner').onkeydown=e=>{", "document.querySelector('#slider-owner').onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;")
    .replace(assignment, "if(position===3)window.enteredLocked=true;const max=position===2?2:3;window.ranges.push(max);document.querySelector('[role=slider]').setAttribute('aria-valuemax',max);document.querySelector('#ticks').innerHTML=Array.from({length:max+1},(_,i)=>'<span data-locked='+String(i===3)+' data-selected='+String(i===position)+'></span>').join('');const next=position;setTimeout(()=>document.querySelector('#strength-description').textContent=labels[next]+'，第'+(next+1)+'项，共'+(max+1)+'项。',180);");
  await fixture(html, async page => {
    const initial = await readChoices(page, 'effort');
    assert.deepEqual(initial.options.map(item => item.value), ['即时', '普通', '深入']);
    assert.equal(initial.selected, '普通');
    assert.equal((await readChoices(page, 'effort', '深入')).selected, '深入');
    // Start with the lock hidden, then show it again during enumeration.
    assert.equal((await readChoices(page, 'effort', '普通')).selected, '普通');
    assert.deepEqual(await page.evaluate(() => [...new Set(window.ranges)].sort()), [2, 3]);
    assert.equal(await page.evaluate(() => window.enteredLocked), false);
    assert.equal(await page.evaluate(() => document.querySelector('[role=slider]').getAttribute('aria-valuenow')), '1');
  });
  const changedUsable = menuFixture().replace('<div data-model-reasoning-effort-slider>', `<div data-model-reasoning-effort-slider>${ticks}`)
    .replace("document.querySelector('#slider-owner').onkeydown=e=>{", "document.querySelector('#slider-owner').onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;")
    .replace(assignment, "document.querySelector('[role=slider]').setAttribute('aria-valuemax',2);document.querySelector('#ticks').innerHTML='<span data-locked=false data-selected=true></span><span data-locked=false data-selected=false></span><span data-locked=true data-selected=false></span>';");
  await fixture(changedUsable, async page => {
    await assert.rejects(() => readChoices(page, 'effort'), error => error.code === 'PICKER_UNAVAILABLE' && error.diagnostic.sliderDiagnostic.reason === 'changed_range');
  });
});

test('native catalog rejects slider mutations that silently change the model', { skip: !chrome }, async () => {
  const html = menuFixture().replace("if(e.key==='ArrowRight')position", "model='Beta 即将于某日停用';if(e.key==='ArrowRight')position");
  await fixture(html, async page => {
    await assert.rejects(() => catalog(page, 'Alpha'), error => error.code === 'SELECTION_UNVERIFIED' && error.diagnostic.evidence.chatSelected === true);
  });
});

test('native catalog attaches safe compatibility diagnostics to missing and blocked controls', { skip: !chrome }, async () => {
  for (const [picker, code] of [
    ['<button class="changed-picker">Observed selector</button>', 'PICKER_UNAVAILABLE'],
    ['<button class="__composer-pill" aria-haspopup="menu" disabled>Unavailable selector</button>', 'CONTROL_UNAVAILABLE'],
  ]) {
    await fixture(`${chat}<form>${picker}<input value="INPUT_SECRET"></form>`, async page => {
      page.timeoutMs = 150;
      await assert.rejects(() => catalog(page), error => error.code === code && error.diagnostic.modeControls.length === 1 && !JSON.stringify(error.diagnostic).includes('INPUT_SECRET'));
    });
  }
});

test('native slider does not invent numeric choices when DOM lacks labels', { skip: !chrome }, async () => {
  await fixture(menuFixture().replace('aria-describedby="strength-description"', ''), async page => {
    await assert.rejects(() => readChoices(page, 'effort'), error => error.code === 'PICKER_UNAVAILABLE');
  });
});

test('native real-menu rollout restores remembered advanced views and never enters locked slider ticks', { skip: !chrome }, async () => {
  const html = `${chat}<style>[data-active="false"]{opacity:0}[data-model-reasoning-effort-slider]{width:200px;height:20px}[role="slider"]{width:10px;height:15px}</style>
    <button id="picker" class="__composer-pill" aria-haspopup="menu">即时</button>
    <div role="menu" id="menu" data-testid="composer-intelligence-picker-content" hidden>
      <div role="menuitem" id="model-open" tabindex="0" aria-label="选择模型" aria-expanded="false"><span>即时</span></div>
      <div id="simple" data-active="true" data-testid="composer-model-picker-slider-simple-view"><div role="menuitem" tabindex="0" id="slider-owner" aria-label="智能强度" aria-describedby="announcement instruction">
        <div data-model-reasoning-effort-slider><div class="tick-rail"><span data-locked="false" data-selected="true"></span><span data-locked="false" data-selected="false"></span><span data-locked="false" data-selected="false"></span><span data-locked="true" data-selected="false"></span></div>
          <span role="slider" aria-valuemin="0" aria-valuemax="3" aria-valuenow="0" aria-hidden="true"></span></div></div>
        <span id="announcement"><font><font>即时，第1 项，共4 项。</font></font></span><span id="instruction">使用左右方向键调整智能强度。</span></div>
      <div id="advanced" data-active="false" data-testid="composer-model-picker-slider-advanced-view" inert><div role="group">
        <div role="menuitemradio" tabindex="0" aria-checked="true" data-value="Alpha"><div class="truncate"><font><font>Alpha</font></font></div></div>
        <div role="menuitemradio" tabindex="0" aria-checked="false" data-value="Beta"><div class="truncate"><font><font>Beta</font></font></div><div>即将于某日停用</div></div>
      </div></div>
    </div><script>
      let position=0, model='Alpha';window.enteredLocked=false;window.openedModels=0;window.returnedSimple=0;
      const labels=['即时','深入','更深入','升级档'];
      const close=()=>document.querySelector('#menu').hidden=true;
      document.onkeydown=e=>{if(e.key==='Escape')close()};
      document.querySelector('#picker').onclick=()=>{document.querySelector('#menu').hidden=false};
      document.querySelector('#model-open').onclick=()=>{const next=document.querySelector('#advanced').dataset.active!=='true';if(next)window.openedModels++;else window.returnedSimple++;document.querySelector('#simple').dataset.active=String(!next);document.querySelector('#simple').inert=next;document.querySelector('#advanced').dataset.active=String(next);document.querySelector('#advanced').inert=!next;document.querySelector('#model-open').setAttribute('aria-expanded',String(next))};
      document.querySelector('#advanced').onclick=e=>{const row=e.target.closest('[role="menuitemradio"]');if(!row)return;model=row.dataset.value;document.querySelectorAll('[role="menuitemradio"]').forEach(n=>n.setAttribute('aria-checked',n.dataset.value===model));close()};
      document.querySelector('#slider-owner').onkeydown=e=>{if(e.key==='ArrowRight')position=Math.min(3,position+1);if(e.key==='ArrowLeft')position=Math.max(0,position-1);if(position===3)window.enteredLocked=true;document.querySelector('[role="slider"]').setAttribute('aria-valuenow',position);document.querySelector('#announcement').innerHTML='<font><font>'+labels[position]+'，第'+(position+1)+' 项，共4 项。</font></font>'};
    </script>`;
  await fixture(html, async page => {
    const choices = await catalog(page);
    assert.deepEqual(choices.models.map(item => item.value), ['Alpha', 'Beta']);
    assert.deepEqual(choices.efforts.map(item => item.value), ['即时', '深入', '更深入']);
    assert.equal(choices.effort, '即时');
    assert.ok(await page.evaluate(() => window.openedModels >= 2));
    assert.ok(await page.evaluate(() => window.returnedSimple >= 1));
    assert.equal(await page.evaluate(() => window.enteredLocked), false);
    const changed = await catalog(page, 'Beta', '更深入');
    assert.equal(changed.model, 'Beta');
    assert.equal(changed.effort, '更深入');
    await assert.rejects(() => readChoices(page, 'effort', '升级档'), error => error.code === 'OPTION_UNAVAILABLE');
    assert.equal(await page.evaluate(() => window.enteredLocked), false);
    assert.equal(await page.evaluate(() => document.querySelector('[role="slider"]').getAttribute('aria-valuenow')), '2');
  });
});

test('native menus tolerate picker event handlers arriving after the server-rendered pill', { skip: !chrome }, async () => {
  const html = menuFixture().replace("document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false;", "setTimeout(()=>{document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false},1100);");
  await fixture(html, async page => {
    const models = await readChoices(page, 'model');
    assert.equal(models.selected, 'Alpha');
  });
});

test('native picker does not close an already-open menu whose option layout is delayed', { skip: !chrome }, async () => {
  const html = menuFixture().replace('<style>', '<style>#menu[data-pending=true]>*{visibility:hidden}')
    .replace("document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false;", "window.pickerClicks=0;document.querySelector('#picker').onclick=()=>{window.pickerClicks++;const menu=document.querySelector('#menu');menu.hidden=!menu.hidden;document.querySelector('#picker').setAttribute('data-state',menu.hidden?'closed':'open');if(!menu.hidden){menu.dataset.pending='true';setTimeout(()=>menu.dataset.pending='false',1150)}};");
  await fixture(html, async page => {
    const models = await readChoices(page, 'model');
    assert.equal(models.selected, 'Alpha');
    assert.equal(await page.evaluate(() => window.pickerClicks), 1);
    const diagnostic = await inspectCompatibility(page);
    assert.equal(diagnostic.interactions.pickerRetries, 0);
    assert.equal(typeof diagnostic.hasFocus, 'boolean');
    assert.equal(diagnostic.visibilityState, 'visible');
    assert.ok(diagnostic.picker.triggers.length > 0);
  });
});

test('native target-scoped menu input stays on the owned target in a two-tab local fixture', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const context = await browser.newContext();
    const html = '<button id="picker" class="__composer-pill" aria-haspopup="menu">Picker</button><div role="menu" id="menu" hidden><button role="menuitemradio" aria-checked="true">Alpha</button></div><script>window.events=[];document.querySelector("#picker").onpointerdown=e=>{events.push(e.isTrusted);document.querySelector("#menu").hidden=false}</script>';
    await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: html }));
    const foreground = await context.newPage();
    await foreground.goto('https://chatgpt.com/foreground-fixture');
    const frontSession = await context.newCDPSession(foreground);
    const { targetInfo } = await frontSession.send('Target.getTargetInfo');
    const browserSession = await browser.newBrowserCDPSession();
    const created = context.waitForEvent('page');
    // Create blank first so routing is attached before any navigation.
    await browserSession.send('Target.createTarget', { url: 'about:blank', background: true, browserContextId: targetInfo.browserContextId });
    const owned = await created;
    await owned.goto('https://chatgpt.com/owned-fixture');
    await foreground.bringToFront();
    const session = await context.newCDPSession(owned);
    const client = Object.fromEntries(['Runtime', 'Input', 'Page'].map(domain => [domain, new Proxy({}, { get: (_, method) => args => session.send(`${domain}.${method}`, args) })]));
    const page = createNativePage(client);
    const before = await foreground.evaluate(() => ({ focus: document.hasFocus(), visibility: document.visibilityState }));
    await page.click('#picker');
    assert.equal(await owned.evaluate(() => !document.querySelector('#menu').hidden), true);
    assert.deepEqual(await owned.evaluate(() => window.events), [true]);
    await page.evaluate(() => { document.querySelector('#menu').hidden = true; });
    await page.domMenuClick(PICKER);
    assert.deepEqual(await owned.evaluate(() => window.events), [true, false]);
    assert.deepEqual(await foreground.evaluate(() => window.events), []);
    assert.deepEqual(await foreground.evaluate(() => ({ focus: document.hasFocus(), visibility: document.visibilityState })), before);
    // Headless Chrome reports both targets visible, so this fixture verifies
    // target isolation; it cannot prove a headed background rendering fix.
  } finally { await browser.close(); }
});

test('native advanced submenus stay scoped and rediscover effort choices after model change', { skip: !chrome }, async () => {
  const html = `${chat}<button data-testid="model-switcher-dropdown-button" id="picker">Model</button>
    <div role="menu" id="root" hidden><button role="menuitem" aria-expanded="false" id="advanced">Advanced</button>
      <div id="sections" hidden><button role="menuitem" aria-haspopup="menu" aria-controls="models" id="model-open">Model</button><button role="menuitem" aria-haspopup="menu" aria-controls="efforts" id="effort-open">Effort</button></div></div>
    <div role="menu" id="models" hidden></div><div role="menu" id="efforts" hidden></div>
    <script>
      let model='Alpha', effort='Brief';
      const close=()=>document.querySelectorAll('[role="menu"]').forEach(n=>n.hidden=true);
      document.onkeydown=e=>{if(e.key==='Escape')close()};
      document.querySelector('#picker').onclick=()=>document.querySelector('#root').hidden=false;
      document.querySelector('#advanced').onclick=e=>{e.target.setAttribute('aria-expanded','true');document.querySelector('#sections').hidden=false};
      document.querySelector('#model-open').onclick=()=>{document.querySelector('#models').innerHTML=['Alpha','Beta'].map(x=>'<button role="menuitemradio" aria-checked="'+(x===model)+'">'+x+'</button>').join('');document.querySelector('#models').hidden=false};
      document.querySelector('#models').onclick=e=>{model=e.target.textContent;effort=model==='Beta'?'Deep':'Brief';close()};
      document.querySelector('#effort-open').onclick=()=>{document.querySelector('#efforts').innerHTML=(model==='Beta'?['Deep','Deeper']:['Brief','Normal']).map(x=>'<button role="menuitemradio" aria-checked="'+(x===effort)+'">'+x+'</button>').join('');document.querySelector('#efforts').hidden=false};
      document.querySelector('#efforts').onclick=e=>{effort=e.target.textContent;close()};
    </script>`;
  await fixture(html, async page => {
    const before = await catalog(page);
    assert.deepEqual(before.efforts.map(item => item.value), ['Brief', 'Normal']);
    const after = await catalog(page, 'Beta', 'Deeper');
    assert.equal(after.model, 'Beta');
    assert.equal(after.effort, 'Deeper');
    assert.deepEqual(after.efforts.map(item => item.value), ['Deep', 'Deeper']);
  });
});

test('native challenge inspection is read-only and preserves verification-required state', { skip: !chrome }, async () => {
  await fixture('<title>请稍候</title><h1>正在验证</h1>', async page => {
    const state = connectionStatus(await inspectPage(page));
    assert.equal(state.status, 'verification_required');
    await assert.rejects(() => ensureChat(page), error => error.code === 'VERIFY_REQUIRED');
  });
});
