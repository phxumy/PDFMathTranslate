import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright-core';
import { createNativePage, inspectPage, connectionStatus, ensureChat, catalog, readChoices, modeFromEvidence } from '../native-ui.mjs';

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
    const page = createNativePage(client, { timeoutMs: 2500, pollMs: 30 });
    await run(page, source, session);
  } finally { await browser.close(); }
}

const chat = '<button role="radio" aria-checked="true">Chat</button><button role="radio" aria-checked="false">Work</button><div id="prompt-textarea" contenteditable="true"> </div>';

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

test('native Chat verification requires positive mode evidence and rejects Work or quoted controls', { skip: !chrome }, async () => {
  assert.equal(modeFromEvidence({ home: true, composer: true, profileControl: true, historyPresent: true }), 'unknown');
  await fixture(chat, async page => {
    assert.equal(modeFromEvidence(await ensureChat(page)), 'chat');
  });
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

test('native catalog rejects slider mutations that silently change the model', { skip: !chrome }, async () => {
  const html = menuFixture().replace("if(e.key==='ArrowRight')position", "model='Beta 即将于某日停用';if(e.key==='ArrowRight')position");
  await fixture(html, async page => {
    await assert.rejects(() => catalog(page, 'Alpha'), error => error.code === 'SELECTION_UNVERIFIED');
  });
});

test('native slider does not invent numeric choices when DOM lacks labels', { skip: !chrome }, async () => {
  await fixture(menuFixture().replace('aria-describedby="strength-description"', ''), async page => {
    await assert.rejects(() => readChoices(page, 'effort'), error => error.code === 'PICKER_UNAVAILABLE');
  });
});

test('native menus tolerate picker event handlers arriving after the server-rendered pill', { skip: !chrome }, async () => {
  const html = menuFixture().replace("document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false;", "setTimeout(()=>{document.querySelector('#picker').onclick=()=>document.querySelector('#menu').hidden=false},1100);");
  await fixture(html, async page => {
    const models = await readChoices(page, 'model');
    assert.equal(models.selected, 'Alpha');
  });
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
