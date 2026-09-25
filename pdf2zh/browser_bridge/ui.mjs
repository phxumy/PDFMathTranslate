// DOM adapters deliberately discover account-specific choices instead of naming models.
export const WORDS = {
  advanced: ['advanced', '高级', '進階', '詳細設定', '詳細表示', 'erweitert', '고급', 'avanzado', 'avancé'],
  model: ['model', '模型', 'モデル', '모델', 'modelo', 'modèle', 'modell'],
  effort: ['effort', 'thinking', 'reasoning', '强度', '強度', '思考', '推理', '推論レベル', '추론', 'aufwand', 'esfuerzo'],
};
export const PICKER = '[data-testid="model-switcher-dropdown-button"],button.__composer-pill[aria-haspopup="menu"]';

export function normalize(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

export function filterOptions(rows) {
  const seen = new Set();
  return rows.filter(row => row.visible !== false && !row.disabled && !row.submenu && row.label)
    .filter(row => !/upgrade|not available|unavailable|coming soon|升级|升級|不可用|无法使用|即将推出/i.test(row.label))
    .map(row => ({ ...row, label: normalize(row.label), value: normalize(row.label) }))
    .filter(row => !seen.has(row.value) && seen.add(row.value));
}

export function modeFromEvidence(evidence) {
  if (evidence.workSelected || evidence.workPath) return 'work';
  if (evidence.chatSelected) return 'chat';
  if (evidence.hasModeControls) return 'unknown';
  // Support the classic site only with a positively identified legacy header picker.
  if (evidence.home && evidence.legacyPicker && evidence.composer && !evidence.workMarker) return 'legacy-chat';
  return 'unknown';
}

export async function inspectPage(page) {
  return page.evaluate(() => {
    const visible = n => !!n && n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden';
    const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
    const controls = [...document.querySelectorAll('button[role="radio"], [role="tab"], button[data-testid*="mode"]')].filter(visible);
    const selected = n => n && ['true', 'on', 'active'].includes(n.getAttribute('aria-checked') || n.getAttribute('aria-selected') || n.getAttribute('data-state'));
    const chat = controls.find(n => /^(Chat|聊天|聊天模式|チャット)$/i.test(norm(n.textContent)));
    const work = controls.find(n => /^(Work|工作|工作模式|ワーク)$/i.test(norm(n.textContent)));
    const composer = [...document.querySelectorAll('#prompt-textarea,textarea[name="prompt-textarea"]')].some(visible);
    const login = [...document.querySelectorAll('button,a')].some(n => visible(n) && /^(Log in|Sign in|登录|登入|ログイン)$/i.test(norm(n.textContent)));
    const authPage = ['auth.openai.com', 'auth0.openai.com'].includes(location.hostname) ||
      (location.hostname === 'chatgpt.com' && /^\/(auth|login|log-in)(\/|$)/.test(location.pathname));
    // Inspect rendered error labels only, never input values, cookies or request bodies.
    const errorLabels = authPage && !composer ? [...document.querySelectorAll('h1,h2,p,pre,code,[role="alert"],[data-testid*="error"]')]
      .filter(visible).map(n => norm(n.textContent)) : [];
    const routeError = errorLabels.some(text => /\bRoute\s*Error\b|\bInvalid content type\b/i.test(text));
    const genericAuthError = errorLabels.some(text => /^(Oops[,!]? (an error occurred|something went wrong)|糟糕[，,！!]?\s*(出错了|发生错误)|出错了|エラーが発生しました)/i.test(text));
    // Performance entries are passive response metadata; no login request is replayed.
    const verifyStatus = authPage ? [...performance.getEntriesByType('resource')].reverse().find(entry => {
      try { const url = new URL(entry.name); return url.origin === location.origin && url.pathname === '/api/accounts/password/verify'; }
      catch { return false; }
    })?.responseStatus : undefined;
    const authError = Boolean(authPage && !composer && (routeError || genericAuthError));
    return {
      url: location.href, home: location.pathname === '/', composer, login,
      automatedBrowser: navigator.webdriver === true,
      authPage, authError, authHttpStatus: authError && Number(verifyStatus) === 403 ? 403 : null,
      chatSelected: !!selected(chat), workSelected: !!selected(work), hasModeControls: !!(chat || work),
      workPath: /^\/(work|agent)(\/|$)/i.test(location.pathname),
      workMarker: [...document.querySelectorAll('[data-testid*="work"], [data-mode="work"]')].some(visible),
      legacyPicker: visible(document.querySelector('[data-testid="model-switcher-dropdown-button"]')),
      modelPill: [...document.querySelectorAll('[data-testid="model-switcher-dropdown-button"],button.__composer-pill[aria-haspopup="menu"]')].filter(visible).map(n => norm(n.innerText)).join(' / '),
      challenge: !composer && /just a moment|please wait|verify you are human|请稍候|请稍等|验证您是真人|しばらくお待ち/i.test(document.title),
    };
  });
}

export function connectionStatus(evidence) {
  if (evidence.challenge) return { ok: false, status: 'verification_required', error_code: 'VERIFY_REQUIRED', message: evidence.automatedBrowser
    ? 'ChatGPT 停在网页人机验证，当前浏览器被识别为程序控制环境。此接入方式尚未通过网站验证，翻译已暂停；请勿反复点击登录或刷新。'
    : 'ChatGPT 正在等待网页人机验证，翻译尚未开始。请在浏览器完成验证；若反复回到此页面，请先检查同一网络下日常浏览器能否访问，不要反复点击登录或刷新。' };
  if (evidence.authError) {
    return { ok: false, status: 'auth_error', error_code: 'AUTH_PAGE_ERROR',
      message: evidence.authHttpStatus === 403
        ? 'ChatGPT 登录验证请求被服务拒绝（HTTP 403），登录页随后显示 Route Error。尚未登录成功。再次点“登录 ChatGPT”可从首页重新开始；登录信息会保留。'
        : 'ChatGPT 登录页面发生错误，尚未登录成功。再次点“登录 ChatGPT”可从首页重新开始；登录信息会保留。' };
  }
  if (evidence.composer && !evidence.login) return { ok: true, status: 'ready', message: '专用浏览器已连接，登录状态可复用。请刷新可用模型。' };
  return { ok: true, status: 'needs_login', message: '请在专用浏览器中登录 ChatGPT，完成后点“刷新模型 / 状态”。' };
}

export async function prepareLoginPage(page) {
  let evidence = await inspectPage(page);
  // Only an explicit Login action resets a recognized error page. Ordinary forms,
  // verification screens and existing chats retain their current state.
  if (evidence.authError && !evidence.challenge) {
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    evidence = await inspectPage(page);
  }
  await page.bringToFront();
  return connectionStatus(evidence);
}

export async function ensureChat(page) {
  let evidence = await inspectPage(page);
  const state = connectionStatus(evidence);
  if (state.error_code) throw Object.assign(coded(state.error_code, state.message), { status: state.status });
  if (evidence.login || !evidence.composer) throw coded('LOGIN_REQUIRED', '请在专用浏览器中登录 ChatGPT，完成验证后刷新模型列表。');
  if (modeFromEvidence(evidence) === 'work') {
    const chat = page.getByRole('radio', { name: /^(Chat|聊天|聊天模式|チャット)$/i }).first();
    if (await chat.isVisible().catch(() => false)) {
      await chat.click();
      await page.waitForTimeout(400);
      evidence = await inspectPage(page);
    }
  }
  if (!['chat', 'legacy-chat'].includes(modeFromEvidence(evidence))) {
    throw coded('CHAT_MODE_UNVERIFIED', '无法确认普通 Chat 模式，已暂停。请在专用浏览器的新聊天中选择 Chat，再刷新。');
  }
  return evidence;
}

export function coded(code, message) {
  return Object.assign(new Error(message), { code });
}

async function closeMenus(page) {
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
}

async function openPicker(page) {
  await closeMenus(page);
  const picker = page.locator(PICKER).filter({ visible: true }).first();
  if (!await picker.count()) throw coded('PICKER_UNAVAILABLE', '网页未显示可识别的模型选择器，请登录后刷新。');
  await picker.click();
  await page.waitForTimeout(200);
}

// Mark only visible elements in the currently open model menu; chat messages are excluded.
async function markMenuNodes(page) {
  return page.evaluate(() => {
    const visible = n => n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0 && getComputedStyle(n).visibility !== 'hidden';
    const norm = t => String(t || '').replace(/\s+/g, ' ').trim();
    document.querySelectorAll('[data-pdf2zh-option]').forEach(node => node.removeAttribute('data-pdf2zh-option'));
    const scopes = [...document.querySelectorAll('[role="menu"],[role="listbox"],[data-testid="composer-intelligence-picker-content"],[data-testid="composer-model-picker-slider-advanced-view"]')].filter(visible);
    const nodes = [...new Set(scopes.flatMap(scope => [...scope.querySelectorAll('[role="radio"],[role="menuitemradio"],[role="option"],[role="menuitem"],button')]))].filter(visible);
    return nodes.map((node, index) => {
      const id = `pdf2zh-option-${index}`;
      node.setAttribute('data-pdf2zh-option', id);
      const labelNode = node.querySelector('[data-testid*="label"], .font-medium');
      const text = norm(labelNode?.textContent || node.getAttribute('aria-label') || node.innerText);
      const role = node.getAttribute('role');
      return {
        id, label: text, role, visible: true,
        disabled: node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' || !!node.closest('[aria-disabled="true"]'),
        submenu: node.getAttribute('aria-haspopup') === 'menu',
        expanded: node.getAttribute('aria-expanded'), controls: node.getAttribute('aria-controls'),
        selected: node.getAttribute('aria-checked') === 'true' || node.getAttribute('aria-selected') === 'true' || ['checked','on'].includes(node.getAttribute('data-state')),
        testid: node.getAttribute('data-testid') || '',
        group: node.closest('[role="radiogroup"]')?.getAttribute('aria-label') || '',
      };
    });
  });
}

const includesWord = (label, words) => words.some(word => label.toLowerCase().includes(word));
async function clickMarked(page, row) {
  const locator = page.locator(`[data-pdf2zh-option="${row.id}"]`);
  await locator.click();
  await page.waitForTimeout(180);
}

async function openSection(page, kind) {
  await openPicker(page);
  let rows = await markMenuNodes(page);
  const advanced = rows.find(row => includesWord(row.label, WORDS.advanced));
  if (advanced && advanced.expanded !== 'true' && !rows.some(row => row.submenu && includesWord(row.label, WORDS[kind]))) {
    await clickMarked(page, advanced);
    rows = await markMenuNodes(page);
  }
  const section = rows.find(row => row.submenu && includesWord(row.label, WORDS[kind]) && !(kind === 'model' && includesWord(row.label, WORDS.effort)));
  if (section) {
    await clickMarked(page, section);
    rows = await markMenuNodes(page);
    // Radix keeps the parent menu visible; only the last visible leaf menu owns options.
    const leafIds = await page.evaluate(controls => {
      const menus = [...document.querySelectorAll('[role="menu"],[role="listbox"]')].filter(n => n.getBoundingClientRect().width > 0 && n.getBoundingClientRect().height > 0);
      const owned = controls ? document.getElementById(controls) : null;
      const leaf = owned && menus.includes(owned) ? owned : menus.at(-1);
      return leaf ? [...leaf.querySelectorAll('[data-pdf2zh-option]')].map(n => n.getAttribute('data-pdf2zh-option')) : [];
    }, section.controls);
    if (leafIds.length) rows = rows.filter(row => leafIds.includes(row.id));
  }
  const groupRows = rows.filter(row => includesWord(row.group, WORDS[kind]));
  if (groupRows.length) return filterOptions(groupRows);
  if (section) return filterOptions(rows.filter(row => ['radio', 'menuitemradio', 'option'].includes(row.role)));
  if (kind === 'effort') {
    // No effort section: expose only explicitly marked effort controls, never models.
    return filterOptions(rows.filter(row => /effort|thinking-time/.test(row.testid)));
  }
  // The old flat model menu identifies choices by radio/option roles or model test IDs.
  return filterOptions(rows.filter(row => ['radio', 'menuitemradio', 'option'].includes(row.role) || /model-switcher-(?!dropdown)/.test(row.testid))
    .filter(row => !includesWord(row.group, WORDS.effort) && !/effort/.test(row.testid)));
}

async function readSlider(page, requested) {
  const selector = '[data-testid="composer-model-picker-slider-simple-view"][data-active="true"] [data-model-reasoning-effort-slider]';
  const read = () => page.evaluate(selector => {
    const root = document.querySelector(selector);
    const control = root?.closest('[role="menuitem"]');
    const thumb = root?.querySelector('[role="slider"]');
    if (!control || !thumb || control.getBoundingClientRect().width === 0) return null;
    const described = (control.getAttribute('aria-describedby') || thumb.getAttribute('aria-describedby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').filter(Boolean).join(' ');
    const label = (thumb.getAttribute('aria-valuetext') || described).trim().split(/[,;，；。\n]/)[0].trim();
    return { value: Number(thumb.getAttribute('aria-valuenow')), min: Number(thumb.getAttribute('aria-valuemin')), max: Number(thumb.getAttribute('aria-valuemax')),
      label, disabled: control.getAttribute('aria-disabled') === 'true' || thumb.getAttribute('aria-disabled') === 'true' };
  }, selector);
  let current = await read();
  if (!current || !current.label) return null;
  if (![current.min, current.max, current.value].every(Number.isInteger) || current.min < 0 || current.max - current.min > 12) throw coded('PICKER_UNAVAILABLE', '网页推理强度滑块范围无法确认。');
  const original = current.value;
  const move = async target => {
    for (let step = 0; current.value !== target && step < 15; step++) {
      if (current.disabled) throw coded('OPTION_UNAVAILABLE', '当前账号的推理强度滑块不可用。');
      const previous = current.value;
      await page.locator(selector).locator('xpath=ancestor::*[@role="menuitem"][1]').press(target > current.value ? 'ArrowRight' : 'ArrowLeft');
      for (let poll = 0; poll < 15; poll++) {
        await page.waitForTimeout(100);
        current = await read();
        if (!current) throw coded('PICKER_UNAVAILABLE', '网页推理强度选择器已变化。');
        if (current.value !== previous) break;
      }
      if (current.value === previous) throw coded('SELECTION_UNVERIFIED', '无法确认网页推理强度滑块移动结果。');
    }
    if (current.value !== target) throw coded('SELECTION_UNVERIFIED', '无法确认网页推理强度。');
  };
  const values = [];
  let destination = original;
  try {
    for (let position = current.min; position <= current.max; position++) {
      await move(position);
      if (!current.label) throw coded('PICKER_UNAVAILABLE', '网页没有提供推理强度标签。');
      values.push({ value: current.label, label: current.label, position });
    }
    if (new Set(values.map(v => v.value)).size !== values.length) throw coded('PICKER_UNAVAILABLE', '网页推理强度标签无法区分，已暂停。');
    if (requested) {
      const wanted = values.find(value => value.value === requested);
      if (!wanted) throw coded('OPTION_UNAVAILABLE', `当前账号的网页没有可用推理强度：${requested}。`);
      destination = wanted.position;
    }
  } finally { await move(destination); }
  await closeMenus(page);
  return { options: values.map(({ value, label }) => ({ value, label })), selected: current.label };
}

export async function readChoices(page, kind, requested) {
  if (kind === 'effort') {
    await openPicker(page);
    const slider = await readSlider(page, requested);
    if (slider) return slider;
  }
  let options = await openSection(page, kind);
  if (requested) {
    const choice = options.find(item => item.value === requested);
    if (!choice) throw coded('OPTION_UNAVAILABLE', `当前账号的网页没有可用选项：${requested}。请刷新列表重新选择。`);
    if (!choice.selected) {
      await clickMarked(page, choice);
      options = await openSection(page, kind);
    }
    if (!options.find(item => item.value === requested)?.selected) {
      throw coded('SELECTION_UNVERIFIED', `无法确认网页已选择 ${requested}，已停止发送。`);
    }
  }
  const selected = options.find(item => item.selected)?.value || '';
  await closeMenus(page);
  return { options: options.map(({ value, label }) => ({ value, label })), selected };
}

export async function catalog(page, model, effort) {
  const evidence = await ensureChat(page);
  const models = await readChoices(page, 'model', model);
  const efforts = await readChoices(page, 'effort', effort);
  if (!models.options.length) throw coded('PICKER_UNAVAILABLE', '网页模型列表结构暂不受支持，没有猜测或提供无效模型。');
  const verified = await readChoices(page, 'model');
  if (!verified.selected || (efforts.options.length && !efforts.selected)) throw coded('SELECTION_UNVERIFIED', '网页没有明确标记当前模型或推理强度，已停止发送。');
  if (model && verified.selected !== model) throw coded('SELECTION_UNVERIFIED', '选择推理强度后网页的模型发生了变化，已停止发送。请刷新模型列表。');
  return { models: verified.options, efforts: efforts.options, model: verified.selected, effort: efforts.selected, mode: modeFromEvidence(evidence) };
}
