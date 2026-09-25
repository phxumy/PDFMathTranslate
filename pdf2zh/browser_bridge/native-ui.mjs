// Target-scoped CDP DOM adapter. The caller owns connection/authorization.
// This module neither starts browsers nor changes browser-identification flags.
import {
  WORDS, PICKER, coded, filterOptions, normalize,
  inspectPage as inspectLegacyPage, connectionStatus as legacyConnectionStatus,
} from './ui.mjs';

export { coded };

const MENU = '[role="menu"],[role="listbox"],[data-testid="composer-intelligence-picker-content"]';
const KEY_CODES = { Escape: 27, ArrowLeft: 37, ArrowRight: 39, ArrowUp: 38, ArrowDown: 40, Enter: 13, Home: 36, End: 35 };

export function createNativePage(client, { timeoutMs = 9000, pollMs = 150 } = {}) {
  if (!client?.Runtime?.evaluate || !client?.Input?.dispatchMouseEvent || !client?.Input?.dispatchKeyEvent) {
    throw coded('CDP_UNAVAILABLE', '原生浏览器连接没有提供当前标签页的操作接口。');
  }
  const page = {
    client, timeoutMs, pollMs,
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
          return rect.width > 0 && rect.height > 0 && getComputedStyle(n).visibility !== 'hidden';
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
      await client.Input.dispatchMouseEvent({ type: 'mouseMoved', ...point });
      await client.Input.dispatchMouseEvent({ type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
      await client.Input.dispatchMouseEvent({ type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
    },
    async press(key, selector) {
      if (!(key in KEY_CODES)) throw coded('INVALID_KEY', '不支持的浏览器按键。');
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
    const outsideMessages = n => !n.closest('article,[data-message-author-role]');
    const modeControls = [...document.querySelectorAll('button[role="radio"],[role="tab"],button[data-testid*="mode"]')].filter(n => visible(n) && outsideMessages(n));
    const selected = n => n && (n.getAttribute('aria-checked') === 'true' || n.getAttribute('aria-selected') === 'true' || ['on','active','checked'].includes(n.getAttribute('data-state')));
    const chatControl = modeControls.find(n => /^(Chat|聊天|聊天模式|チャット)$/i.test(norm(n.textContent)));
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

export async function ensureChat(page) {
  const evidence = await inspectPage(page);
  const state = connectionStatus(evidence);
  if (state.error_code) throw Object.assign(coded(state.error_code, state.message), { status: state.status });
  if (!evidence.composer || evidence.login) throw coded('LOGIN_REQUIRED', '请先在浏览器完成 ChatGPT 登录，再刷新模型。');
  if (!['chat', 'legacy-chat'].includes(modeFromEvidence(evidence))) {
    throw coded('CHAT_MODE_UNVERIFIED', '无法确认普通 Chat 模式，已停止发送。请在已授权浏览器打开普通 Chat 后刷新。');
  }
  return evidence;
}

async function closeMenus(page) {
  await page.press('Escape');
  await page.press('Escape');
}

async function menuSnapshot(page) {
  return page.evaluate(menuSelector => {
    const visible = n => n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden';
    const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
    document.querySelectorAll('[data-pdf2zh-native-option]').forEach(n => n.removeAttribute('data-pdf2zh-native-option'));
    const menus = [...document.querySelectorAll(menuSelector)].filter(n => visible(n) && !n.closest('article,[data-message-author-role]'));
    const nodes = [...new Set(menus.flatMap(menu => [...menu.querySelectorAll('[role="radio"],[role="menuitemradio"],[role="option"],[role="menuitem"],button')]))].filter(visible);
    return nodes.map((n, index) => {
      const id = `native-${index}`;
      n.setAttribute('data-pdf2zh-native-option', id);
      const primary = n.querySelector('[data-testid*="label"],.font-medium');
      return { id, label: norm(primary?.textContent || n.getAttribute('aria-label') || n.innerText), role: n.getAttribute('role'),
        selected: n.getAttribute('aria-checked') === 'true' || n.getAttribute('aria-selected') === 'true' || ['checked','on'].includes(n.getAttribute('data-state')),
        disabled: n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true' || !!n.closest('[aria-disabled="true"]'),
        submenu: n.getAttribute('aria-haspopup') === 'menu', expanded: n.getAttribute('aria-expanded'), controls: n.getAttribute('aria-controls'),
        testid: n.getAttribute('data-testid') || '', group: n.closest('[role="radiogroup"]')?.getAttribute('aria-label') || '',
        parentSection: n.parentElement?.closest('[role="menuitem"]')?.getAttribute('data-pdf2zh-native-option') || '',
        menu: menus.findLastIndex(menu => menu.contains(n)),
      };
    });
  }, MENU);
}

async function clickRow(page, row) {
  await page.click(`[data-pdf2zh-native-option="${row.id}"]`);
  await page.waitForTimeout(page.pollMs);
}

const containsWord = (label, words) => words.some(word => normalize(label).toLowerCase().includes(word));
const radioRows = rows => rows.filter(row => ['radio', 'menuitemradio', 'option'].includes(row.role));

async function openPicker(page) {
  await closeMenus(page);
  // The composer is often available several seconds before React mounts the picker.
  await poll(page, () => page.evaluate(selector => [...document.querySelectorAll(selector)].some(n => n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden'), PICKER), 'PICKER_UNAVAILABLE', '网页模型选择器尚未准备好，请稍后刷新。');
  await page.click(PICKER);
  let clickedAt = Date.now();
  return poll(page, async () => {
    const rows = await menuSnapshot(page);
    if (rows.length) return rows;
    // A server-rendered pill can precede React's event handlers. Retry only
    // while there is no open menu; never click an already open picker closed.
    if (Date.now() - clickedAt > 800) { await page.click(PICKER); clickedAt = Date.now(); }
    return null;
  }, 'PICKER_UNAVAILABLE', '网页没有打开可识别的模型菜单。');
}

async function openSection(page, kind) {
  let rows = await openPicker(page);
  let advancedClicked = false;
  let sectionClicked = false;
  return poll(page, async () => {
    rows = await menuSnapshot(page);
    const groupRows = radioRows(rows).filter(row => containsWord(row.group, WORDS[kind]));
    if (groupRows.length) return filterOptions(groupRows);
    if (sectionClicked) {
      const candidates = radioRows(rows);
      if (candidates.length) {
        const last = Math.max(...candidates.map(row => row.menu));
        return filterOptions(candidates.filter(row => row.menu === last));
      }
    }
    const section = rows.find(row => row.role === 'menuitem' && !row.disabled && containsWord(row.label, WORDS[kind]) && !(kind === 'model' && containsWord(row.label, WORDS.effort)));
    if (section && !sectionClicked) {
      const inline = radioRows(rows).filter(row => row.parentSection === section.id);
      if (inline.length) return filterOptions(inline);
      if (section.expanded === 'true') { sectionClicked = true; return null; }
      // Some rollouts use plain menuitems rather than aria-haspopup submenus.
      await clickRow(page, section);
      sectionClicked = true;
      return null;
    }
    const advanced = rows.find(row => containsWord(row.label, WORDS.advanced));
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
  }, 'PICKER_UNAVAILABLE', '网页选项结构暂不受支持，没有猜测可用模型或推理强度。');
}

async function readSliderState(page) {
  return page.evaluate(() => {
    const visible = n => n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden';
    const roots = [...document.querySelectorAll('[data-model-reasoning-effort-slider]')].filter(n => visible(n) && n.closest('[role="menu"],[data-testid="composer-intelligence-picker-content"]') && !n.closest('[data-active="false"]'));
    if (roots.length !== 1) return null;
    const root = roots[0];
    const control = root.closest('[role="menuitem"]');
    const thumb = root.querySelector('[role="slider"]');
    if (!control || !thumb || !visible(control)) return null;
    control.setAttribute('data-pdf2zh-native-slider', 'owner');
    const descriptions = [...new Set([control, thumb].flatMap(n => (n.getAttribute('aria-describedby') || '').split(/\s+/)).filter(Boolean))]
      .map(id => document.getElementById(id)?.textContent?.trim()).filter(Boolean);
    const label = (thumb.getAttribute('aria-valuetext') || descriptions[0] || '').split(/[,;，；。\n]/)[0].trim();
    const number = name => { const raw = thumb.getAttribute(name); return raw === null || raw.trim() === '' ? NaN : Number(raw); };
    return { label, value: number('aria-valuenow'), min: number('aria-valuemin'), max: number('aria-valuemax'), disabled: control.getAttribute('aria-disabled') === 'true' || thumb.getAttribute('aria-disabled') === 'true' };
  });
}

export async function readSlider(page, requested) {
  let current = await readSliderState(page);
  if (!current) return null;
  if (!current.label || ![current.min, current.max, current.value].every(Number.isInteger) || current.min < 0 || current.max - current.min > 12 || current.value < current.min || current.value > current.max) {
    throw coded('PICKER_UNAVAILABLE', '网页推理强度滑块没有明确的范围或文字标签，已暂停。');
  }
  const original = current.value;
  const range = { min: current.min, max: current.max };
  const move = async target => {
    for (let step = 0; current.value !== target && step < 15; step++) {
      if (current.disabled) throw coded('OPTION_UNAVAILABLE', '当前账号的推理强度滑块不可用。');
      const previous = current.value;
      await page.press(target > current.value ? 'ArrowRight' : 'ArrowLeft', '[data-pdf2zh-native-slider="owner"]');
      current = await poll(page, async () => {
        const next = await readSliderState(page);
        if (!next || next.min !== range.min || next.max !== range.max) throw coded('PICKER_UNAVAILABLE', '网页推理强度范围发生变化，请刷新模型。');
        return next.value !== previous ? next : null;
      }, 'SELECTION_UNVERIFIED', '无法确认网页推理强度已改变。');
    }
    if (current.value !== target) throw coded('SELECTION_UNVERIFIED', '网页没有确认目标推理强度。');
  };
  let destination = original;
  const choices = [];
  try {
    for (let position = current.min; position <= current.max; position++) {
      await move(position);
      if (!current.label) throw coded('PICKER_UNAVAILABLE', '网页没有提供推理强度文字标签。');
      choices.push({ value: current.label, label: current.label, position });
    }
    if (new Set(choices.map(item => item.value)).size !== choices.length) throw coded('PICKER_UNAVAILABLE', '网页推理强度标签无法区分，已暂停。');
    if (requested) {
      const selected = choices.find(item => item.value === requested);
      if (!selected) throw coded('OPTION_UNAVAILABLE', `当前账号没有提供推理强度：${requested}。`);
      destination = selected.position;
    }
  } finally { await move(destination); }
  return { options: choices.map(({ value, label }) => ({ value, label })), selected: current.label };
}

export async function readChoices(page, kind, requested) {
  try {
    if (kind === 'effort') {
      await openPicker(page);
      const slider = await readSlider(page, requested);
      if (slider) return slider;
    }
    let options = await openSection(page, kind);
    if (options.absent) options = [];
    if (requested) {
      const selected = options.find(item => item.value === requested);
      if (!selected) throw coded('OPTION_UNAVAILABLE', `当前账号的网页没有可用选项：${requested}。请刷新列表重新选择。`);
      if (!selected.selected) {
        await clickRow(page, selected);
        options = await openSection(page, kind);
      }
      if (!Array.isArray(options) || !options.some(item => item.value === requested && item.selected)) throw coded('SELECTION_UNVERIFIED', `网页未确认 ${requested} 已被选中。`);
    }
    return { options: options.map(({ value, label }) => ({ value, label })), selected: options.find(item => item.selected)?.value || '' };
  } finally { await closeMenus(page); }
}

export async function catalog(page, model, effort) {
  const evidence = await ensureChat(page);
  const models = await readChoices(page, 'model', model);
  if (!models.options.length || !models.selected) throw coded('PICKER_UNAVAILABLE', '网页未明确列出可用模型及当前选择，请稍后刷新。');
  const efforts = await readChoices(page, 'effort', effort);
  const verified = await readChoices(page, 'model');
  if (!verified.selected || verified.selected !== models.selected || (efforts.options.length && !efforts.selected)) throw coded('SELECTION_UNVERIFIED', '读取推理强度后模型发生变化或选择不明确，已停止发送。请刷新模型列表。');
  await ensureChat(page);
  return { models: verified.options, efforts: efforts.options, model: verified.selected, effort: efforts.selected, mode: modeFromEvidence(evidence) };
}
