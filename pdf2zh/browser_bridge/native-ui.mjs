// Target-scoped CDP DOM adapter. The caller owns connection/authorization.
// This module neither starts browsers nor changes browser-identification flags.
import {
  WORDS, PICKER, coded, filterOptions, normalize, effortKey,
  inspectPage as inspectLegacyPage, connectionStatus as legacyConnectionStatus,
} from './ui.mjs';
import { buildClickDispatcher } from '@steipete/oracle/dist/src/browser/actions/domEvents.js';

export { coded };

const MENU = '[role="menu"],[role="listbox"],[data-testid="composer-intelligence-picker-content"]';
const KEY_CODES = { Escape: 27, ArrowLeft: 37, ArrowRight: 39, ArrowUp: 38, ArrowDown: 40, Enter: 13, Home: 36, End: 35 };
const MENU_CLICK_DISPATCHER = buildClickDispatcher('dispatchMenuClick');
const isMenuSelector = selector => selector === PICKER || /^\[data-pdf2zh-native-option="native-\d+"\]$/.test(selector) || selector === '[data-pdf2zh-native-slider="owner"]';

export function createNativePage(client, { timeoutMs = 9000, modeTimeoutMs = 30000, pollMs = 150 } = {}) {
  if (!client?.Runtime?.evaluate || !client?.Input?.dispatchMouseEvent || !client?.Input?.dispatchKeyEvent) {
    throw coded('CDP_UNAVAILABLE', '原生浏览器连接没有提供当前标签页的操作接口。');
  }
  const page = {
    client, timeoutMs, modeTimeoutMs, pollMs,
    interactionCounts: { pointerClicks: 0, domMenuClicks: 0, keyPresses: 0, pickerRetries: 0 },
    async evaluate(fn, arg) {
      const expression = `(${fn.toString()})(${arg === undefined ? '' : JSON.stringify(arg)})`;
      const result = await client.Runtime.evaluate({ expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw coded('DOM_EVALUATION_FAILED', '浏览器页面检查失败，请刷新连接状态后重试。');
      return result.result?.value;
    },
    waitForTimeout(ms) { return new Promise(resolve => setTimeout(resolve, ms)); },
    async click(selector) {
      const point = await page.evaluate(selector => {
        const node = [...document.querySelectorAll(selector)].find(n => {
          const rect = n.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && getComputedStyle(n).visibility !== 'hidden' && !n.closest('[inert],[data-active="false"]');
        });
        if (!node || node.disabled || node.getAttribute('aria-disabled') === 'true') return null;
        node.scrollIntoView({ block: 'center', inline: 'nearest' });
        const rect = node.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const hit = document.elementFromPoint(x, y);
        return hit && (hit === node || node.contains(hit)) ? { x, y } : null;
      }, selector);
      if (!point) throw coded('CONTROL_UNAVAILABLE', '网页选项不可用或被其他窗口覆盖，请检查浏览器页面。');
      page.interactionCounts.pointerClicks++;
      await client.Input.dispatchMouseEvent({ type: 'mouseMoved', ...point });
      await client.Input.dispatchMouseEvent({ type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
      await client.Input.dispatchMouseEvent({ type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
    },
    async domMenuClick(selector) {
      if (!isMenuSelector(selector)) throw coded('CONTROL_UNAVAILABLE', 'DOM 交互仅限模型菜单控件。');
      const activate = ({ selector, picker, menus }) => {
        const node = [...document.querySelectorAll(selector)].find(candidate => {
          const rect = candidate.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && getComputedStyle(candidate).visibility !== 'hidden'
            && !candidate.closest('article,[data-message-author-role],[data-chatgpt-search-unit-key],[inert],[data-active="false"],[hidden],[aria-hidden="true"],#prompt-textarea,input,textarea,[contenteditable="true"],[data-testid="send-button"],[data-testid*="stop-button"]');
        });
        if (!node || node.disabled || node.closest('[aria-disabled="true"]') || node.querySelector('#prompt-textarea,input,textarea,[contenteditable="true"],[data-testid="send-button"],[data-testid*="stop-button"]')) return false;
        const isPicker = selector === picker && node.matches(picker);
        const isRow = node.hasAttribute('data-pdf2zh-native-option') && node.closest(menus);
        const isSlider = node.getAttribute('data-pdf2zh-native-slider') === 'owner' && node.closest(menus) && node.querySelector('[data-model-reasoning-effort-slider],[data-model-picker-power-slider]');
        if (!isPicker && !isRow && !isSlider) return false;
        // The shell's view toggle owns its click handler; an extra pointerdown
        // can also reach the menu's close handler before the view has changed.
        if (isRow && node.getAttribute('data-model-picker-view-toggle') === 'true') { node.click(); return true; }
        return dispatchMenuClick(node);
      };
      // Oracle's ordinary DOM menu events work on a hidden target without
      // activating it. This dispatcher is never used for prompts or Send.
      const expression = `(() => { ${MENU_CLICK_DISPATCHER}; return (${activate.toString()})(${JSON.stringify({ selector, picker: PICKER, menus: MENU })}); })()`;
      const result = await client.Runtime.evaluate({ expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails || result.result?.value !== true) throw coded('CONTROL_UNAVAILABLE', '网页模型菜单控件不可用。');
      page.interactionCounts.domMenuClicks++;
    },
    async menuClick(selector) {
      if (!isMenuSelector(selector)) throw coded('CONTROL_UNAVAILABLE', '交互仅限模型菜单控件。');
      const useDOM = await page.evaluate(() => document.visibilityState === 'hidden' ||
        [...document.querySelectorAll('form[data-chatgpt-composer] button[data-codex-intelligence-trigger][data-composer-navigation-target="reasoning"]')]
          .some(node => !node.closest('article,[data-message-author-role],[data-chatgpt-search-unit-key],[hidden],[inert]') && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0));
      if (useDOM) return page.domMenuClick(selector);
      return page.click(selector);
    },
    async press(key, selector) {
      if (!(key in KEY_CODES)) throw coded('INVALID_KEY', '不支持的浏览器按键。');
      const menuKey = selector === '[data-pdf2zh-native-slider="owner"]' && ['ArrowLeft', 'ArrowRight'].includes(key)
        || !selector && key === 'Escape';
      if (menuKey && await page.evaluate(() => document.visibilityState === 'hidden')) {
        // Headed Chrome can drop CDP keyboard input on a background PDF tab.
        // Dispatch only slider movement or menu dismissal in the bound target;
        // the slider reader still verifies its actual position and label.
        const dispatched = await page.evaluate(({ key, keyCode, selector, menus }) => {
          const excluded = 'article,[data-message-author-role],[data-chatgpt-search-unit-key],[inert],[data-active="false"],[hidden],[aria-hidden="true"],input,textarea,[contenteditable="true"],[data-testid="send-button"],[data-testid*="stop-button"]';
          const visible = node => {
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && getComputedStyle(node).visibility !== 'hidden' && !node.closest(excluded);
          };
          const roots = [...document.querySelectorAll(menus)].filter(visible);
          const node = selector
            ? [...document.querySelectorAll(selector)].find(candidate => visible(candidate) && roots.includes(candidate.closest(menus)))
            : roots.at(-1);
          if (!node) return !selector;
          if (node.disabled || node.closest('[aria-disabled="true"]')) return false;
          if (selector && (!node.matches('[role="menuitem"]') || !node.querySelector('[data-model-reasoning-effort-slider],[data-model-picker-power-slider]') || node.querySelector('input,textarea,[contenteditable="true"],[data-testid="send-button"],[data-testid*="stop-button"]'))) return false;
          if (selector) node.focus();
          const detail = { key, code: key, keyCode, which: keyCode, bubbles: true, cancelable: true, composed: true };
          node.dispatchEvent(new KeyboardEvent('keydown', detail));
          node.dispatchEvent(new KeyboardEvent('keyup', detail));
          return true;
        }, { key, keyCode: KEY_CODES[key], selector, menus: MENU });
        if (!dispatched) throw coded('CONTROL_UNAVAILABLE', '后台标签页的推理强度菜单控件不可用。');
        page.interactionCounts.keyPresses++;
        return;
      }
      if (selector) {
        const focused = await page.evaluate(selector => {
          const node = document.querySelector(selector);
          if (!node || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
          node.focus();
          return document.activeElement === node;
        }, selector);
        if (!focused) throw coded('CONTROL_UNAVAILABLE', '无法聚焦网页选项。');
      }
      const detail = { key, code: key, windowsVirtualKeyCode: KEY_CODES[key], nativeVirtualKeyCode: KEY_CODES[key] };
      page.interactionCounts.keyPresses++;
      await client.Input.dispatchKeyEvent({ type: 'keyDown', ...detail });
      await client.Input.dispatchKeyEvent({ type: 'keyUp', ...detail });
    },
    async goto(url) {
      const parsed = new URL(url);
      if (parsed.origin !== 'https://chatgpt.com') throw coded('INVALID_URL', '只能打开 ChatGPT 官网。');
      await client.Page.navigate({ url });
      await poll(page, () => page.evaluate(() => document.readyState !== 'loading'), 'PAGE_NOT_READY', 'ChatGPT 页面尚未准备好。');
    },
    async bringToFront() { await client.Page.bringToFront(); },
  };
  page.keyboard = { press: key => page.press(key) };
  return page;
}

async function poll(page, probe, code, message) {
  const deadline = Date.now() + page.timeoutMs;
  do {
    const value = await probe();
    if (value) return value;
    await page.waitForTimeout(page.pollMs);
  } while (Date.now() < deadline);
  throw coded(code, message);
}

export async function inspectPage(page) {
  const base = await inspectLegacyPage(page);
  const extra = await page.evaluate(() => {
    const visible = n => !!n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden';
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const outsideMessages = n => !n.closest('article,[data-message-author-role],[data-chatgpt-search-unit-key]');
    const modeControls = [...document.querySelectorAll('button[role="radio"],[role="tab"],button[data-testid*="mode"]')].filter(n => visible(n) && outsideMessages(n));
    const selected = n => n && (n.getAttribute('aria-checked') === 'true' || n.getAttribute('aria-selected') === 'true' || ['on','active','checked'].includes(n.getAttribute('data-state')));
    const chatControl = modeControls.find(n => /^(Chat|聊天|聊天模式|对话|對話|チャット)$/i.test(norm(n.textContent)));
    const workControl = modeControls.find(n => /^(Work|工作|工作模式|ワーク)$/i.test(norm(n.textContent)));
    const profileControl = [...document.querySelectorAll('[data-testid*="profile"],button[aria-label]')]
      .some(n => visible(n) && outsideMessages(n) && (/profile/i.test(n.getAttribute('data-testid') || '') || /^(Open profile menu|打开个人资料菜单|プロフィールメニューを開く)$/i.test(n.getAttribute('aria-label') || '')));
    const historyLinks = [...document.querySelectorAll('a.__menu-item[href*="/c/"]')].filter(outsideMessages);
    const conversationId = location.pathname.match(/\/c\/([a-zA-Z0-9-]+)/)?.[1];
    const active = conversationId ? historyLinks.filter(n => {
      try { const url = new URL(n.getAttribute('href'), location.origin); return url.origin === location.origin && url.pathname.match(/\/c\/([a-zA-Z0-9-]+)/)?.[1] === conversationId; }
      catch { return false; }
    }) : [];
    const workBadge = n => [...n.querySelectorAll('span')].some(span => !span.childElementCount && span.classList.contains('shrink-0') && span.parentElement?.matches('span.flex.items-center') && /^(Work|工作|ワーク)$/i.test(norm(span.textContent)));
    const historyWork = active.some(n => workBadge(n) || /[,，]\s*(Work|工作|ワーク)\s*$/i.test(n.getAttribute('aria-label') || ''));
    const historyChat = active.length > 0 && !historyWork && active.every(n => norm(n.getAttribute('aria-label')));
    return { profileControl, historyPresent: historyLinks.length > 0, historyWork, historyChat, conversation: !!conversationId,
      chatSelected: !!selected(chatControl), workSelected: !!selected(workControl), hasModeControls: !!(chatControl || workControl) };
  });
  return { ...base, ...extra };
}

export function modeFromEvidence(evidence) {
  if (evidence.workSelected || evidence.workPath || evidence.historyWork) return 'work';
  if (evidence.chatSelected || evidence.historyChat) return 'chat';
  if (evidence.chatgptComposer && !evidence.workMarker) return 'chat';
  if (evidence.hasModeControls || evidence.conversation) return 'unknown';
  if (evidence.home && evidence.legacyPicker && evidence.composer && !evidence.workMarker) return 'legacy-chat';
  return 'unknown';
}

export function connectionStatus(evidence) {
  const state = legacyConnectionStatus(evidence);
  return { ...state, message: state.message.replaceAll('专用浏览器', '已授权的浏览器') };
}

export async function prepareLoginPage(page) {
  let evidence = await inspectPage(page);
  if (evidence.authError && !evidence.challenge) {
    await page.goto('https://chatgpt.com/');
    evidence = await inspectPage(page);
  }
  await page.bringToFront();
  return connectionStatus(evidence);
}

// Compatibility diagnostics deliberately describe chrome, never conversation
// text, input values, account details, URLs, cookies or arbitrary DOM attributes.
export async function inspectCompatibility(page, knownEvidence) {
  const source = knownEvidence || await inspectPage(page);
  const evidence = Object.fromEntries([
    'home', 'composer', 'chatgptComposer', 'login', 'challenge', 'authError', 'legacyPicker',
    'workPath', 'workMarker', 'chatSelected', 'workSelected', 'hasModeControls',
    'profileControl', 'historyPresent', 'historyWork', 'historyChat', 'conversation',
  ].map(key => [key, !!source[key]]));
  const structure = await page.evaluate(() => {
    const visible = n => !!n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden';
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    const excluded = 'article,aside,nav,[role="navigation"],[data-message-author-role],[data-message-id],[data-chatgpt-search-unit-key],[data-testid*="conversation-turn"],input,textarea,select,[contenteditable="true"],[data-testid*="sidebar" i],[id*="sidebar" i],[class*="sidebar" i],[data-testid*="profile" i],[id*="profile" i],[class*="profile" i],[data-testid*="account" i],[id*="account" i],[class*="account" i]';
    const privateLabel = /profile|account|personal|个人资料|账户|账号|プロフィール|アカウント/i;
    const safeNode = n => {
      if (n.closest(excluded)) return false;
      for (let parent = n; parent && parent !== document.body; parent = parent.parentElement) {
        if (privateLabel.test(parent.getAttribute('aria-label') || '')) return false;
      }
      return true;
    };
    const short = (value, limit = 160) => norm(value).slice(0, limit);
    const attrs = (node, names) => Object.fromEntries(names.map(name => [name, short(node.getAttribute(name))]));
    const composer = [...document.querySelectorAll('#prompt-textarea,textarea[name="prompt-textarea"],form[data-chatgpt-composer] [data-composer-markdown][contenteditable="true"]')].find(visible);
    const composerAncestors = [];
    for (let node = composer?.parentElement; node && node !== document.body && composerAncestors.length < 12; node = node.parentElement) {
      if (!safeNode(node)) continue;
      composerAncestors.push({ tag: node.tagName.toLowerCase(), ...attrs(node, ['id', 'class', 'data-testid', 'data-mode', 'data-type']) });
    }
    const modeControls = [...document.querySelectorAll('main button,header button,form button,main [role="button"],header [role="button"],form [role="button"],main [role="radio"],header [role="radio"],form [role="radio"],main [role="tab"],header [role="tab"],form [role="tab"]')]
      .filter(node => visible(node) && safeNode(node) && !privateLabel.test(node.textContent || '') && !node.querySelector(excluded))
      .filter(node => {
        if (node.closest('form') && (node.tagName === 'BUTTON' || node.getAttribute('role') === 'button')) return true;
        if (['radio', 'tab'].includes(node.getAttribute('role')) && node.closest('main,header')) return true;
        const metadata = ['data-testid', 'class', 'data-mode', 'data-type'].map(name => node.getAttribute(name) || '').join(' ');
        const label = norm(node.getAttribute('aria-label') || node.textContent);
        return /mode|model|composer.*pill|intelligence|reasoning|thinking/i.test(metadata) || /^(?:Chat(?:GPT)?|Work|Chat mode|Work mode|Mode|Model|Models|聊天|聊天模式|工作|工作模式|模式|模型|选择模型|智能强度|推理强度|即时|チャット|ワーク|モード|モデル)(?:\s*[⌄▾▼])?$/i.test(label);
      })
      .slice(0, 32)
      .map(node => {
        const rect = node.getBoundingClientRect();
        return { tag: node.tagName.toLowerCase(), ...attrs(node, ['role', 'data-testid', 'class', 'aria-label', 'aria-selected', 'aria-checked', 'data-state', 'aria-haspopup', 'aria-disabled']), label: short(node.textContent, 80), rect: { width: Math.round(rect.width), height: Math.round(rect.height) } };
      });
    return { composerAncestors, modeControls, visibilityState: document.visibilityState, hasFocus: document.hasFocus() };
  });
  const picker = await pickerSnapshot(page);
  const interactions = Object.fromEntries(['pointerClicks', 'domMenuClicks', 'keyPresses', 'pickerRetries'].map(key => [key, Number(page.interactionCounts?.[key] || 0)]));
  return { evidence, ...structure, picker, interactions };
}

export async function ensureChat(page) {
  const deadline = Date.now() + (page.modeTimeoutMs ?? page.timeoutMs);
  let evidence;
  for (;;) {
    evidence = await inspectPage(page);
    const state = connectionStatus(evidence);
    if (state.error_code) throw Object.assign(coded(state.error_code, state.message), { status: state.status });
    const mode = modeFromEvidence(evidence);
    // The composer can hydrate before the mode switch. Waiting only observes
    // DOM state: never click a mode or send anything while the mode is unknown.
    if (mode === 'work') break;
    if (!evidence.composer || evidence.login || evidence.authPage) throw coded('LOGIN_REQUIRED', '请先在浏览器完成 ChatGPT 登录，再刷新模型。');
    if (['chat', 'legacy-chat'].includes(mode)) return evidence;
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await page.waitForTimeout(Math.min(page.pollMs, remaining));
  }
  const error = coded('CHAT_MODE_UNVERIFIED', '无法确认普通 Chat 模式，已停止发送。请在已授权浏览器打开普通 Chat 后刷新。');
  try { error.diagnostic = await inspectCompatibility(page, evidence); } catch { /* Preserve the closed gate if diagnostic inspection fails. */ }
  throw error;
}

async function closeMenus(page) {
  await page.press('Escape');
  await page.press('Escape');
}

async function menuSnapshot(page, captureStructure = false) {
  return page.evaluate(({ menuSelector, captureStructure }) => {
    const visible = n => n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden' && !n.closest('[inert],[data-active="false"]');
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    document.querySelectorAll('[data-pdf2zh-native-option]').forEach(n => n.removeAttribute('data-pdf2zh-native-option'));
    const privateArea = 'aside,nav,input,textarea,[contenteditable="true"],[data-testid*="sidebar" i],[data-testid*="profile" i],[data-testid*="account" i],[id*="profile" i],[id*="account" i],article,[data-message-author-role],[data-chatgpt-search-unit-key]';
    const privateLabel = /profile|account|personal|个人资料|账户|账号|個人資料|帳戶|帳號|プロフィール|アカウント/i;
    const diagnosticSafe = node => {
      if (node.closest(privateArea) || node.querySelector(privateArea) || privateLabel.test(node.textContent || '')) return false;
      for (let parent = node; parent && parent !== document.body; parent = parent.parentElement) if (privateLabel.test(parent.getAttribute('aria-label') || '')) return false;
      return true;
    };
    const outsideMessages = n => !n.closest('article,[data-message-author-role],[data-chatgpt-search-unit-key]');
    const menus = [...document.querySelectorAll(menuSelector)].filter(n => visible(n) && outsideMessages(n));
    const nodes = [...new Set(menus.flatMap(menu => [...menu.querySelectorAll('[role="radio"],[role="menuitemradio"],[role="option"],[role="menuitem"],button')]))].filter(n => visible(n) && outsideMessages(n));
    const rows = nodes.map((n, index) => {
      const id = `native-${index}`;
      n.setAttribute('data-pdf2zh-native-option', id);
      const primary = n.querySelector('.truncate') || n.querySelector('[data-testid*="label"],.font-medium');
      return { id, label: norm(primary?.textContent || n.getAttribute('aria-label') || n.innerText), ariaLabel: norm(n.getAttribute('aria-label')), role: n.getAttribute('role'), diagnosticSafe: diagnosticSafe(n),
        modelViewToggle: n.getAttribute('data-model-picker-view-toggle') === 'true',
        fullText: captureStructure && diagnosticSafe(n) ? norm(n.innerText).slice(0, 2000) : undefined,
        selected: n.getAttribute('aria-checked') === 'true' || n.getAttribute('aria-selected') === 'true' || ['checked','on'].includes(n.getAttribute('data-state')),
        disabled: n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true' || !!n.closest('[aria-disabled="true"]'),
        submenu: n.getAttribute('aria-haspopup') === 'menu', expanded: n.getAttribute('aria-expanded'), controls: n.getAttribute('aria-controls'),
        testid: n.getAttribute('data-testid') || '', group: n.closest('[role="radiogroup"]')?.getAttribute('aria-label') || '',
        parentSection: n.parentElement?.closest('[role="menuitem"]')?.getAttribute('data-pdf2zh-native-option') || '',
        menu: menus.findLastIndex(menu => menu.contains(n)),
      };
    });
    if (captureStructure && rows.length) {
      const fragments = menus.filter(menu => !menu.closest(privateArea) && !privateLabel.test(menu.getAttribute('aria-label') || '')).slice(0, 4).map(menu => {
        const clone = menu.cloneNode(true);
        clone.querySelectorAll(`${privateArea},svg,path,style,script,select`).forEach(node => node.remove());
        for (const node of [clone, ...clone.querySelectorAll('*')]) {
          if (privateLabel.test(node.getAttribute('aria-label') || '') || (!node.childElementCount && privateLabel.test(node.textContent || ''))) { node.remove(); continue; }
          for (const attr of [...node.attributes]) if (!/^(?:id|class|role|type|disabled|aria-[\w-]+|data-[\w-]+)$/.test(attr.name)) node.removeAttribute(attr.name);
        }
        return clone.outerHTML;
      });
      rows[0].menuStructure = fragments.join('\n').slice(0, 12000);
    }
    return rows;
  }, { menuSelector: MENU, captureStructure });
}

async function clickRow(page, row) {
  await page.menuClick(`[data-pdf2zh-native-option="${row.id}"]`);
  await page.waitForTimeout(page.pollMs);
}

async function openMenuDiagnostic(page, kind) {
  return page.evaluate(({ picker, menus, kind }) => {
    const excluded = 'article,aside,nav,[data-message-author-role],[data-chatgpt-search-unit-key],[data-message-id],input,textarea,[contenteditable="true"],[data-testid*="sidebar" i],[data-testid*="profile" i],[data-testid*="account" i]';
    const privateLabel = /profile|account|personal|个人资料|账户|账号|個人資料|帳戶|帳號|プロフィール|アカウント/i;
    const safe = node => {
      if (node.closest(excluded)) return false;
      for (let parent = node; parent && parent !== document.body; parent = parent.parentElement) {
        if (privateLabel.test(parent.getAttribute('aria-label') || '')) return false;
      }
      return true;
    };
    const visible = node => safe(node) && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0 && getComputedStyle(node).visibility !== 'hidden' && !node.closest('[hidden],[inert],[aria-hidden="true"],[data-active="false"]');
    const triggers = [...document.querySelectorAll(picker)].filter(visible);
    const panels = [...document.querySelectorAll(menus)].filter(visible);
    const owns = (control, panel) => (control.getAttribute('aria-controls') || '').split(/\s+/).some(id => id && document.getElementById(id)?.contains(panel)) ||
      !!control.id && (panel.getAttribute('aria-labelledby') || '').split(/\s+/).includes(control.id);
    const owned = new Set(panels.filter(panel => panel.matches('[data-testid="composer-intelligence-picker-content"]') || triggers.some(trigger => owns(trigger, panel))));
    for (let changed = true; changed;) {
      changed = false;
      for (const panel of panels) if (!owned.has(panel) && [...owned].some(parent => parent.contains(panel) || [...parent.querySelectorAll('[aria-controls]')].filter(safe).some(control => owns(control, panel)))) {
        owned.add(panel); changed = true;
      }
    }
    const short = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    const nodes = [...new Set([...owned].flatMap(panel => [...panel.querySelectorAll('button,[role="menuitem"],[role="menuitemradio"],[role="radio"],[role="option"],[role="slider"]')]))]
      .filter(node => visible(node) && !node.querySelector(excluded) && !privateLabel.test(node.getAttribute('aria-label') || node.textContent || ''));
    return { kind, menuCount: owned.size, rows: nodes.slice(0, 48).map(node => ({
      tag: node.tagName.toLowerCase(), ...Object.fromEntries(['role', 'data-testid', 'aria-haspopup', 'aria-expanded', 'aria-checked', 'aria-selected', 'aria-disabled', 'data-state'].map(name => [name, short(node.getAttribute(name))])),
      label: short(node.getAttribute('aria-label') || node.textContent),
    })) };
  }, { picker: PICKER, menus: MENU, kind });
}

const containsWord = (label, words) => words.some(word => normalize(label).toLowerCase().includes(word));
const rowContainsWord = (row, words) => containsWord(row.ariaLabel, words) || containsWord(row.label, words);
const radioRows = rows => rows.filter(row => ['radio', 'menuitemradio', 'option'].includes(row.role));

async function pickerSnapshot(page) {
  return page.evaluate(({ picker, menus }) => {
    const eligible = node => !node.closest('article,[data-message-author-role],[data-chatgpt-search-unit-key],[inert],[data-active="false"]');
    const displayed = node => {
      if (!eligible(node) || node.closest('[hidden],[aria-hidden="true"]') || getComputedStyle(node).visibility === 'hidden') return false;
      for (let parent = node; parent; parent = parent.parentElement) if (getComputedStyle(parent).display === 'none') return false;
      return true;
    };
    const visible = node => displayed(node) && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0;
    const triggers = [...document.querySelectorAll(picker)].filter(eligible).slice(0, 8);
    const panels = [...document.querySelectorAll(menus)].filter(eligible);
    const states = triggers.map(node => ({ state: node.getAttribute('data-state') || '', expanded: node.getAttribute('aria-expanded') || '', visible: visible(node) }));
    // Radix can mark a picker open before its portalled rows receive layout.
    // Clicking its trigger again at that point closes the pending menu.
    const open = states.some(state => state.state === 'open' || state.expanded === 'true') || panels.some(displayed);
    return { open, triggers: states, menuCount: panels.length, displayedMenuCount: panels.filter(displayed).length, visibleMenuCount: panels.filter(visible).length };
  }, { picker: PICKER, menus: MENU });
}

async function openPicker(page) {
  await closeMenus(page);
  // The composer is often available several seconds before React mounts the picker.
  await poll(page, () => page.evaluate(selector => [...document.querySelectorAll(selector)].some(n => n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden'), PICKER), 'PICKER_UNAVAILABLE', '网页模型选择器尚未准备好，请稍后刷新。');
  await page.menuClick(PICKER);
  let clickedAt = Date.now();
  return poll(page, async () => {
    const rows = await menuSnapshot(page, true);
    if (rows.length) return rows;
    // A server-rendered pill can precede React's event handlers. Retry only
    // while there is no open menu; never click an already open picker closed.
    if (Date.now() - clickedAt > 800 && !(await pickerSnapshot(page)).open) {
      if (page.interactionCounts) page.interactionCounts.pickerRetries++;
      await page.menuClick(PICKER);
      clickedAt = Date.now();
    }
    return null;
  }, 'PICKER_UNAVAILABLE', '网页没有打开可识别的模型菜单。');
}

async function openSection(page, kind) {
  let rows = await openPicker(page);
  const initialMenuStructure = rows[0]?.menuStructure || '';
  const diagnosticRows = items => items.filter(row => row.diagnosticSafe !== false).slice(0, 48).map(row => Object.fromEntries(
    ['label', 'ariaLabel', 'modelViewToggle', 'fullText', 'role', 'testid', 'group', 'selected', 'disabled', 'submenu', 'expanded', 'parentSection', 'menu'].map(key =>
      [key, typeof row[key] === 'string' ? row[key].slice(0, key === 'fullText' ? 2000 : 120) : row[key]])));
  const initialRows = diagnosticRows(rows);
  let lastRows = initialRows;
  let advancedClicked = false;
  let sectionClicked = false;
  let menuReopens = 0;
  return poll(page, async () => {
    rows = await menuSnapshot(page);
    if (!rows.length && menuReopens === 0 && !(await pickerSnapshot(page)).open) {
      menuReopens++;
      rows = await openPicker(page);
      advancedClicked = false;
      sectionClicked = false;
    }
    if (rows.length) lastRows = diagnosticRows(rows);
    const groupRows = radioRows(rows).filter(row => containsWord(row.group, WORDS[kind]));
    if (groupRows.length) return filterOptions(groupRows);
    if (kind === 'model' && rows.some(row => row.modelViewToggle)) {
      const visibleModels = radioRows(rows).filter(row => !containsWord(row.group, WORDS.effort) && !/effort|thinking-time/.test(row.testid));
      if (visibleModels.length) return filterOptions(visibleModels);
    }
    if (sectionClicked) {
      const candidates = radioRows(rows);
      if (candidates.length) {
        const last = Math.max(...candidates.map(row => row.menu));
        return filterOptions(candidates.filter(row => row.menu === last));
      }
    }
    const section = rows.find(row => row.role === 'menuitem' && !row.disabled && ((kind === 'model' && row.modelViewToggle) || rowContainsWord(row, WORDS[kind])) && !(kind === 'model' && rowContainsWord(row, WORDS.effort)));
    if (section && !sectionClicked) {
      const inline = radioRows(rows).filter(row => row.parentSection === section.id);
      if (inline.length) return filterOptions(inline);
      if (section.expanded === 'true') { sectionClicked = true; return null; }
      // Some rollouts use plain menuitems rather than aria-haspopup submenus.
      await clickRow(page, section);
      sectionClicked = true;
      return null;
    }
    const advanced = rows.find(row => rowContainsWord(row, WORDS.advanced));
    if (advanced && !advancedClicked && advanced.expanded !== 'true') {
      await clickRow(page, advanced);
      advancedClicked = true;
      return null;
    }
    if (kind === 'model' && !advanced && !sectionClicked) {
      const flat = radioRows(rows).filter(row => !containsWord(row.group, WORDS.effort) && !/effort|thinking-time/.test(row.testid));
      if (flat.length) return filterOptions(flat);
    }
    if (kind === 'effort' && !advanced && !section) {
      return { absent: true };
    }
    return null;
  }, 'PICKER_UNAVAILABLE', '网页选项结构暂不受支持，没有猜测可用模型或推理强度。').catch(async error => {
    let current = { kind };
    try { current = await openMenuDiagnostic(page, kind); } catch { /* Retain rows already read if the page disappears. */ }
    error.diagnostic = { ...error.diagnostic, openMenu: { ...current, initialRows, lastRows, initialMenuStructure, advancedClicked, sectionClicked, menuReopens } };
    throw error;
  });
}

async function readSliderState(page) {
  return page.evaluate(() => {
    const visible = n => n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden' && !n.closest('[inert],[data-active="false"]');
    const roots = [...document.querySelectorAll('[data-model-reasoning-effort-slider],[data-model-picker-power-slider]')].filter(n => visible(n) && !n.closest('article,[data-message-author-role],[data-chatgpt-search-unit-key]') && n.closest('[role="menu"],[data-testid="composer-intelligence-picker-content"]'));
    if (roots.length !== 1) return null;
    const root = roots[0];
    const control = root.closest('[role="menuitem"]');
    const thumb = root.querySelector('[role="slider"]');
    if (!control || !thumb || !visible(control)) return null;
    control.setAttribute('data-pdf2zh-native-slider', 'owner');
    const descriptionIds = [...new Set([control, thumb].flatMap(n => (n.getAttribute('aria-describedby') || '').split(/\s+/)).filter(Boolean))];
    const descriptions = descriptionIds.map(id => document.getElementById(id)?.textContent?.trim()).filter(Boolean);
    const label = (thumb.getAttribute('aria-valuetext') || descriptions[0] || '').split(/[,;，；。\n]/)[0].trim();
    const announcement = descriptions.join(' ');
    const ordinal = announcement.match(/第\s*(\d+)\s*[项項]/) || announcement.match(/(?:选项|選項|\bitem\b|\boption\b)\s*(\d+)/i) || announcement.match(/(?:^|[,，]\s*)(\d+)\s+of\s+\d+/i);
    const total = announcement.match(/共\s*(\d+)\s*[项項]/) || announcement.match(/\bof\s+(\d+)/i);
    const number = name => { const raw = thumb.getAttribute(name); return raw === null || raw.trim() === '' ? NaN : Number(raw); };
    const ticks = [...root.querySelectorAll('[data-locked][data-selected]')].map(n => n.getAttribute('data-locked'));
    return { label, hasLabelSource: thumb.hasAttribute('aria-valuetext') || descriptionIds.length > 0,
      announcementPosition: ordinal ? Number(ordinal[1]) : null, announcementTotal: total ? Number(total[1]) : null,
      value: number('aria-valuenow'), min: number('aria-valuemin'), max: number('aria-valuemax'), ticks,
      hasLocked: root.matches('[data-locked="true"]') || !!root.querySelector('[data-locked="true"]'),
      disabled: control.getAttribute('aria-disabled') === 'true' || thumb.getAttribute('aria-disabled') === 'true' || !!root.querySelector('[aria-disabled="true"]') };
  });
}

async function revealSimpleSlider(page) {
  const rememberedAdvanced = await page.evaluate(() => {
    const active = node => !!node && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0 && getComputedStyle(node).visibility !== 'hidden' && !node.closest('[inert],[data-active="false"]');
    const legacy = [...document.querySelectorAll('[data-testid="composer-intelligence-picker-content"]')].some(menu => {
      if (!active(menu)) return false;
      const simple = menu.querySelector('[data-testid="composer-model-picker-slider-simple-view"]');
      const advanced = menu.querySelector('[data-testid="composer-model-picker-slider-advanced-view"]');
      return simple && !active(simple) && active(advanced);
    });
    const shell = [...document.querySelectorAll('[data-model-picker-view]')].some(view => active(view) && view.getAttribute('data-model-picker-view') !== 'simple' &&
      [...view.querySelectorAll('[role="menuitemradio"],[role="radio"],[role="option"]')].some(active));
    return { legacy, shell };
  });
  if (!rememberedAdvanced.legacy && !rememberedAdvanced.shell) return;
  const rows = await menuSnapshot(page, true);
  const toggle = rows.find(row => row.role === 'menuitem' && !row.disabled && (rememberedAdvanced.shell ? row.modelViewToggle :
    row.expanded === 'true' && rowContainsWord(row, WORDS.model) && !rowContainsWord(row, WORDS.effort)));
  if (!toggle && rememberedAdvanced.shell) {
    let selectedModelReturnAttempted = false;
    try {
      const selected = radioRows(rows).filter(row => row.selected);
      if (selected.length !== 1 || selected[0].disabled) throw coded('PICKER_UNAVAILABLE', '高级模型列表没有唯一且可用的已选模型，无法返回推理强度滑块。');
      selectedModelReturnAttempted = true;
      await clickRow(page, selected[0]);
      await openPicker(page);
      await poll(page, () => readSliderState(page), 'PICKER_UNAVAILABLE', '确认当前模型后网页未返回推理强度滑块，已停止。');
      return;
    } catch (error) {
      const current = await menuSnapshot(page, true).catch(() => []);
      const snapshot = items => items.filter(row => row.diagnosticSafe !== false).slice(0, 48).map(row => ({
        label: row.label.slice(0, 120), ariaLabel: row.ariaLabel.slice(0, 120), role: row.role, selected: row.selected, disabled: row.disabled,
      }));
      error.diagnostic = { ...error.diagnostic, openMenu: { kind: 'effort', selectedModelReturnAttempted,
        initialRows: snapshot(rows), lastRows: snapshot(current), initialMenuStructure: rows[0]?.menuStructure || '', lastMenuStructure: current[0]?.menuStructure || '' } };
      throw error;
    }
  }
  if (!toggle) throw coded('PICKER_UNAVAILABLE', '网页模型菜单停留在高级视图，未找到返回推理强度滑块的控件。');
  await clickRow(page, toggle);
  await poll(page, () => readSliderState(page), 'PICKER_UNAVAILABLE', '网页未返回推理强度滑块，请刷新模型。');
}

export async function readSlider(page, requested) {
  let current = await readSliderState(page);
  if (!current) return null;
  if (!current.hasLabelSource || ![current.min, current.max, current.value].every(Number.isInteger) || current.min < 0 || current.max - current.min > 12 || current.value < current.min || current.value > current.max) {
    throw coded('PICKER_UNAVAILABLE', '网页推理强度滑块没有明确的范围或文字标签，已暂停。');
  }
  const original = current.value;
  const range = { min: current.min, max: current.max };
  if ((current.ticks.length && (current.ticks.length !== range.max - range.min + 1 || current.ticks.some(value => !['true', 'false'].includes(value)))) || (current.hasLocked && !current.ticks.length)) {
    throw coded('PICKER_UNAVAILABLE', '网页推理强度锁定状态不明确，已暂停。');
  }
  const available = Array.from({ length: range.max - range.min + 1 }, (_, index) => index + range.min).filter(position => current.ticks[position - range.min] !== 'true');
  if (!available.includes(original) || available.some((position, index) => index > 0 && position !== available[index - 1] + 1)) {
    throw coded('OPTION_UNAVAILABLE', '当前推理强度不可用，或可用档位被锁定档位隔开。请在网页选择可用档位后刷新。');
  }
  let acceptedRange = { min: current.min, max: current.max, ticks: [...current.ticks] };
  let restoreSafe = true;
  let lastObservation = current;
  let lastReason = 'label_not_synchronized';
  let reopenedMenus = 0;
  const addSliderDiagnostic = (error, target) => {
    const snapshot = value => value ? {
      value: value.value, min: value.min, max: value.max,
      ticks: value.ticks.map(item => ['true', 'false'].includes(item) ? item : 'unknown'),
      hasLocked: value.hasLocked, disabled: value.disabled,
      hasLabel: !!value.label, announcementPosition: value.announcementPosition, announcementTotal: value.announcementTotal,
    } : null;
    error.diagnostic = { ...error.diagnostic, sliderDiagnostic: {
      reason: lastReason, expected: { target, original, min: acceptedRange.min, max: acceptedRange.max, ticks: [...acceptedRange.ticks], available: [...available] },
      observed: snapshot(lastObservation), reopenedMenus,
    } };
    return error;
  };
  const observe = async deadline => {
    let next = await readSliderState(page);
    if (!next) { lastObservation = null; lastReason = 'missing_slider'; }
    if (!next && Date.now() < deadline) {
      const menuOpen = await page.evaluate(selector => [...document.querySelectorAll(selector)].some(node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && getComputedStyle(node).visibility !== 'hidden' && !node.closest('[inert],[data-active="false"]');
      }), MENU);
      if (!menuOpen) {
        // Reopen only a demonstrably closed picker; missing slider nodes inside
        // an open menu may merely be remounting and must not trigger more keys.
        const recovery = { ...page, timeoutMs: Math.max(1, deadline - Date.now()) };
        reopenedMenus++;
        await openPicker(recovery);
        recovery.timeoutMs = Math.max(1, deadline - Date.now());
        await revealSimpleSlider(recovery);
        next = await readSliderState(page);
      }
    }
    lastObservation = next;
    if (!next) { lastReason = 'missing_slider'; return null; }
    const rangeChanged = next.min !== acceptedRange.min || next.max !== acceptedRange.max;
    const locksChanged = JSON.stringify(next.ticks) !== JSON.stringify(acceptedRange.ticks);
    if (rangeChanged || locksChanged) {
      const completeTicks = state => [state.min, state.max].every(Number.isInteger) && state.max >= state.min && state.max - state.min <= 12 && state.ticks.length === state.max - state.min + 1 && state.ticks.every(value => ['true', 'false'].includes(value));
      const nextAvailable = next.ticks.flatMap((value, index) => value === 'false' ? [next.min + index] : []);
      const overlap = Math.min(next.ticks.length, acceptedRange.ticks.length);
      // The page may hide/show trailing upgrade-only ticks when a tier is
      // selected. Accept only a complete, equivalent set of usable positions.
      const sameUsableRange = next.min === range.min && next.max !== acceptedRange.max && completeTicks(next) && completeTicks(acceptedRange)
        && JSON.stringify(nextAvailable) === JSON.stringify(available)
        && next.ticks.slice(0, overlap).every((value, index) => value === acceptedRange.ticks[index])
        && [...next.ticks.slice(overlap), ...acceptedRange.ticks.slice(overlap)].every(value => value === 'true');
      if (sameUsableRange) acceptedRange = { min: next.min, max: next.max, ticks: [...next.ticks] };
      else {
        restoreSafe = false;
        lastReason = rangeChanged ? 'changed_range' : 'changed_locks';
        throw coded('PICKER_UNAVAILABLE', rangeChanged ? '网页推理强度范围发生变化，请刷新模型。' : '网页推理强度锁定状态发生变化，请刷新模型。');
      }
    }
    if (!available.includes(next.value)) {
      restoreSafe = false;
      lastReason = 'unavailable_value';
      throw coded('OPTION_UNAVAILABLE', '网页切换到了不可用推理强度，已停止操作。');
    }
    current = next;
    return next;
  };
  const synchronized = async (target, previousLabel, requireLabel = true) => {
    let signature = '';
    let stableSince = 0;
    const deadline = Date.now() + page.timeoutMs;
    try {
      return await poll(page, async () => {
        const next = await observe(deadline);
        if (!next) { signature = ''; return null; }
        // Keep the newest position even while labels lag, so failure recovery
        // never sends keys from an obsolete position.
        if (next.value !== target) { lastReason = 'position_not_synchronized'; signature = ''; return null; }
        if (!requireLabel) return next;
        const ordinalMatches = next.announcementPosition === null || next.announcementPosition === next.value - next.min + 1;
        const totalMatches = next.announcementTotal === null || next.announcementTotal === next.max - next.min + 1;
        lastReason = !ordinalMatches || !totalMatches ? 'announcement_not_synchronized' : 'label_not_synchronized';
        if (!next.label || next.label === previousLabel || !ordinalMatches || !totalMatches) { signature = ''; return null; }
        const candidate = JSON.stringify([next.value, next.label, next.announcementPosition, next.announcementTotal]);
        if (candidate !== signature) { signature = candidate; stableSince = Date.now(); return null; }
        return Date.now() - stableSince >= 150 ? next : null;
      }, 'SELECTION_UNVERIFIED', '网页推理强度的位置与文字标签尚未同步，请稍后刷新。');
    } catch (error) { throw addSliderDiagnostic(error, target); }
  };
  const move = async (target, requireLabel = true) => {
    if (!available.includes(target)) throw coded('OPTION_UNAVAILABLE', '当前账号没有提供该推理强度。');
    for (let step = 0; current.value !== target && step < 15; step++) {
      if (current.disabled) throw coded('OPTION_UNAVAILABLE', '当前账号的推理强度滑块不可用。');
      const direction = target > current.value ? 1 : -1;
      const nextPosition = current.value + direction;
      const previousLabel = current.label;
      await page.press(direction > 0 ? 'ArrowRight' : 'ArrowLeft', '[data-pdf2zh-native-slider="owner"]');
      await synchronized(nextPosition, previousLabel, requireLabel);
    }
    if (current.value !== target) throw coded('SELECTION_UNVERIFIED', '网页没有确认目标推理强度。');
  };
  let destination = original;
  const choices = [];
  let failure;
  try {
    await synchronized(original);
    for (const position of available) {
      await move(position);
      if (!current.label) throw coded('PICKER_UNAVAILABLE', '网页没有提供推理强度文字标签。');
      choices.push({ value: current.label, label: current.label, position });
    }
    if (new Set(choices.map(item => item.value)).size !== choices.length) throw coded('PICKER_UNAVAILABLE', '网页推理强度标签无法区分，已暂停。');
    if (requested) {
      const selected = choices.find(item => effortKey(item.value) === effortKey(requested));
      if (!selected) throw coded('OPTION_UNAVAILABLE', `当前账号没有提供推理强度：${requested}。`);
      destination = selected.position;
    }
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    if (restoreSafe) {
      try {
        if (failure) {
          // If a remount hid the post-key state, recover an actual observation
          // before deciding whether any restoration key is necessary.
          const deadline = Date.now() + page.timeoutMs;
          await poll(page, () => observe(deadline), 'SELECTION_UNVERIFIED', '无法重新确认推理强度位置。');
        }
        await move(destination, !failure);
      }
      catch (error) { if (!failure) throw error; }
    }
  }
  return { options: choices.map(({ value, label }) => ({ value, label })), selected: current.label };
}

export async function readChoices(page, kind, requested) {
  try {
    if (kind === 'effort') {
      await openPicker(page);
      await revealSimpleSlider(page);
      const slider = await readSlider(page, requested);
      if (slider) return slider;
    }
    let options = await openSection(page, kind);
    if (options.absent) options = [];
    if (requested) {
      const matches = item => kind === 'effort' ? effortKey(item.value) === effortKey(requested) : item.value === requested;
      const selected = options.find(matches);
      if (!selected) throw coded('OPTION_UNAVAILABLE', `当前账号的网页没有可用选项：${requested}。请刷新列表重新选择。`);
      if (!selected.selected) {
        await clickRow(page, selected);
        options = await openSection(page, kind);
      }
      if (!Array.isArray(options) || !options.some(item => matches(item) && item.selected)) throw coded('SELECTION_UNVERIFIED', `网页未确认 ${requested} 已被选中。`);
    }
    return { options: options.map(({ value, label }) => ({ value, label })), selected: options.find(item => item.selected)?.value || '' };
  } finally { await closeMenus(page); }
}

export async function catalog(page, model, effort) {
  try {
    const evidence = await ensureChat(page);
    const models = await readChoices(page, 'model', model);
    if (!models.options.length || !models.selected) throw coded('PICKER_UNAVAILABLE', '网页未明确列出可用模型及当前选择，请稍后刷新。');
    const efforts = await readChoices(page, 'effort', effort);
    const verified = await readChoices(page, 'model');
    if (!verified.selected || verified.selected !== models.selected || (efforts.options.length && !efforts.selected)) throw coded('SELECTION_UNVERIFIED', '读取推理强度后模型发生变化或选择不明确，已停止发送。请刷新模型列表。');
    await ensureChat(page);
    return { models: verified.options, efforts: efforts.options, model: verified.selected, effort: efforts.selected, mode: modeFromEvidence(evidence) };
  } catch (error) {
    if (/^(PICKER|SELECTION|CONTROL)_/.test(error.code || '')) {
      try { error.diagnostic = { ...error.diagnostic, ...await inspectCompatibility(page) }; } catch { /* Preserve the original error if inspection fails. */ }
    }
    throw error;
  }
}
