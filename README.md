<div align="center">

English | [简体中文](docs/README_zh-CN.md) | [繁體中文](docs/README_zh-TW.md) | [日本語](docs/README_ja-JP.md) | [한국어](docs/README_ko-KR.md)

<img src="./docs/images/banner.png" width="320px"  alt="PDF2ZH"/>

<h2 id="title">PDFMathTranslate</h2>

<p>
  <!-- PyPI -->
  <a href="https://pypi.org/project/pdf2zh/">
    <img src="https://img.shields.io/pypi/v/pdf2zh"></a>
  <a href="https://pepy.tech/projects/pdf2zh">
    <img src="https://static.pepy.tech/badge/pdf2zh"></a>
  <a href="https://hub.docker.com/repository/docker/byaidu/pdf2zh">
    <img src="https://img.shields.io/docker/pulls/byaidu/pdf2zh"></a>
  <a href="https://hellogithub.com/repository/8ec2cfd3ef744762bf531232fa32bc47" target="_blank"><img src="https://api.hellogithub.com/v1/widgets/recommend.svg?rid=8ec2cfd3ef744762bf531232fa32bc47&claim_uid=JQ0yfeBNjaTuqDU&theme=small" alt="Featured｜HelloGitHub" /></a>
  <a href="https://gitcode.com/Byaidu/PDFMathTranslate/overview">
    <img src="https://gitcode.com/Byaidu/PDFMathTranslate/star/badge.svg"></a>
  <a href="https://huggingface.co/spaces/reycn/PDFMathTranslate-Docker">
    <img src="https://img.shields.io/badge/%F0%9F%A4%97-Online%20Demo-FF9E0D"></a>
  <a href="https://www.modelscope.cn/studios/AI-ModelScope/PDFMathTranslate">
    <img src="https://img.shields.io/badge/ModelScope-Demo-blue"></a>
  <a href="https://github.com/Byaidu/PDFMathTranslate/pulls">
    <img src="https://img.shields.io/badge/contributions-welcome-green"></a>
  <a href="https://t.me/+Z9_SgnxmsmA5NzBl">
    <img src="https://img.shields.io/badge/Telegram-2CA5E0?style=flat-squeare&logo=telegram&logoColor=white"></a>
  <!-- License -->
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/phxumy/PDFMathTranslate"></a>
</p>

<a href="https://trendshift.io/repositories/12424" target="_blank"><img src="https://trendshift.io/api/badge/repositories/12424" alt="Byaidu%2FPDFMathTranslate | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

> [!IMPORTANT]
>
> This repository contains an **unofficial Codex-enhanced v1.9.11 branch**.
> It adds a local Codex CLI translation backend and scientific-PDF layout fixes;
> it is not an official PDFMathTranslate or OpenAI release. See the
> [Chinese usage and attribution guide](./docs/CODEX_ENHANCED_FORK_zh-CN.md)
> and the [Windows desktop app guide](./docs/DESKTOP_APP_zh-CN.md).

## Download the Windows desktop app

The ready-to-use package is for **Windows 10/11 x64** and does not require
Python.

- **Recommended:** [download the Windows EXE installer with offline layout assets](https://github.com/phxumy/PDFMathTranslate/releases/latest/download/PDFMathTranslate-Codex-Setup-win64.exe)
- [Open the latest release page and SHA-256 checksums](https://github.com/phxumy/PDFMathTranslate/releases/latest)
- [Read the detailed Chinese desktop guide](./docs/DESKTOP_APP_zh-CN.md)

Quick start:

1. Download and run `PDFMathTranslate-Codex-Setup-win64.exe`. The installer creates a Start menu shortcut and an uninstaller.
2. Launch **PDFMathTranslate Codex Studio** and click **登录 / 切换账号** inside the app. Complete the official browser sign-in; the app updates the account and quota panel automatically.
3. Upload a PDF, choose languages and pages, then click **Translate**.
4. Under **关于与更新**, use **检查更新** and **下载并安装更新** for future releases. Your account, settings and translation cache are retained.

### New in v1.9.11-codex.6.1

- Fix the `Parameter interrupted is not a valid keyword argument` error when opening translation history and local file management in newer Edge / WebView2 versions. Automatic scrolling now runs entirely in the frontend without passing browser scroll results to a backend callback.
- Upgrade from `.6` through **关于与更新**, or install the new EXE over the existing application. Account settings, history, translation cache and saved PDFs are retained.

### New in v1.9.11-codex.5.4

- Bundle official stable Codex CLI 0.166.0 so the default executable supports current models such as `gpt-6-luna`. The CLI archive is pinned by SHA-256 in the release workflow.
- Select multiple PDFs and translate them in displayed order. Completed mono/bilingual downloads remain available if a later file fails. Stop halts the queue; retrying the same files/settings in the current session skips completed outputs unless Ignore cache is selected.
- Show structured model/access rejections directly instead of retrying them into an opaque `RetryError`. The model documentation now distinguishes official IDs, CLI versions and account availability.

### New in v1.9.11-codex.5.3

- Continue ChatGPT translations when the website virtualizes older turns, including the sixth batch and later. Recover an existing pending batch before sending newly planned work.
- Preserve the original failure from PDF Form objects. Fix `Text` font names being mistaken for TeX math fonts, which left some Nature abstracts and body paragraphs untranslated.
- Keep numbered institution addresses translatable and compact footnotes readable. Validate the reported paper's first page through actual ChatGPT translation, PDF rendering and a cached repeat without additional submissions.

### New in v1.9.11-codex.5.2

- Keep the first ChatGPT reply bound to its submitted message while the website assigns a permanent conversation URL. Persist the observed `local-chatgpt:` client ID so live replies remain readable after the address changes and across later batches.
- Recover a closed setup tab only when its task has no submission history. Preserve existing pending tasks across the observed English/Chinese effort labels, including `High` and `高`, rather than starting another chat.
- Report completed empty replies explicitly. Recovery does not regenerate or resend automatically; regenerate the response in its original chat only when you intend to request a new answer.

### New in v1.9.11-codex.5.1

- Fix ChatGPT reasoning-effort selection in background PDF tabs. Chrome could ignore the slider's keyboard input even though the browser connection and model list were available, causing a false synchronization error before translation started.
- Keep slider movement and menu dismissal inside the translation tab, with the existing position, label, model, and conversation checks.
- Support hotfix version tags in release builds and future update checks. **Users on codex.5 should download the new EXE manually**, because that version's updater does not recognize `.5.1` tags. Install over the existing application to retain settings and documents.

### New in v1.9.11-codex.5

- In-app Codex account status, browser/device-code login, and login cancellation.
- Adjustable batch concurrency (default 4), a **2× concurrency** option, and an independent **Codex Fast mode** switch. Double concurrency does not guarantee half the elapsed time; Fast mode can consume more account allowance and requires model/account support.
- Completed pages appear during standard translation, including cross-page paragraph handling. The full PDF replaces the page previews when final file generation completes. The experimental BabelDOC workflow still shows its PDF after completion.
- Remaining time uses a rolling range from actually completed pages, excludes the first cold-start sample and waits for sufficient observations. Font processing and final PDF writing are shown as a separate stage.
- A Windows EXE installer and explicit in-app GitHub updates, with size and SHA-256 verification before installation. Translation must finish or be cancelled before updating.

The release includes the official stable **Codex CLI 0.166.0 program** so a separate
Python or Codex installation is unnecessary. It does **not** include an account,
subscription, free quota, login credentials, API keys, user PDFs, or translation
caches. The desktop interface is bound to `127.0.0.1`, but text is sent to the
translation provider you select; Codex requests are sent to OpenAI according to
the account that signs in. See the [official OpenAI Codex CLI documentation](https://developers.openai.com/codex/cli)
for account and sign-in details.

### Codex fields and model IDs (checked October 28, 2026)

These field rules apply to desktop releases codex.5 through codex.7.3. The program passes an explicit model ID unchanged to `codex exec --model`; it has no model-name allowlist or automatic model substitution.

Starting with codex.7.2, the bundled CLI uses independent credentials under `%USERPROFILE%\.config\PDFMathTranslate\codex`, including when `CODEX_BIN` explicitly points to the bundled executable. Login, logout, account/model queries, and translation all use this scope; inherited OpenAI credentials are not reused. Upgrading requires signing in once, without copying or modifying personal Codex credentials. An external CLI selected by the user retains its existing `CODEX_HOME` and credential store. The account panel displays email, plan, quota remaining percentages and reset times from the official account RPCs when available. Failed queries show unavailable, and API-key billing balances are not supplied by this interface. Separate sign-ins do not create extra account quota.

Codex.7.3 upgrades the bundled CLI to 0.166.0 and adds **取消下载** during update downloads. Cancelling prevents installer launch, removes the unfinished download, and restores the retry controls. The existing independent login from codex.7.2 is retained.

| Field | Behavior |
| --- | --- |
| `CODEX_BIN` | Blank or `codex` prefers the bundled CLI before `CODEX_CLI_PATH`, `PATH`, and the desktop CLI. To use another installed version, enter its full executable path and check that exact binary with `--version`. |
| `CODEX_PROFILE` | Usually blank. A selected profile and configuration handling depend on the chosen CLI; the supported fast path ignores user configuration and rules. |
| `CODEX_MODEL` | Exact model ID, or blank to omit `--model`. Automatic selection is not pinned to a model or synchronized with the desktop model picker. |
| `CODEX_REASONING_EFFORT` | Defaults to `low`, including an empty field. It explicitly overrides the CLI/profile reasoning default. The backend accepts `none`, `low`, `medium`, `high`, `xhigh`, and `max`, but not `ultra`. |
| `CODEX_TIMEOUT` | Defaults to 120 seconds. Increasing it cannot resolve an HTTP 400 model rejection. |

| Model | Exact ID |
| --- | --- |
| GPT-6 Luna | `gpt-6-luna` |
| GPT-6.1 Sol | `gpt-6.1-sol` |
| GPT-6.1 Terra | `gpt-6.1-terra` |
| GPT-6 Sol | `gpt-6-sol` |
| GPT-6 Astra | `gpt-6-astra` |
| Previous-generation GPT-5.6 Sol / Terra / Luna | `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna` |

GPT-6 Luna is an official Codex model and supports reasoning through `max`. Use the full hyphenated ID, not `gpt6luna` or `luna`. See the [Luna model specification](https://developers.openai.com/api/docs/models/gpt-6-luna) and [Codex model availability](https://learn.chatgpt.com/docs/models). GPT-6 Astra does not support explicit `none`.

GPT-6.1 Terra can be entered as `gpt-6.1-terra`; it is also present in the local official CLI 0.163.0 model catalog checked on October 28. The [Terra API specification](https://developers.openai.com/api/docs/models/gpt-6.1-terra) lists `none`, `low`, `medium`, `high`, `xhigh`, and `max`. Codex picker choices can differ: use the selected CLI's returned model/effort metadata. This translation backend currently supports efforts through `max`, so an `ultra` option advertised by a Codex catalog is not yet accepted here.

Starting with codex.7, the model and effort fields are editable dropdowns. They load the selected CLI's catalog at startup or when selecting Codex; the refresh button reloads it. Choosing a catalog model updates the effort options, while custom model IDs remain editable. A failed refresh preserves current values. The official [`model/list` app-server RPC](https://learn.chatgpt.com/docs/app-server) supplies IDs, display names, supported efforts and defaults. Its catalog may be cached or bundled and does not prove inference access; see [account model catalogs](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

**codex.5.4 bundles official stable CLI 0.163.0; keep `CODEX_BIN=codex` to use it.** Earlier codex.5.3 installers bundle 0.145.0. The [official September 23 changelog](https://learn.chatgpt.com/docs/changelog) records the Sol/Luna catalog addition in CLI 0.156.1. A local comparison on October 18 found that 0.145.0 rejected `gpt-6-luna` + `max` with HTTP 400, while official stable 0.163.0 translated a short text successfully with the same account and settings. This identifies the older CLI as a compatibility issue in that environment, rather than proving Luna or Max unavailable in general. The compatibility comparison uses a short request; PDF and queue checks are described in the release notes.

On codex.5.3 or earlier, upgrade the application to codex.5.4 or point `CODEX_BIN` to a current official CLI. Updating the CLI on `PATH` alone does not override the bundled executable. Check account/workspace access if the current CLI still rejects the request. `RetryError` is a retry wrapper, not the underlying reason. Starting with codex.5.4, structured model/access rejections stop immediately with their original message.

For ChatGPT sign-in, the old `gpt-5.4` / `gpt-5.4-mini`, Spark, and `gpt-5.5` entries have retired; consult the linked Codex guide instead of relying on an old local catalog. API-key availability and billing follow separate rules. The **ChatGPT chat** service uses its web model picker, not these CLI IDs.

PDF scientific paper translation and bilingual comparison.

- 📊 Preserve formulas, charts, table of contents, and annotations _([preview](#preview))_.
- 🌐 Support [multiple languages](#language), and diverse [translation services](#services).
- 🤖 Provides [commandline tool](#usage), [interactive user interface](#gui), and [Docker](#docker)

For problems specific to this Codex-enhanced fork, use this fork's [GitHub Issues](https://github.com/phxumy/PDFMathTranslate/issues). For upstream PDFMathTranslate discussions, use the [upstream project](https://github.com/PDFMathTranslate/PDFMathTranslate).

For details on how to contribute, please consult the [Contribution Guide](https://github.com/Byaidu/PDFMathTranslate/wiki/Contribution-Guide---%E8%B4%A1%E7%8C%AE%E6%8C%87%E5%8D%97).

<h2 id="updates">Updates</h2>

- [May 9, 2025] pdf2zh 2.0 Preview Version [#586](https://github.com/Byaidu/PDFMathTranslate/issues/586): The Windows ZIP file and Docker image are now available.

> [!NOTE]
>
> 2.0 Moved to a new repository under the organization: [PDFMathTranslate/PDFMathTranslate-next](https://github.com/PDFMathTranslate/PDFMathTranslate-next)
> 
> Version 2.0 official release has been published.

- [Mar. 3, 2025] Experimental support for the new backend [BabelDOC](https://github.com/funstory-ai/BabelDOC) WebUI added as an experimental option (by [@awwaawwa](https://github.com/awwaawwa))
- [Feb. 22 2025] Better release CI and well-packaged windows-amd64 exe (by [@awwaawwa](https://github.com/awwaawwa))
- [Dec. 24 2024] The translator now supports local models on [Xinference](https://github.com/xorbitsai/inference) _(by [@imClumsyPanda](https://github.com/imClumsyPanda))_
- [Dec. 19 2024] Non-PDF/A documents are now supported using `-cp` _(by [@reycn](https://github.com/reycn))_
- [Dec. 13 2024] Additional support for backend by _(by [@YadominJinta](https://github.com/YadominJinta))_
- [Dec. 10 2024] The translator now supports OpenAI models on Azure _(by [@yidasanqian](https://github.com/yidasanqian))_

<h2 id="preview">Preview</h2>

<div align="center">
<img src="./docs/images/preview.gif" width="80%"/>
</div>

<h2 id="demo">Online Service 🌟</h2>

You can try our application out using either of the following demos:

- [Public free service](https://pdf2zh.com/) online without installation _(recommended)_.
- [Immersive Translate - BabelDOC](https://app.immersivetranslate.com/babel-doc/) 1000 free pages per month. _(recommended)_
- [Demo hosted on HuggingFace](https://huggingface.co/spaces/reycn/PDFMathTranslate-Docker)
- [Demo hosted on ModelScope](https://www.modelscope.cn/studios/AI-ModelScope/PDFMathTranslate) without installation.

Note that the computing resources of the demo are limited, so please avoid abusing them.

<h2 id="install">Installation and Usage</h2>

### Methods

For different use cases, we provide distinct methods to use our program:

<details open>
  <summary>1. UV install</summary>

1. Python installed (3.10 <= version <= 3.12)

2. Install our package:

   ```bash
   pip install uv
   uv tool install --python 3.12 pdf2zh
   ```

3. Execute translation, files generated in [current working directory](https://chatgpt.com/share/6745ed36-9acc-800e-8a90-59204bd13444):

   ```bash
   pdf2zh document.pdf
   ```

</details>

<details>
  <summary>2. Windows exe</summary>

1. Download `PDFMathTranslate-Codex-win64-with-assets.zip` from [this fork's latest release](https://github.com/phxumy/PDFMathTranslate/releases/latest).

2. Extract the complete archive. To use Codex, double-click `Login-Codex.cmd` once and complete sign-in.

3. Double-click `PDFMathTranslate-Codex.exe`.

The package includes the pinned official Codex CLI program and BabelDOC offline
assets. `pdf2zh.exe` is also included for the classic browser UI and command-line
use. No account or login credential is bundled.

</details>

<details>
  <summary>3. Graphic user interface</summary>

1. Python installed (3.10 <= version <= 3.12)

2. Install our package:

```bash
pip install pdf2zh
```

3. Start using in browser:

   ```bash
   pdf2zh -i
   ```

4. If your browser has not been started automatically, goto

   ```bash
   http://localhost:7860/
   ```

   <img src="./docs/images/gui.gif" width="500"/>

See [documentation for GUI](./docs/README_GUI.md) for more details.

</details>

<details>
  <summary>4. Docker</summary>

1. Pull and run:

   ```bash
   docker pull byaidu/pdf2zh
   docker run -d -p 7860:7860 byaidu/pdf2zh
   ```

2. Open in browser:

   ```
   http://localhost:7860/
   ```

For docker deployment on cloud service:

<div>
<a href="https://www.heroku.com/deploy?template=https://github.com/Byaidu/PDFMathTranslate">
  <img src="https://www.herokucdn.com/deploy/button.svg" alt="Deploy" height="26"></a>
<a href="https://render.com/deploy">
  <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Koyeb" height="26"></a>
<a href="https://zeabur.com/templates/5FQIGX?referralCode=reycn">
  <img src="https://zeabur.com/button.svg" alt="Deploy on Zeabur" height="26"></a>
<a href="https://template.sealos.io/deploy?templateName=pdf2zh">
  <img src="https://sealos.io/Deploy-on-Sealos.svg" alt="Deploy on Sealos" height="26"></a>
<a href="https://app.koyeb.com/deploy?type=git&builder=buildpack&repository=github.com/Byaidu/PDFMathTranslate&branch=main&name=pdf-math-translate">
  <img src="https://www.koyeb.com/static/images/deploy/button.svg" alt="Deploy to Koyeb" height="26"></a>
</div>

</details>

<details>
  <summary>5. Zotero Plugin</summary>


See [Zotero PDF2zh](https://github.com/guaguastandup/zotero-pdf2zh) for more details.

</details>

<details>
  <summary>6. Commandline</summary>

1. Python installed (3.10 <= version <= 3.12)
2. Install our package:

   ```bash
   pip install pdf2zh
   ```

3. Execute translation, files generated in [current working directory](https://chatgpt.com/share/6745ed36-9acc-800e-8a90-59204bd13444):

   ```bash
   pdf2zh document.pdf
   ```

</details>

> [!TIP]
>
> - If you're using Windows and cannot open the file after downloading, please install [vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe) and try again.
>
> - If you cannot access Docker Hub, please try the image on [GitHub Container Registry](https://github.com/Byaidu/PDFMathTranslate/pkgs/container/pdfmathtranslate).
> ```bash
> docker pull ghcr.io/byaidu/pdfmathtranslate
> docker run -d -p 7860:7860 ghcr.io/byaidu/pdfmathtranslate
> ```

### Unable to install?

The present program needs an AI model(`wybxc/DocLayout-YOLO-DocStructBench-onnx`) before working and some users are not able to download due to network issues. If you have a problem with downloading this model, we provide a workaround using the following environment variable:

```shell
set HF_ENDPOINT=https://hf-mirror.com
```

For PowerShell user:

```shell
$env:HF_ENDPOINT = https://hf-mirror.com
```

If the solution does not work to you / you encountered other issues, please refer to [frequently asked questions](https://github.com/Byaidu/PDFMathTranslate/wiki#-faq--%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98).

<h2 id="usage">Advanced Options</h2>

Execute the translation command in the command line to generate the translated document `example-mono.pdf` and the bilingual document `example-dual.pdf` in the current working directory. Use Google as the default translation service. More support translation services can find [HERE](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#services).

<img src="./docs/images/cmd.explained.png" width="580px"  alt="cmd"/>

In the following table, we list all advanced options for reference:

| Option                | Function                                                                                                      | Example                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| files                 | Local files                                                                                                   | `pdf2zh ~/local.pdf`                           |
| links                 | Online files                                                                                                  | `pdf2zh http://arxiv.org/paper.pdf`            |
| `-i`                  | [Enter GUI](#gui)                                                                                             | `pdf2zh -i`                                    |
| `-p`                  | [Partial document translation](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#partial) | `pdf2zh example.pdf -p 1`                      |
| `-li`                 | [Source language](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#languages)            | `pdf2zh example.pdf -li en`                    |
| `-lo`                 | [Target language](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#languages)            | `pdf2zh example.pdf -lo zh`                    |
| `-s`                  | [Translation service](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#services)         | `pdf2zh example.pdf -s deepl`                  |
| `-t`                  | [Multi-threads](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#threads)                | `pdf2zh example.pdf -t 1`                      |
| `-o`                  | Output dir                                                                                                    | `pdf2zh example.pdf -o output`                 |
| `-f`, `-c`            | [Exceptions](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#exceptions)                | `pdf2zh example.pdf -f "(MS.*)"`               |
| `-cp`                 | Compatibility Mode                                                                                            | `pdf2zh example.pdf --compatible`              |
| `--skip-subset-fonts` | [Skip font subset](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#font-subset)         | `pdf2zh example.pdf --skip-subset-fonts`       |
| `--ignore-cache`      | [Ignore translate cache](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#cache)         | `pdf2zh example.pdf --ignore-cache`            |
| `--share`             | Public link                                                                                                   | `pdf2zh -i --share`                            |
| `--authorized`        | [Authorization](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#auth)                   | `pdf2zh -i --authorized users.txt [auth.html]` |
| `--prompt`            | [Custom Prompt](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#prompt)                 | `pdf2zh --prompt [prompt.txt]`                 |
| `--onnx`              | [Use Custom DocLayout-YOLO ONNX model]                                                                        | `pdf2zh --onnx [onnx/model/path]`              |
| `--serverport`        | [Use Custom WebUI port]                                                                                       | `pdf2zh --serverport 7860`                     |
| `--dir`               | [batch translate]                                                                                             | `pdf2zh --dir /path/to/translate/`             |
| `--config`            | [configuration file](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#cofig)             | `pdf2zh --config /path/to/config/config.json`  |
| `--serverport`        | [custom gradio server port]                                                                                   | `pdf2zh --serverport 7860`                     |
| `--babeldoc`          | Use Experimental backend [BabelDOC](https://funstory-ai.github.io/BabelDOC/) to translate                     | `pdf2zh --babeldoc` -s openai example.pdf      |
| `--mcp`               | Enable MCP STDIO mode                                                                                         | `pdf2zh --mcp`                                 |
| `--sse`               | Enable MCP SSE mode                                                                                           | `pdf2zh --mcp --sse`                           |

For detailed explanations, please refer to our document about [Advanced Usage](./docs/ADVANCED.md) for a full list of each option.

<h2 id="downstream">Secondary Development (APIs)</h2>

For downstream applications, please refer to our document about [API Details](./docs/APIS.md) for further information about:

- [Python API](./docs/APIS.md#api-python), how to use the program in other Python programs
- [HTTP API](./docs/APIS.md#api-http), how to communicate with a server with the program installed

<h2 id="todo">TODOs</h2>

- [ ] Parse layout with DocLayNet based models, [PaddleX](https://github.com/PaddlePaddle/PaddleX/blob/17cc27ac3842e7880ca4aad92358d3ef8555429a/paddlex/repo_apis/PaddleDetection_api/object_det/official_categories.py#L81), [PaperMage](https://github.com/allenai/papermage/blob/9cd4bb48cbedab45d0f7a455711438f1632abebe/README.md?plain=1#L102), [SAM2](https://github.com/facebookresearch/sam2)

- [ ] Fix page rotation, table of contents, format of lists

- [ ] Fix pixel formula in old papers

- [ ] Async retry except KeyboardInterrupt

- [ ] Knuth–Plass algorithm for western languages

- [ ] Support non-PDF/A files

- [ ] Plugins of [Zotero](https://github.com/zotero/zotero) and [Obsidian](https://github.com/obsidianmd/obsidian-releases)

<h2 id="acknowledgement">Acknowledgements</h2>

- [Immersive Translation](https://immersivetranslate.com) sponsors monthly Pro membership redemption codes for active contributors to this project, see details at: [CONTRIBUTOR_REWARD.md](https://github.com/funstory-ai/BabelDOC/blob/main/docs/CONTRIBUTOR_REWARD.md)

- New backend: [BabelDOC](https://github.com/funstory-ai/BabelDOC)

- Document merging: [PyMuPDF](https://github.com/pymupdf/PyMuPDF)

- Document parsing: [Pdfminer.six](https://github.com/pdfminer/pdfminer.six)

- Document extraction: [MinerU](https://github.com/opendatalab/MinerU)

- Document Preview: [Gradio PDF](https://github.com/freddyaboulton/gradio-pdf)

- Multi-threaded translation: [MathTranslate](https://github.com/SUSYUSTC/MathTranslate)

- Layout parsing: [DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO)

- Document standard: [PDF Explained](https://zxyle.github.io/PDF-Explained/), [PDF Cheat Sheets](https://pdfa.org/resource/pdf-cheat-sheets/)

- Multilingual Font: [Go Noto Universal](https://github.com/satbyy/go-noto-universal)

<h2 id="contrib">Contributors</h2>

<a href="https://github.com/Byaidu/PDFMathTranslate/graphs/contributors">
  <img src="https://opencollective.com/PDFMathTranslate/contributors.svg?width=890&button=false" />
</a>

![Alt](https://repobeats.axiom.co/api/embed/dfa7583da5332a11468d686fbd29b92320a6a869.svg "Repobeats analytics image")

<h2 id="star_hist">Star History</h2>

<a href="https://star-history.com/#Byaidu/PDFMathTranslate&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Byaidu/PDFMathTranslate&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Byaidu/PDFMathTranslate&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Byaidu/PDFMathTranslate&type=Date"/>
 </picture>
</a>
