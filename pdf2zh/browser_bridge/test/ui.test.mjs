import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright-core';
import { catalog, connectionStatus, ensureChat, inspectPage, prepareLoginPage, readChoices } from '../ui.mjs';

const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync);
test('account menus retain Advanced state and refresh model-specific effort choices', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<button role="radio" aria-checked="true">Chat</button><button role="radio" aria-checked="false">Work</button>
      <div id="prompt-textarea" contenteditable="true"> </div><button data-testid="model-switcher-dropdown-button" onclick="document.querySelector('#root').hidden=false">Models</button>
      <div id="root" role="menu" hidden><button role="menuitem" aria-expanded="false" id="advanced">Advanced</button>
        <div id="sections" hidden><button role="menuitem" aria-haspopup="menu" aria-controls="models" id="model-open">Model</button><button role="menuitem" aria-haspopup="menu" aria-controls="efforts" id="effort-open">Effort</button></div></div>
      <div role="menu" id="models" hidden></div><div role="menu" id="efforts" hidden></div>
      <script>
      let model='Alpha', effort='Brief';
      const close=()=>document.querySelectorAll('[role=menu]').forEach(n=>n.hidden=true);
      document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
      document.querySelector('#advanced').onclick=e=>{let expanded=e.target.getAttribute('aria-expanded')!=='true';e.target.setAttribute('aria-expanded',expanded);document.querySelector('#sections').hidden=!expanded};
      const renderModels=()=>{document.querySelector('#models').innerHTML=['Alpha','Beta','Unavailable'].map(x=>'<button role="menuitemradio" aria-checked="'+(x===model)+'" '+(x==='Unavailable'?'aria-disabled="true"':'')+'>'+x+'</button>').join('');document.querySelector('#models').hidden=false;};
      document.querySelector('#model-open').onclick=renderModels;
      document.querySelector('#models').onclick=e=>{if(e.target.getAttribute('aria-disabled')==='true')return;model=e.target.textContent;effort=model==='Beta'?'Deep':'Brief';close()};
      document.querySelector('#effort-open').onclick=()=>{document.querySelector('#efforts').innerHTML=(model==='Beta'?['Deep','Deeper']:['Brief','Normal']).map(x=>'<button role="menuitemradio" aria-checked="'+(x===effort)+'">'+x+'</button>').join('');document.querySelector('#efforts').hidden=false;};
      document.querySelector('#efforts').onclick=e=>{effort=e.target.textContent;close()};
      </script>`);
    const initial = await catalog(page);
    assert.deepEqual(initial.models.map(m => m.value), ['Alpha', 'Beta']);
    assert.deepEqual(initial.efforts.map(e => e.value), ['Brief', 'Normal']);
    const changed = await catalog(page, 'Beta', 'Deeper');
    assert.equal(changed.model, 'Beta');
    assert.equal(changed.effort, 'Deeper');
    assert.deepEqual(changed.efforts.map(e => e.value), ['Deep', 'Deeper']);
  } finally { await browser.close(); }
});
test('slider choices come from actual ARIA labels and catalog restores prior effort', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<button data-testid="model-switcher-dropdown-button" onclick="document.querySelector('#menu').hidden=false">Model</button>
      <div id="menu" role="menu" hidden><div data-testid="composer-model-picker-slider-simple-view" data-active="true"><div role="menuitem" id="owner" tabindex="0" aria-describedby="description"><div data-model-reasoning-effort-slider><div role="slider" aria-valuemin="0" aria-valuemax="2" aria-valuenow="1"></div></div></div></div><span id="description">中等，选项 2</span></div>
      <script>let value=1;document.querySelector('#owner').onkeydown=e=>{if(e.key==='ArrowRight')value=Math.min(2,value+1);if(e.key==='ArrowLeft')value=Math.max(0,value-1);document.querySelector('[role=slider]').setAttribute('aria-valuenow',value);document.querySelector('#description').textContent=['轻量','中等','深入'][value]+'，选项 '+(value+1);};document.onkeydown=e=>{if(e.key==='Escape')document.querySelector('#menu').hidden=true};</script>`);
    const values = await readChoices(page, 'effort');
    assert.deepEqual(values.options.map(e => e.value), ['轻量', '中等', '深入']);
    assert.equal(values.selected, '中等');
    const changed = await readChoices(page, 'effort', '深入');
    assert.equal(changed.selected, '深入');
  } finally { await browser.close(); }
});

test('auth route error distinguishes passive 403 evidence from ordinary login', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    // Every request is served from a fixture; this test never contacts OpenAI.
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<h1>糟糕，出错了!</h1><pre>Route Error (400 Invalid content type: text/html; charset=UTF-8)</pre>' }));
    await page.goto('https://auth.openai.com/log-in/password');
    await page.evaluate(() => Object.defineProperty(performance, 'getEntriesByType', { configurable: true, value: () => [{ name: 'https://auth.openai.com/api/accounts/password/verify', responseStatus: 403 }] }));
    const evidence = await inspectPage(page);
    const state = connectionStatus(evidence);
    assert.equal(state.status, 'auth_error');
    assert.equal(state.error_code, 'AUTH_PAGE_ERROR');
    assert.match(state.message, /403/);
    assert.doesNotMatch(state.message, /密码错误/);
    await assert.rejects(() => ensureChat(page), error => error.code === 'AUTH_PAGE_ERROR' && error.status === 'auth_error');
    await page.evaluate(() => Object.defineProperty(performance, 'getEntriesByType', { value: () => [{ name: 'https://auth.openai.com/awe/api/v2/rum', responseStatus: 403 }] }));
    assert.doesNotMatch(connectionStatus(await inspectPage(page)).message, /403/, 'telemetry rejections are not authentication evidence');
    await page.setContent('<h1>Enter your password</h1><input type="password"><button>Continue</button>');
    assert.equal(connectionStatus(await inspectPage(page)).status, 'needs_login');
  } finally { await browser.close(); }
});

test('explicit Login recovers only recognized error pages and retains a normal form', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    const requests = [];
    await page.route('**/*', route => {
      requests.push(route.request().url());
      return route.fulfill({ contentType: 'text/html', body: route.request().url().includes('auth.openai.com')
        ? '<h1>Oops, an error occurred</h1><pre>Route Error (400 Invalid content type: text/html)</pre>'
        : '<h1>Welcome</h1><button>Log in</button>' });
    });
    await page.goto('https://auth.openai.com/log-in/password');
    const errorState = connectionStatus(await inspectPage(page));
    assert.equal(errorState.status, 'auth_error');
    assert.equal(requests.length, 1, 'status inspection does not navigate');
    const recovered = await prepareLoginPage(page);
    assert.equal(page.url(), 'https://chatgpt.com/');
    assert.equal(recovered.status, 'needs_login');
    assert.deepEqual(requests, ['https://auth.openai.com/log-in/password', 'https://chatgpt.com/']);
    await page.goto('https://auth.openai.com/log-in/password');
    await page.setContent('<h1>Enter your password</h1><input id="form-state" value="fixture-in-progress"><button>Continue</button>');
    const before = requests.length;
    await prepareLoginPage(page);
    assert.equal(requests.length, before);
    assert.equal(page.url(), 'https://auth.openai.com/log-in/password');
    assert.equal(await page.locator('#form-state').inputValue(), 'fixture-in-progress');
  } finally { await browser.close(); }
});

test('verification is reported distinctly and quoted chat errors are ignored', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<title>Just a moment...</title><h1>Verify you are human</h1>');
    assert.equal(connectionStatus(await inspectPage(page)).status, 'verification_required');
    await page.setContent('<title>ChatGPT</title><div id="prompt-textarea" contenteditable="true"> </div><article><p>Route Error (400 Invalid content type)</p></article>');
    assert.equal(connectionStatus(await inspectPage(page)).status, 'ready');
  } finally { await browser.close(); }
});

test('localized challenge takes precedence over auth errors and Login does not reload it', { skip: !chrome }, async () => {
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage();
    let navigations = 0;
    await page.route('**/*', route => {
      navigations++;
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<title>请稍候…</title><h1>糟糕，出错了!</h1><p>Route Error (400 Invalid content type)</p>' });
    });
    await page.goto('https://auth.openai.com/log-in/password');
    assert.equal(connectionStatus(await inspectPage(page)).status, 'verification_required');
    assert.match(connectionStatus(await inspectPage(page)).message, /程序控制环境/);
    assert.equal((await prepareLoginPage(page)).status, 'verification_required');
    assert.equal(navigations, 1);
    await page.setContent('<title>请稍等</title><h1>正在验证</h1>');
    assert.equal(connectionStatus(await inspectPage(page)).status, 'verification_required');
  } finally { await browser.close(); }
});
