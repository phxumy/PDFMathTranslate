"""Check the history panel's frontend event contract without loading models."""

import ast
from pathlib import Path
import shutil
import subprocess

import gradio as gr
import pytest


@pytest.fixture
def scroll_event():
    source = Path(__file__).parents[1] / "pdf2zh" / "gui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "then"
        and any(
            kw.arg == "js"
            and isinstance(kw.value, ast.Constant)
            and "translation-history" in str(kw.value.value)
            for kw in node.keywords
        )
    )


def test_history_scroll_does_not_submit_a_backend_request(scroll_event):
    scroll_event.func.value = ast.Name(id="previous", ctx=ast.Load())
    expression = ast.fix_missing_locations(ast.Expression(body=scroll_event))
    with gr.Blocks(analytics_enabled=False) as demo:
        previous = gr.Button().click(lambda: None, api_name=False)
        eval(compile(expression, "history-scroll", "eval"), {"previous": previous})
    event = demo.config["dependencies"][-1]
    assert event["backend_fn"] is False
    assert event["inputs"] == event["outputs"] == []


@pytest.mark.parametrize("mode", ["legacy", "completed", "interrupted", "missing"])
def test_history_scroll_never_returns_browser_result_as_event_data(scroll_event, mode):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the frontend callback")
    callback = next(kw.value.value for kw in scroll_event.keywords if kw.arg == "js")
    script = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const mode = process.argv[1];
let calls = 0;
const document = {
  getElementById(id) {
    assert.equal(id, 'translation-history');
    if (mode === 'missing') return null;
    return { scrollIntoView(options) {
      calls++;
      assert.equal(options.block, 'start');
      assert.equal(options.behavior, 'smooth');
      return mode === 'legacy' ? undefined : Promise.resolve({interrupted: mode === 'interrupted'});
    }};
  }
};
const callback = new Function('document', 'return (' + readFileSync(0, 'utf8') + ')')(document);
const result = await callback();
assert.deepEqual(result, []);
assert.equal(calls, mode === 'missing' ? 0 : 1);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, mode],
        input=callback,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
