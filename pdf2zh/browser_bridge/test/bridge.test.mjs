import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { filterOptions, modeFromEvidence } from '../ui.mjs';
import { oracleArguments } from '../bridge.mjs';

const bridge = fileURLToPath(new URL('../bridge.mjs', import.meta.url));
test('only enabled, visible, distinct actual options are offered', () => {
  assert.deepEqual(filterOptions([
    { label: 'Model A' }, { label: 'Model A' }, { label: 'Model B', disabled: true },
    { label: 'Model C', visible: false }, { label: 'Advanced', submenu: true },
    { label: 'Upgrade for Model D' }, { label: '特殊模型' },
  ]).map(row => row.value), ['Model A', '特殊模型']);
});
test('ambiguous mode fails closed, Work cannot masquerade as Chat', () => {
  assert.equal(modeFromEvidence({ workSelected: true, chatSelected: true }), 'work');
  assert.equal(modeFromEvidence({ chatSelected: true }), 'chat');
  assert.equal(modeFromEvidence({ home: true, legacyPicker: true, composer: true }), 'legacy-chat');
  assert.equal(modeFromEvidence({ home: true, composer: true }), 'unknown');
  assert.equal(modeFromEvidence({ home: true, legacyPicker: true, composer: true, hasModeControls: true }), 'unknown');
});
test('Oracle invocation cannot inherit API mode, change model, archive chats or upload files', () => {
  const args = oracleArguments({ port: 9229, target: 'owned-tab', output: 'answer.txt', slug: 'pdf-request-unique' });
  for (const [flag, expected] of [['--engine', 'browser'], ['--browser-model-strategy', 'current'], ['--browser-archive', 'never'], ['--browser-attachments', 'never'], ['--browser-tab', 'owned-tab'], ['--prompt', '-']]) {
    assert.equal(args[args.indexOf(flag) + 1], expected);
  }
  assert.ok(args.includes('--no-browser-capture-provider-native'));
});
test('JSON protocol reports missing browser without launching or submitting', async () => {
  const state = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf2zh-bridge-test-'));
  try {
    const result = spawnSync(process.execPath, [bridge], { input: JSON.stringify({ op: 'status', state_dir: state }), encoding: 'utf8', timeout: 10000, windowsHide: true });
    const response = JSON.parse(result.stdout);
    assert.equal(response.ok, false);
    assert.equal(response.error_code, 'BROWSER_CLOSED');
    assert.equal(result.status, 0);
    assert.deepEqual(await fs.readdir(state), []);
  } finally { await fs.rm(state, { recursive: true, force: true }); }
});
test('recovery with no dispatch journal positively identifies no submission', async () => {
  const state = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf2zh-bridge-test-'));
  try {
    const result = spawnSync(process.execPath, [bridge], { input: JSON.stringify({ op: 'recover', state_dir: state, request_dir: path.join(state, 'request') }), encoding: 'utf8', timeout: 10000, windowsHide: true });
    const response = JSON.parse(result.stdout);
    assert.equal(response.error_code, 'NOT_SUBMITTED');
    assert.equal(response.not_submitted, true);
  } finally { await fs.rm(state, { recursive: true, force: true }); }
});
test('ambiguous dispatch is never marked as not submitted or automatically relaunched', async () => {
  const state = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf2zh-bridge-test-'));
  const requestDir = path.join(state, 'request');
  try {
    await fs.mkdir(requestDir);
    await fs.writeFile(path.join(requestDir, 'journal.json'), JSON.stringify({ dispatch_started: true, slug: 'pdf-fixture-test', output_file: path.join(requestDir, 'absent.txt') }));
    const result = spawnSync(process.execPath, [bridge], { input: JSON.stringify({ op: 'translate', state_dir: state, request_dir: requestDir }), encoding: 'utf8', timeout: 10000, windowsHide: true });
    const response = JSON.parse(result.stdout);
    assert.equal(response.error_code, 'BROWSER_CLOSED');
    assert.equal(response.not_submitted, false);
    assert.deepEqual(await fs.readdir(requestDir), ['journal.json']);
  } finally { await fs.rm(state, { recursive: true, force: true }); }
});
test('corrupt or structurally incomplete journals never authorize resubmission', async () => {
  const state = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf2zh-bridge-test-'));
  const requestDir = path.join(state, 'request');
  try {
    await fs.mkdir(requestDir);
    for (const contents of ['{broken', '{}', 'null', '[]', '{"dispatch_started":"false"}']) {
      await fs.writeFile(path.join(requestDir, 'journal.json'), contents);
      for (const op of ['translate', 'recover']) {
        const result = spawnSync(process.execPath, [bridge], { input: JSON.stringify({ op, state_dir: state, request_dir: requestDir }), encoding: 'utf8', timeout: 10000, windowsHide: true });
        const response = JSON.parse(result.stdout);
        assert.equal(response.error_code, 'JOURNAL_UNREADABLE', `${op}: ${contents}`);
        assert.equal(response.not_submitted, false, `${op}: ${contents}`);
      }
    }
  } finally { await fs.rm(state, { recursive: true, force: true }); }
});
