# ChatGPT browser companion

Local-only Node 24+ bridge used by `pdf2zh.chatgpt`. Install pinned dependencies
with `npm ci --omit=dev --ignore-scripts`; run `npm test` for the offline tests.
The two DOM fixture tests open **headless Chrome on generated HTML**, never a
ChatGPT account. No API keys or Codex executable are used.

The CLI reads one JSON object from stdin and returns one JSON object on stdout:

```json
{"op":"login","state_dir":"C:/Users/example/AppData/Local/PDFMathTranslate/ChatGPT"}
```

Supported operations: `login`, `status`, `catalog`, `select`, `translate`,
`recover`. `select` accepts `model` and optionally `effort`, using values from
the latest catalog. `translate` additionally requires absolute `request_dir`,
`prompt_file`, and `output_file` paths; it accepts `request_id` and
`timeout_seconds`. `recover` requires the same `request_dir`. Error responses
include `error_code`, `message`, `not_submitted`, and any known `session_id` /
`conversation_url`. `not_submitted` is true only before dispatch; damaged or
ambiguous journals fail closed.

## Browser and account lifecycle

Only `login` launches a visible Chrome window, with a dedicated persistent
`state_dir/chrome-profile`. Chrome's CDP listener binds to loopback with a
random port; the bridge verifies its `DevToolsActivePort` socket identity.
It never imports cookies from a normal Chrome profile and never controls the
user's ordinary browser or desktop app. Login tokens remain in Chrome's own
profile. Close/reopen normally preserves login; logout, expiry or account
security checks can still require login again.

The UI has a separate setup tab; each translation batch gets a fresh owned
tab. Only actual enabled options from the rendered picker are offered. Both
Advanced → Model/Effort menus and the accessible effort slider are supported.
Slider discovery reads the account's actual range and ARIA labels, then restores
its previous setting. Unknown controls produce an actionable error, not guessed
models. UI selectors will need maintenance when ChatGPT changes its website.

## Transport and recovery

Pinned Oracle `0.21.3` uses `--engine browser`, exact `--browser-tab` target,
`--browser-model-strategy current`, no attachments/research, no automatic archive,
and no provider-native conversation export. The CLI's `gpt-5.5` argument only
selects Oracle's ChatGPT provider; `current` preserves the actual verified web
model and effort. Browser execution never falls back to API mode. User Oracle
configuration is isolated in `state_dir/oracle` and API keys are removed from
the worker environment.

Before launch, `request_dir/journal.json` records an ambiguous-dispatch marker.
Oracle runs as a detached worker, so canceling a controller stops waiting without
resending or discarding the result. An active-request marker blocks a second
batch while that worker is still active. Request logs are local under request_dir.
On recovery the bridge reads the original output file or harvests the exact
owned conversation through Oracle, requiring the stored submitted-turn hash.
Browser closure never triggers an automatic fresh submission. A saved conversation
can be reopened after login; an unidentifiable original request remains paused.

Account history and account-wide model preferences may sync across ChatGPT
clients even though the browser profile is separate. Actual account quota
attribution and live translation still require a signed-in smoke test.
