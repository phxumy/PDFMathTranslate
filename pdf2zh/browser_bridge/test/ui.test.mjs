import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright-core';
import { catalog, readChoices } from '../ui.mjs';

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
