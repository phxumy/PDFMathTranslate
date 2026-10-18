import asyncio
import cgi
import os
import shutil
import uuid
import time
from dataclasses import replace
from queue import Empty, Queue
from threading import Event, Thread
from asyncio import CancelledError
from pathlib import Path
import typing as T

import gradio as gr
import requests
import tqdm
from gradio_pdf import PDF
from string import Template
import logging

from pdf2zh import __version__
from pdf2zh.high_level import translate
from pdf2zh.doclayout import ModelInstance
from pdf2zh.config import ConfigManager
from pdf2zh.translation_queue import queue_files, queue_signature, queue_summary
from pdf2zh.app_update import APP_VERSION, REPOSITORY_URL, update_manager
from pdf2zh.codex_account import (
    get_codex_status,
    start_codex_login,
    poll_codex_login,
    cancel_codex_login,
)
from pdf2zh.chatgpt import (
    ChatGPTTranslator,
    cancel_chatgpt_wait,
    get_chatgpt_status,
    login_chatgpt,
    refresh_chatgpt_options,
)
from pdf2zh.translator import (
    AnythingLLMTranslator,
    AzureOpenAITranslator,
    AzureTranslator,
    BaseTranslator,
    BingTranslator,
    CodexTranslator,
    DeepLTranslator,
    DeepLXTranslator,
    DifyTranslator,
    ArgosTranslator,
    GeminiTranslator,
    GoogleTranslator,
    ModelScopeTranslator,
    OllamaTranslator,
    OpenAITranslator,
    SiliconTranslator,
    TencentTranslator,
    XinferenceTranslator,
    ZhipuTranslator,
    GrokTranslator,
    GroqTranslator,
    DeepseekTranslator,
    OpenAIlikedTranslator,
    QwenMtTranslator,
    TranslationAbortedError,
)
from babeldoc.docvision.doclayout import OnnxModel
from babeldoc import __version__ as babeldoc_version

logger = logging.getLogger(__name__)

BABELDOC_MODEL = OnnxModel.load_available()
# The following variables associate strings with translators
service_map: dict[str, BaseTranslator] = {
    "Google": GoogleTranslator,
    "Bing": BingTranslator,
    "DeepL": DeepLTranslator,
    "DeepLX": DeepLXTranslator,
    "Ollama": OllamaTranslator,
    "Xinference": XinferenceTranslator,
    "AzureOpenAI": AzureOpenAITranslator,
    "OpenAI": OpenAITranslator,
    "Zhipu": ZhipuTranslator,
    "ModelScope": ModelScopeTranslator,
    "Silicon": SiliconTranslator,
    "Gemini": GeminiTranslator,
    "Azure": AzureTranslator,
    "Tencent": TencentTranslator,
    "Dify": DifyTranslator,
    "AnythingLLM": AnythingLLMTranslator,
    "Argos Translate": ArgosTranslator,
    "Grok": GrokTranslator,
    "Groq": GroqTranslator,
    "DeepSeek": DeepseekTranslator,
    "OpenAI-liked": OpenAIlikedTranslator,
    "Codex": CodexTranslator,
    "ChatGPT 聊天": ChatGPTTranslator,
    "Ali Qwen-Translation": QwenMtTranslator,
}

# The following variables associate strings with specific languages
lang_map = {
    "Simplified Chinese": "zh",
    "Traditional Chinese": "zh-TW",
    "English": "en",
    "French": "fr",
    "German": "de",
    "Japanese": "ja",
    "Korean": "ko",
    "Russian": "ru",
    "Spanish": "es",
    "Italian": "it",
}

# The following variable associate strings with page ranges
page_map = {
    "All": None,
    "First": [0],
    "First 5 pages": list(range(0, 5)),
    "Others": None,
}

# Check if this is a public demo, which has resource limits
flag_demo = False

# Limit resources
if ConfigManager.get("PDF2ZH_DEMO"):
    flag_demo = True
    service_map = {
        "Google": GoogleTranslator,
    }
    page_map = {
        "First": [0],
        "First 20 pages": list(range(0, 20)),
    }
    client_key = ConfigManager.get("PDF2ZH_CLIENT_KEY")
    server_key = ConfigManager.get("PDF2ZH_SERVER_KEY")


MAX_ENV_FIELDS = max(len(translator.envs) for translator in service_map.values())


# Limit Enabled Services
enabled_services: T.Optional[T.List[str]] = ConfigManager.get("ENABLED_SERVICES")
if isinstance(enabled_services, list):
    default_services = ["Google", "Bing"]
    enabled_services_names = [str(_).lower().strip() for _ in enabled_services]
    enabled_services = [
        k
        for k in service_map.keys()
        if str(k).lower().strip() in enabled_services_names
    ]
    if len(enabled_services) == 0:
        raise RuntimeError("No services available.")
    enabled_services = default_services + enabled_services
else:
    enabled_services = list(service_map.keys())


# Configure about Gradio show keys
hidden_gradio_details: bool = bool(ConfigManager.get("HIDDEN_GRADIO_DETAILS"))


# Public demo control
def verify_recaptcha(response):
    """
    This function verifies the reCAPTCHA response.
    """
    recaptcha_url = "https://www.google.com/recaptcha/api/siteverify"
    data = {"secret": server_key, "response": response}
    result = requests.post(recaptcha_url, data=data).json()
    return result.get("success")


def download_with_limit(url: str, save_path: str, size_limit: int) -> str:
    """
    This function downloads a file from a URL and saves it to a specified path.

    Inputs:
        - url: The URL to download the file from
        - save_path: The path to save the file to
        - size_limit: The maximum size of the file to download

    Returns:
        - The path of the downloaded file
    """
    chunk_size = 1024
    total_size = 0
    with requests.get(url, stream=True, timeout=10) as response:
        response.raise_for_status()
        content = response.headers.get("Content-Disposition")
        try:  # filename from header
            _, params = cgi.parse_header(content)
            filename = params["filename"]
        except Exception:  # filename from url
            filename = os.path.basename(url)
        filename = os.path.splitext(os.path.basename(filename))[0] + ".pdf"
        with open(save_path / filename, "wb") as file:
            for chunk in response.iter_content(chunk_size=chunk_size):
                total_size += len(chunk)
                if size_limit and total_size > size_limit:
                    raise gr.Error("Exceeds file size limit")
                file.write(chunk)
    return save_path / filename


def _chatgpt_control_updates(result):
    """Expose only model/effort choices observed in the signed-in browser."""
    updates = []
    for options_key, selection_key in (("models", "model"), ("efforts", "effort")):
        choices = [
            (option["label"], option["value"])
            for option in result.get(options_key, [])
            if option.get("label") and option.get("value")
        ]
        available_values = {value for _, value in choices}
        selected = result.get(selection_key)
        updates.append(
            gr.update(
                choices=choices,
                value=selected if selected in available_values else None,
                interactive=bool(choices),
            )
        )
    return (
        result.get("message") or result.get("status") or "请打开登录窗口。",
        *updates,
    )


def _chatgpt_action(action, *args):
    try:
        result = action(*args)
    except Exception as exc:
        logger.warning("ChatGPT browser connection: %s", exc)
        result = {"ok": False, "message": str(exc)}
    return _chatgpt_control_updates(result)


def open_chatgpt_login():
    return _chatgpt_action(login_chatgpt)


def refresh_chatgpt_controls(model=None, effort=None):
    return _chatgpt_action(refresh_chatgpt_options, model, effort)


def select_chatgpt_model(model):
    # Effort choices belong to the newly selected model, not the previous one.
    return refresh_chatgpt_controls(model, None)


def stop_translate_file(state: dict) -> None:
    """
    This function stops the translation process.

    Inputs:
        - state: The state of the translation process

    Returns:- None
    """
    queue_event = cancellation_event_map.get(state.get("queue_id"))
    if queue_event is not None:
        queue_event.set()
    session_id = state["session_id"]
    if session_id is None:
        return
    if session_id in cancellation_event_map:
        logger.info(f"Stopping translation for session {session_id}")
        cancellation_event_map[session_id].set()
        if state.get("service") == ChatGPTTranslator.name:
            cancel_chatgpt_wait()


def translate_file(
    file_type,
    file_input,
    link_input,
    service,
    lang_from,
    lang_to,
    page_range,
    page_input,
    prompt,
    threads,
    skip_subset_fonts,
    ignore_cache,
    vfont,
    use_babeldoc,
    recaptcha_response,
    state,
    chatgpt_model=None,
    chatgpt_effort=None,
    progress=gr.Progress(),
    *envs,
    progress_callback=None,
    preview_callback=None,
    _session_id=None,
    _cancellation_event=None,
):
    """
    This function translates a PDF file from one language to another.

    Inputs:
        - file_type: The type of file to translate
        - file_input: The file to translate
        - link_input: The link to the file to translate
        - service: The translation service to use
        - lang_from: The language to translate from
        - lang_to: The language to translate to
        - page_range: The range of pages to translate
        - page_input: The input for the page range
        - prompt: The custom prompt for the llm
        - threads: The number of threads to use
        - recaptcha_response: The reCAPTCHA response
        - state: The state of the translation process
        - progress: The progress bar
        - envs: The environment variables

    Returns:
        - The translated file
        - The translated file
        - The translated file
        - The progress bar
        - The progress bar
        - The progress bar
    """
    translator = service_map[service]
    is_chatgpt = translator.name == ChatGPTTranslator.name
    if is_chatgpt:
        if use_babeldoc:
            raise gr.Error("ChatGPT 聊天目前支持标准排版流程，请关闭 Use BabelDOC。")
        if not chatgpt_model:
            raise gr.Error(
                "请先登录 ChatGPT 并刷新模型，选择网页实际提供的模型后再翻译。"
            )
    session_id = _session_id or uuid.uuid4()
    state["session_id"] = session_id
    state["service"] = translator.name
    # Translate PDF content using selected service.
    if flag_demo and not verify_recaptcha(recaptcha_response):
        raise gr.Error("reCAPTCHA fail")

    progress(0, desc="Starting translation...")

    output = Path("pdf2zh_files") / str(session_id)
    output.mkdir(parents=True, exist_ok=True)

    if file_type == "File":
        if not file_input:
            raise gr.Error("No input")
        file_path = shutil.copy(file_input, output)
    else:
        if not link_input:
            raise gr.Error("No input")
        file_path = download_with_limit(
            link_input,
            output,
            5 * 1024 * 1024 if flag_demo else None,
        )

    if _cancellation_event is not None and _cancellation_event.is_set():
        raise CancelledError()

    filename = os.path.splitext(os.path.basename(file_path))[0]
    file_raw = output / f"{filename}.pdf"
    file_mono = output / f"{filename}-mono.pdf"
    file_dual = output / f"{filename}-dual.pdf"

    if page_range != "Others":
        selected_page = page_map[page_range]
    else:
        selected_page = []
        for p in page_input.split(","):
            if "-" in p:
                start, end = p.split("-")
                selected_page.extend(range(int(start) - 1, int(end)))
            else:
                selected_page.append(int(p) - 1)
    lang_from = lang_map[lang_from]
    lang_to = lang_map[lang_to]

    _envs = {}
    for i, env in enumerate(translator.envs.items()):
        _envs[env[0]] = envs[i] if i < len(envs) else env[1]
    if is_chatgpt:
        _envs["CHATGPT_MODEL"] = chatgpt_model
        _envs["CHATGPT_EFFORT"] = chatgpt_effort or ""
        ConfigManager.set_translator_by_name(translator.name, _envs)
    for k, v in _envs.items():
        if str(k).upper().endswith("API_KEY") and str(v) == "***":
            # Load Real API_KEYs from local configure file
            real_keys: str = ConfigManager.get_env_by_translatername(
                translator, k, None
            )
            _envs[k] = real_keys

    def progress_bar(t: tqdm.tqdm):
        desc = getattr(t, "desc", "Translating...")
        if desc == "":
            desc = "Translating..."
        progress(t.n / max(t.total or 1, 1), desc=desc)

    try:
        threads = int(threads)
    except ValueError:
        threads = 1
    if is_chatgpt:
        threads = 1

    cancellation_event_map[session_id] = (
        _cancellation_event if _cancellation_event is not None else asyncio.Event()
    )

    param = {
        "files": [str(file_raw)],
        "pages": selected_page,
        "lang_in": lang_from,
        "lang_out": lang_to,
        "service": f"{translator.name}",
        "output": output,
        "thread": int(threads),
        "callback": progress_bar,
        "cancellation_event": cancellation_event_map[session_id],
        "envs": _envs,
        "prompt": Template(prompt) if prompt else None,
        "skip_subset_fonts": skip_subset_fonts,
        "ignore_cache": ignore_cache,
        "vfont": vfont,  # 添加自定义公式字体正则表达式
        "model": ModelInstance.value,
    }

    if not use_babeldoc:
        param["progress_callback"] = progress_callback
        param["preview_callback"] = preview_callback

    try:
        if use_babeldoc:
            return babeldoc_translate_file(**param)
        translate(**param)
    except CancelledError:
        raise gr.Error("Translation cancelled")
    except TranslationAbortedError as exc:
        raise gr.Error(str(exc)) from exc
    finally:
        cancellation_event_map.pop(session_id, None)

    if not file_mono.exists() or not file_dual.exists():
        raise gr.Error("No output")

    progress(1.0, desc="Translation complete!")

    return (
        str(file_mono),
        str(file_mono),
        str(file_dual),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
    )


def _codex_settings(env_values, fast_mode, speed, parallel):
    values = list(env_values)
    keys = list(CodexTranslator.envs)
    while len(values) < len(keys):
        values.append(CodexTranslator.envs[keys[len(values)]])
    if (
        not values[keys.index("CODEX_MODEL")]
        and not values[keys.index("CODEX_PROFILE")]
        and str(values[keys.index("CODEX_REASONING_EFFORT")] or "").strip()
        in {"", "none"}
    ):
        # Older releases saved "none", which today's automatic model rejects.
        values[keys.index("CODEX_REASONING_EFFORT")] = "low"
    workers = max(1, min(8, int(parallel or 4)))
    if speed == "double":
        workers *= 2
    for key, value in {
        "CODEX_FAST_MODE": "true" if fast_mode else "false",
        "CODEX_MAX_CONCURRENCY": str(workers),
    }.items():
        values[keys.index(key)] = value
    return values, workers


def _duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes} 分 {seconds:02d} 秒"


def _translation_status(event):
    phases = {
        "preparing": "正在准备文档与模型",
        "analyzing": "正在识别版面",
        "translating": "正在翻译与排版",
        "finalizing": "正在生成完整 PDF",
        "complete": "翻译完成",
    }
    status = phases.get(event.phase, event.phase)
    text = f"**{status}** · 已完成 {event.completed_pages}/{event.total_pages} 页"
    text += f" · 已用 {_duration(event.elapsed_seconds)}"
    if event.phase == "complete":
        return text
    if event.phase == "finalizing":
        return text + " · 正在合并、处理字体与保存文件"
    if event.eta_low_seconds is None or event.eta_high_seconds is None:
        return text + " · 剩余时间估算中（等待足够的完成样本）"
    return text + (
        f" · 翻译预计还需 {_duration(event.eta_low_seconds)}"
        f"–{_duration(event.eta_high_seconds)}，另需最终文件处理时间"
    )


def translate_file_stream(
    file_type,
    file_input,
    link_input,
    service,
    lang_from,
    lang_to,
    page_range,
    page_input,
    prompt,
    threads,
    skip_subset_fonts,
    ignore_cache,
    vfont,
    use_babeldoc,
    recaptcha_response,
    state,
    chatgpt_model,
    chatgpt_effort,
    codex_fast,
    codex_speed,
    codex_parallel,
    progress=gr.Progress(),
    *envs,
):
    """Keep the UI responsive while publishing completed page images."""
    queue_event = cancellation_event_map.get(state.get("queue_id"))
    if queue_event is not None and queue_event.is_set():
        raise gr.Error("队列已停止。")
    update_manager.begin_translation()
    events = Queue()
    worker = None
    session_id = uuid.uuid4()
    cancellation_event = Event()
    cancellation_event_map[session_id] = cancellation_event
    state["session_id"] = session_id
    state["service"] = service_map[service].name
    if queue_event is not None and queue_event.is_set():
        cancellation_event.set()
    finished = False
    preview_dir = Path("pdf2zh_files") / ("preview-" + uuid.uuid4().hex)
    pages = {}
    last_preview_at = 0.0
    started = time.monotonic()
    last_progress = None
    last_progress_at = started
    latest_status = "**正在准备翻译** · 剩余时间估算中"
    if use_babeldoc:
        latest_status += " · BabelDOC 模式将在完成后显示完整 PDF"
    try:
        if service_map[service].name == "codex":
            envs, threads = _codex_settings(
                envs, codex_fast, codex_speed, codex_parallel
            )
            ConfigManager.set("PDF2ZH_CODEX_FAST", bool(codex_fast))
            ConfigManager.set("PDF2ZH_CODEX_SPEED", codex_speed)
            ConfigManager.set("PDF2ZH_CODEX_PARALLEL", int(codex_parallel))

        def save_preview(event):
            if cancellation_event.is_set():
                raise CancelledError()
            preview_dir.mkdir(parents=True, exist_ok=True)
            path = preview_dir / f"page-{event.page_number:05d}.png"
            path.write_bytes(event.png_bytes)
            events.put(("preview", (event.page_number, str(path))))

        def run():
            try:
                result = translate_file(
                    file_type,
                    file_input,
                    link_input,
                    service,
                    lang_from,
                    lang_to,
                    page_range,
                    page_input,
                    prompt,
                    threads,
                    skip_subset_fonts,
                    ignore_cache,
                    vfont,
                    use_babeldoc,
                    recaptcha_response,
                    state,
                    chatgpt_model,
                    chatgpt_effort,
                    lambda *a, **kw: None,
                    *envs,
                    progress_callback=lambda event: events.put(("progress", event)),
                    preview_callback=save_preview,
                    _session_id=session_id,
                    _cancellation_event=cancellation_event,
                )
                events.put(("result", result))
            except BaseException as exc:
                events.put(("error", exc))
            finally:
                cancellation_event_map.pop(session_id, None)

        worker = Thread(target=run, name="pdf2zh-ui-translation", daemon=True)
        worker.start()
        yield (
            gr.update(value=None, visible=False),
            gr.update(value=file_input if file_type == "File" else None, visible=True),
            gr.update(value=None, visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(value=[], visible=False),
            latest_status,
            state,
        )
        while True:
            try:
                kind, value = events.get(timeout=1.0)
            except Empty:
                if last_progress is not None:
                    idle = max(0.0, time.monotonic() - last_progress_at)
                    low, high = (
                        last_progress.eta_low_seconds,
                        last_progress.eta_high_seconds,
                    )
                    if high is not None and high > idle:
                        low, high = max(0.0, (low or 0.0) - idle), high - idle
                    else:
                        low = high = None
                    heartbeat = replace(
                        last_progress,
                        elapsed_seconds=last_progress.elapsed_seconds + idle,
                        eta_low_seconds=low,
                        eta_high_seconds=high,
                    )
                    message = _translation_status(heartbeat)
                else:
                    message = (
                        latest_status
                        + f" · 已用 {_duration(time.monotonic() - started)}"
                    )
                yield (*[gr.update() for _ in range(7)], message, state)
                continue
            if kind == "progress":
                last_progress, last_progress_at = value, time.monotonic()
                latest_status = _translation_status(value)
                yield (*[gr.update() for _ in range(7)], latest_status, state)
            elif kind == "preview":
                number, path = value
                pages[number] = (path, f"原文第 {number} 页 · 已翻译")
                if not events.empty() and time.monotonic() - last_preview_at < 0.4:
                    continue
                last_preview_at = time.monotonic()
                gallery = [pages[number] for number in sorted(pages)]
                yield (
                    gr.update(),
                    gr.update(visible=False),
                    *[gr.update() for _ in range(4)],
                    gr.update(value=gallery, visible=True),
                    latest_status,
                    state,
                )
            elif kind == "result":
                finished = True
                result = list(value)
                result[1] = gr.update(value=result[1], visible=True)
                yield (
                    *result,
                    gr.update(visible=False),
                    f"**翻译完成** · 共用时 {_duration(time.monotonic() - started)} · 可查看或下载完整 PDF",
                    state,
                )
                break
            else:
                message = str(value) or "翻译已取消"
                yield (
                    gr.update(),
                    gr.update(visible=not bool(pages)),
                    *[gr.update() for _ in range(4)],
                    gr.update(
                        value=[pages[number] for number in sorted(pages)],
                        visible=bool(pages),
                    ),
                    f"**翻译已停止** · {message} · 已显示的完成页仍可查看",
                    state,
                )
                raise gr.Error(message) from value
    finally:
        if worker is not None and worker.is_alive():
            cancellation_event.set()
            stop_translate_file(state)

            # Keep update protection until the translation has really stopped.
            def release_after_stop():
                worker.join()
                update_manager.end_translation()

            Thread(target=release_after_stop, daemon=True).start()
        else:
            cancellation_event_map.pop(session_id, None)
            update_manager.end_translation()
        if finished and preview_dir.is_dir():
            root = Path("pdf2zh_files").resolve()
            target = preview_dir.resolve()
            if target.parent == root and target.name.startswith("preview-"):
                shutil.rmtree(target, ignore_errors=True)


def preview_uploaded_queue(value):
    files = queue_files(value)
    names = [Path(path).name for path in files]
    return (
        gr.update(value=files[0] if files else None, visible=True),
        gr.update(value=[], visible=False),
        (
            f"已载入 {len(files)} 个 PDF，按列表顺序逐个翻译。"
            if files
            else "请选择 PDF。"
        ),
        queue_summary(names, ["待翻译"] * len(files)) if files else "",
    )


def translate_queue_stream(
    file_type,
    file_input,
    link_input,
    service,
    lang_from,
    lang_to,
    page_range,
    page_input,
    prompt,
    threads,
    skip_subset_fonts,
    ignore_cache,
    vfont,
    use_babeldoc,
    recaptcha_response,
    state,
    chatgpt_model,
    chatgpt_effort,
    codex_fast,
    codex_speed,
    codex_parallel,
    progress=gr.Progress(),
    *envs,
):
    """Run one PDF at a time and retain completed downloads after a failure."""
    files = queue_files(file_input) if file_type == "File" else [None]
    if not files:
        raise gr.Error("请先选择至少一个 PDF。")
    names = [Path(path).name if path else "链接 PDF" for path in files]
    settings = [
        file_type,
        link_input if file_type != "File" else None,
        service,
        lang_from,
        lang_to,
        page_range,
        page_input,
        prompt,
        threads,
        skip_subset_fonts,
        ignore_cache,
        vfont,
        use_babeldoc,
        chatgpt_model,
        chatgpt_effort,
        codex_fast,
        codex_speed,
        codex_parallel,
        list(envs),
    ]
    try:
        signature = queue_signature(files if file_type == "File" else [], settings)
    except ValueError as error:
        raise gr.Error(str(error)) from error
    saved = state.get("queue_results") or []
    reuse = (
        not ignore_cache
        and file_type == "File"
        and state.get("queue_signature") == signature
    )
    records = [
        (
            saved[i]
            if reuse
            and i < len(saved)
            and saved[i]
            and all(Path(path).is_file() for path in saved[i])
            else None
        )
        for i in range(len(files))
    ]
    statuses = ["已完成" if record else "待翻译" for record in records]
    state["queue_signature"] = signature
    state["queue_results"] = records
    queue_id = uuid.uuid4()
    cancelled = Event()
    update_manager.begin_translation()
    cancellation_event_map[queue_id] = cancelled
    state["queue_id"] = queue_id

    def downloads():
        return gr.update(
            value=[path for record in records if record for path in record],
            visible=any(records),
        )

    def status_update(message):
        return (
            *[gr.update() for _ in range(7)],
            message,
            state,
            downloads(),
            queue_summary(names, statuses),
        )

    current = None
    index = 0
    try:
        yield status_update(f"**准备队列** · 共 {len(files)} 个 PDF")
        for index, file in enumerate(files):
            if cancelled.is_set():
                break
            if records[index]:
                mono, dual = records[index]
                yield (
                    mono,
                    gr.update(value=mono, visible=True),
                    dual,
                    gr.update(visible=True),
                    gr.update(visible=True),
                    gr.update(visible=True),
                    gr.update(visible=False),
                    f"**复用已完成文件** · {index + 1}/{len(files)}",
                    state,
                    downloads(),
                    queue_summary(names, statuses),
                )
                continue
            statuses[index] = "翻译中"
            current = translate_file_stream(
                file_type,
                file,
                link_input,
                service,
                lang_from,
                lang_to,
                page_range,
                page_input,
                prompt,
                threads,
                skip_subset_fonts,
                ignore_cache,
                vfont,
                use_babeldoc,
                recaptcha_response,
                state,
                chatgpt_model,
                chatgpt_effort,
                codex_fast,
                codex_speed,
                codex_parallel,
                progress,
                *envs,
            )
            completed = False
            for result in current:
                values = list(result)
                if isinstance(values[0], str) and isinstance(values[2], str):
                    paths = [values[0], values[2]]
                    if not all(Path(path).is_file() for path in paths):
                        raise gr.Error("译文文件未保存完整，队列已停止。")
                    records[index] = paths
                    statuses[index] = "已完成"
                    completed = True
                values[7] = f"**文件 {index + 1}/{len(files)}** · {values[7]}"
                yield (*values, downloads(), queue_summary(names, statuses))
            current.close()
            current = None
            if not completed:
                raise gr.Error("当前 PDF 未完成，队列已停止。")
        if cancelled.is_set():
            statuses = [
                "已停止" if status == "翻译中" else status for status in statuses
            ]
            yield status_update(
                "**队列已停止** · 已完成的文件仍可下载；再次翻译可继续。"
            )
        else:
            yield status_update(
                f"**队列翻译完成** · 已完成 {len(files)}/{len(files)} 个 PDF"
            )
    except Exception as error:
        if statuses[index] != "已完成":
            statuses[index] = "已停止" if cancelled.is_set() else "失败"
        message = "队列已停止" if cancelled.is_set() else f"队列已暂停：{error}"
        yield status_update(f"**{message}** · 已完成的文件仍可下载；再次翻译可继续。")
        if not cancelled.is_set():
            raise
    finally:
        cancelled.set()
        if current is not None:
            current.close()
        cancellation_event_map.pop(queue_id, None)
        if state.get("queue_id") == queue_id:
            state["queue_id"] = None
        update_manager.end_translation()


def refresh_codex_account(configured_bin):
    try:
        return get_codex_status(configured_bin or "codex")["message"]
    except Exception as exc:
        return f"无法读取 Codex 登录状态：{exc}"


def _codex_login_updates(result):
    message = result.get("message") or result.get("status") or "等待登录"
    if result.get("user_code"):
        message += "\n设备验证码：" + result["user_code"]
    url = result.get("auth_url") or ""
    if url:
        message += "\n请在浏览器打开：" + url
    pending = result.get("status") in {"starting", "pending", "waiting", "running"}
    return (
        message,
        result.get("login_id") if pending else None,
        gr.update(active=pending),
    )


def begin_codex_account_login(configured_bin, mode, login_id=None):
    if not desktop_mode:
        raise gr.Error("请在桌面软件中登录 Codex。")
    try:
        if login_id:
            existing = poll_codex_login(login_id)
            if existing.get("status") == "pending":
                return _codex_login_updates(existing)
        return _codex_login_updates(
            start_codex_login(mode=mode, configured_bin=configured_bin or "codex")
        )
    except Exception as exc:
        return str(exc), None, gr.update(active=False)


def poll_codex_account_login(login_id):
    if not login_id:
        return gr.update(), None, gr.update(active=False)
    return _codex_login_updates(poll_codex_login(login_id))


def cancel_codex_account_login(login_id):
    if not login_id:
        return gr.update(), None, gr.update(active=False)
    return _codex_login_updates(cancel_codex_login(login_id))


def check_app_updates():
    try:
        result = update_manager.check()
        return result["message"], gr.update(
            interactive=bool(result["available"]) and desktop_mode
        )
    except Exception as exc:
        return f"检查更新失败：{exc}", gr.update(interactive=False)


def install_app_update():
    events = Queue()

    def run():
        try:
            message = update_manager.install(
                progress_callback=lambda value: events.put((False, value))
            )
        except Exception as exc:
            message = f"更新未完成：{exc}"
        events.put((True, message))

    Thread(target=run, name="pdf2zh-update-download", daemon=True).start()
    yield "正在准备更新…"
    while True:
        try:
            finished, message = events.get(timeout=1.0)
        except Empty:
            continue
        yield message
        if finished:
            break


def babeldoc_translate_file(**kwargs):
    from babeldoc.high_level import init as babeldoc_init

    babeldoc_init()
    from babeldoc.high_level import async_translate as babeldoc_translate
    from babeldoc.translation_config import TranslationConfig as YadtConfig

    for translator in [
        GoogleTranslator,
        BingTranslator,
        DeepLTranslator,
        DeepLXTranslator,
        OllamaTranslator,
        XinferenceTranslator,
        AzureOpenAITranslator,
        OpenAITranslator,
        ZhipuTranslator,
        ModelScopeTranslator,
        SiliconTranslator,
        GeminiTranslator,
        AzureTranslator,
        TencentTranslator,
        DifyTranslator,
        AnythingLLMTranslator,
        ArgosTranslator,
        GrokTranslator,
        GroqTranslator,
        DeepseekTranslator,
        OpenAIlikedTranslator,
        CodexTranslator,
        QwenMtTranslator,
    ]:
        if kwargs["service"] == translator.name:
            translator = translator(
                kwargs["lang_in"],
                kwargs["lang_out"],
                "",
                envs=kwargs["envs"],
                prompt=kwargs["prompt"],
                ignore_cache=kwargs["ignore_cache"],
            )
            break
    else:
        raise ValueError("Unsupported translation service")
    import asyncio
    from babeldoc.main import create_progress_handler

    for file in kwargs["files"]:
        file = file.strip("\"'")
        yadt_config = YadtConfig(
            input_file=file,
            font=None,
            pages=",".join((str(x) for x in getattr(kwargs, "raw_pages", []))),
            output_dir=kwargs["output"],
            doc_layout_model=BABELDOC_MODEL,
            translator=translator,
            debug=False,
            lang_in=kwargs["lang_in"],
            lang_out=kwargs["lang_out"],
            no_dual=False,
            no_mono=False,
            qps=kwargs["thread"],
            use_rich_pbar=False,
            disable_rich_text_translate=not isinstance(
                translator, (OpenAITranslator, CodexTranslator)
            ),
            skip_clean=kwargs["skip_subset_fonts"],
            report_interval=0.5,
        )

        async def yadt_translate_coro(yadt_config):
            progress_context, progress_handler = create_progress_handler(yadt_config)

            # 开始翻译
            with progress_context:
                async for event in babeldoc_translate(yadt_config):
                    progress_handler(event)
                    if yadt_config.debug:
                        logger.debug(event)
                    kwargs["callback"](progress_context)
                    if kwargs["cancellation_event"].is_set():
                        yadt_config.cancel_translation()
                        raise CancelledError
                    if event["type"] == "finish":
                        result = event["translate_result"]
                        logger.info("Translation Result:")
                        logger.info(f"  Original PDF: {result.original_pdf_path}")
                        logger.info(f"  Time Cost: {result.total_seconds:.2f}s")
                        logger.info(f"  Mono PDF: {result.mono_pdf_path or 'None'}")
                        logger.info(f"  Dual PDF: {result.dual_pdf_path or 'None'}")
                        file_mono = result.mono_pdf_path
                        file_dual = result.dual_pdf_path
                        break
            import gc

            gc.collect()
            return (
                str(file_mono),
                str(file_mono),
                str(file_dual),
                gr.update(visible=True),
                gr.update(visible=True),
                gr.update(visible=True),
            )

        return asyncio.run(yadt_translate_coro(yadt_config))


# Global setup
desktop_mode = os.environ.get("PDF2ZH_DESKTOP") == "1"

custom_blue = gr.themes.Color(
    c50="#E8F3FF",
    c100="#BEDAFF",
    c200="#94BFFF",
    c300="#6AA1FF",
    c400="#4080FF",
    c500="#165DFF",  # Primary color
    c600="#0E42D2",
    c700="#0A2BA6",
    c800="#061D79",
    c900="#03114D",
    c950="#020B33",
)

desktop_teal = gr.themes.Color(
    c50="#ECFDF9",
    c100="#CFF9EF",
    c200="#A3F0DF",
    c300="#6CE2CD",
    c400="#31C9B4",
    c500="#18AA99",
    c600="#108A7F",
    c700="#106F68",
    c800="#115954",
    c900="#104A47",
    c950="#052D2C",
)

desktop_neutral = gr.themes.Color(
    c50="#F7FAFA",
    c100="#EEF3F3",
    c200="#DCE6E6",
    c300="#BDCDCE",
    c400="#91A8AA",
    c500="#718B8E",
    c600="#596F73",
    c700="#485A5E",
    c800="#3D4B4E",
    c900="#354043",
    c950="#1A2325",
)

custom_css = """
    .secondary-text {color: #999 !important;}
    footer {visibility: hidden}
    .env-warning {color: #dd5500 !important;}
    .env-success {color: #559900 !important;}

    /* Add dashed border to input-file class */
    .input-file {
        border: 1.2px dashed #165DFF !important;
        border-radius: 6px !important;
    }

    .progress-bar-wrap {
        border-radius: 8px !important;
    }

    .progress-bar {
        border-radius: 8px !important;
    }

    .pdf-canvas canvas {
        width: 100%;
    }
    #queue-status table {width: 100%; table-layout: fixed;}
    #queue-status th:first-child, #queue-status td:first-child {width: 56px; white-space: nowrap;}
    #queue-status th:last-child, #queue-status td:last-child {width: 88px; white-space: nowrap;}
    #queue-status td:nth-child(2) {overflow-wrap: anywhere;}
    """

desktop_css = """
    :root {
        --desk-ink: #10242e;
        --desk-muted: #637a81;
        --desk-line: rgba(26, 75, 84, 0.13);
        --desk-teal: #18aa99;
        --desk-navy: #081927;
        --desk-paper: rgba(255, 255, 255, 0.94);
    }

    body {
        background: #eaf1f1 !important;
    }

    .gradio-container {
        max-width: none !important;
        min-height: 100vh !important;
        padding: 22px 26px 34px !important;
        color: var(--desk-ink) !important;
        font-family: Inter, "Segoe UI Variable", "Microsoft YaHei UI", sans-serif !important;
        background:
            linear-gradient(rgba(18, 75, 82, 0.035) 1px, transparent 1px),
            linear-gradient(90deg, rgba(18, 75, 82, 0.035) 1px, transparent 1px),
            radial-gradient(circle at 12% 0%, rgba(38, 198, 181, 0.10), transparent 31%),
            #eaf1f1 !important;
        background-size: 36px 36px, 36px 36px, auto, auto !important;
    }

    #desktop-header {
        position: relative;
        overflow: hidden;
        margin: 0 0 18px !important;
        padding: 22px 26px !important;
        border: 1px solid rgba(126, 226, 208, 0.14) !important;
        border-radius: 22px !important;
        color: #ecfbf8 !important;
        background:
            radial-gradient(circle at 82% 10%, rgba(74, 127, 244, .28), transparent 30%),
            radial-gradient(circle at 15% 120%, rgba(49, 215, 197, .24), transparent 34%),
            linear-gradient(135deg, #071522, #0b2332 58%, #0a1b2a) !important;
        box-shadow: 0 18px 48px rgba(8, 25, 39, 0.18) !important;
    }

    .desktop-brand {
        display: flex;
        align-items: center;
        gap: 18px;
    }

    .desktop-mark {
        width: 56px;
        height: 56px;
        flex: none;
        filter: drop-shadow(0 0 16px rgba(69, 227, 202, .22));
    }

    .desktop-kicker {
        margin: 0 0 4px;
        color: #65e1ce;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: .18em;
        text-transform: uppercase;
    }

    .desktop-title-row {
        display: flex;
        align-items: baseline;
        flex-wrap: wrap;
        gap: 10px 16px;
    }

    .desktop-title {
        margin: 0;
        color: #f5fffd;
        font-size: clamp(24px, 3vw, 34px);
        font-weight: 680;
        letter-spacing: -.035em;
    }

    .desktop-subtitle {
        color: #9db8bd;
        font-size: 13px;
    }

    .desktop-badges {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-left: auto;
    }

    .desktop-badge {
        padding: 6px 10px;
        border: 1px solid rgba(128, 225, 210, .20);
        border-radius: 999px;
        color: #b9d8d8;
        font-size: 10px;
        font-weight: 650;
        letter-spacing: .08em;
        background: rgba(255, 255, 255, .045);
    }

    #workspace-row {
        align-items: stretch !important;
        gap: 18px !important;
    }

    #control-panel,
    #preview-panel {
        padding: 20px !important;
        border: 1px solid var(--desk-line) !important;
        border-radius: 22px !important;
        background: var(--desk-paper) !important;
        box-shadow: 0 14px 38px rgba(21, 58, 65, 0.08) !important;
    }

    #preview-panel {
        backdrop-filter: blur(18px);
    }

    #control-panel {
        min-width: 340px !important;
    }

    #control-panel h2,
    #preview-panel h2 {
        margin-top: 2px !important;
        color: var(--desk-ink) !important;
        font-size: 15px !important;
        font-weight: 760 !important;
        letter-spacing: .06em !important;
        text-transform: uppercase !important;
    }

    #control-panel .block,
    #preview-panel .block {
        border-color: rgba(30, 82, 90, .12) !important;
        box-shadow: none !important;
    }

    #control-panel .input-file {
        border: 1.5px dashed rgba(24, 170, 153, .62) !important;
        border-radius: 15px !important;
        background: linear-gradient(145deg, rgba(236, 253, 249, .92), rgba(247, 250, 250, .74)) !important;
    }

    #control-panel button.primary {
        min-height: 44px !important;
        border: 0 !important;
        border-radius: 13px !important;
        color: white !important;
        font-weight: 720 !important;
        background: linear-gradient(135deg, #16af9c, #197f88) !important;
        box-shadow: 0 9px 22px rgba(24, 170, 153, .24) !important;
    }

    #control-panel button.primary:hover {
        transform: translateY(-1px);
        box-shadow: 0 12px 26px rgba(24, 170, 153, .30) !important;
    }

    #control-panel button.secondary {
        border-color: rgba(62, 91, 97, .15) !important;
        border-radius: 13px !important;
        color: #50686e !important;
        background: #f1f5f5 !important;
    }

    #preview-panel .pdf-canvas {
        border-radius: 15px !important;
        background: #dce6e6 !important;
    }

    #preview-panel canvas {
        border-radius: 8px !important;
        box-shadow: 0 8px 26px rgba(8, 25, 39, .12) !important;
    }

    #control-panel .secondary-text {
        color: #82979b !important;
        font-size: 12px !important;
    }

    @media (max-width: 1080px) {
        .gradio-container { padding: 14px !important; }
        #desktop-header { padding: 18px !important; }
        .desktop-badges { display: none; }
        #workspace-row { flex-direction: column !important; }
    }
"""

if desktop_mode:
    custom_css += desktop_css

app_theme = gr.themes.Default(
    primary_hue=desktop_teal if desktop_mode else custom_blue,
    neutral_hue=desktop_neutral if desktop_mode else gr.themes.colors.gray,
    spacing_size="md",
    radius_size="lg",
    font=("Inter", "Segoe UI", "Microsoft YaHei UI", "sans-serif"),
)

desktop_header_html = f"""
<div id="desktop-header">
  <div class="desktop-brand">
    <svg class="desktop-mark" viewBox="0 0 80 80" aria-hidden="true">
      <defs><linearGradient id="desk-g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#79f2d4"/><stop offset="1" stop-color="#4b7df3"/></linearGradient></defs>
      <path d="M15 24 40 10l25 14v31L40 70 15 55Z" fill="none" stroke="url(#desk-g)" stroke-width="3"/>
      <path d="m15 24 25 15 25-15M40 39v31" fill="none" stroke="url(#desk-g)" stroke-width="2" opacity=".72"/>
      <circle cx="15" cy="24" r="5" fill="#79f2d4"/><circle cx="65" cy="24" r="5" fill="#4b7df3"/>
      <circle cx="40" cy="39" r="6" fill="url(#desk-g)"/><circle cx="40" cy="70" r="4" fill="#54b5e9"/>
    </svg>
    <div>
      <p class="desktop-kicker">Unofficial Codex Edition</p>
      <div class="desktop-title-row">
        <h1 class="desktop-title">PDFMathTranslate</h1>
        <span class="desktop-subtitle">科研 PDF 翻译工作台</span>
      </div>
    </div>
    <div class="desktop-badges">
      <span class="desktop-badge">LOCAL · PRIVATE</span>
      <span class="desktop-badge">{APP_VERSION}</span>
    </div>
  </div>
</div>
"""

demo_recaptcha = """
    <script src="https://www.google.com/recaptcha/api.js?render=explicit" async defer></script>
    <script type="text/javascript">
        var onVerify = function(token) {
            el=document.getElementById('verify').getElementsByTagName('textarea')[0];
            el.value=token;
            el.dispatchEvent(new Event('input'));
        };
    </script>
    """

tech_details_string = f"""
                    <summary>Technical details</summary>
                    - GitHub: <a href="https://github.com/Byaidu/PDFMathTranslate">Byaidu/PDFMathTranslate</a><br>
                    - BabelDOC: <a href="https://github.com/funstory-ai/BabelDOC">funstory-ai/BabelDOC</a><br>
                    - GUI by: <a href="https://github.com/reycn">Rongxin</a><br>
                    - pdf2zh Version: {__version__} <br>
                    - BabelDOC Version: {babeldoc_version}
                """
cancellation_event_map = {}


# The following code creates the GUI
with gr.Blocks(
    title="PDFMathTranslate - PDF Translation with preserved formats",
    theme=app_theme,
    css=custom_css,
    head=demo_recaptcha if flag_demo else "",
) as demo:
    if desktop_mode:
        gr.HTML(desktop_header_html)
    else:
        gr.Markdown(
            "# [PDFMathTranslate @ GitHub](https://github.com/Byaidu/PDFMathTranslate)"
        )

    with gr.Row(elem_id="workspace-row" if desktop_mode else None):
        with gr.Column(scale=1, elem_id="control-panel" if desktop_mode else None):
            gr.Markdown("## File | < 5 MB" if flag_demo else "## File")
            file_type = gr.Radio(
                choices=["File", "Link"],
                label="Type",
                value="File",
            )
            file_input = gr.File(
                label="PDF 文件（按列表顺序翻译）",
                file_count="multiple",
                file_types=[".pdf"],
                type="filepath",
                elem_classes=["input-file"],
            )
            link_input = gr.Textbox(
                label="Link",
                visible=False,
                interactive=True,
            )
            gr.Markdown("## Option")
            service = gr.Dropdown(
                label="Service",
                choices=enabled_services,
                value=(
                    "Codex"
                    if desktop_mode and "Codex" in enabled_services
                    else enabled_services[0]
                ),
            )
            with gr.Accordion("模型与服务设置", open=False):
                envs = []
                for _ in range(MAX_ENV_FIELDS):
                    envs.append(
                        gr.Textbox(
                            visible=False,
                            interactive=True,
                        )
                    )
            with gr.Column(
                visible=desktop_mode and "Codex" in enabled_services
            ) as codex_panel:
                codex_status = gr.Textbox(
                    label="Codex 登录状态",
                    value="正在检查登录状态…",
                    interactive=False,
                    lines=3,
                )
                with gr.Row():
                    codex_login_btn = gr.Button("登录 Codex", visible=desktop_mode)
                    codex_refresh_btn = gr.Button("刷新登录状态")
                    codex_cancel_btn = gr.Button("取消登录", visible=desktop_mode)
                codex_login_mode = gr.Radio(
                    choices=[
                        ("浏览器登录（推荐）", "browser"),
                        ("设备验证码登录", "device"),
                    ],
                    value="browser",
                    label="登录方式",
                    visible=desktop_mode,
                )
                codex_login_id = gr.State(None)
                codex_login_timer = gr.Timer(1.5, active=False)
                gr.Markdown(
                    "在软件内点击登录，由官方浏览器页面完成账号验证；完成后这里会自动显示状态。"
                )
                codex_parallel = gr.Slider(
                    minimum=1,
                    maximum=8,
                    step=1,
                    value=ConfigManager.get("PDF2ZH_CODEX_PARALLEL", 4),
                    label="并行批次数",
                    info="默认 4；遇到限流可降低。实际并行受待翻译批次数限制。",
                )
                codex_speed = gr.Radio(
                    choices=[
                        ("标准并行", "standard"),
                        ("双倍并行（2×并发）", "double"),
                    ],
                    value=ConfigManager.get("PDF2ZH_CODEX_SPEED", "standard"),
                    label="翻译速度",
                    info="双倍模式将上方并行数乘以 2，最多 16；不保证总耗时减半。",
                )
                codex_fast = gr.Checkbox(
                    label="启用 Codex Fast 模式",
                    value=ConfigManager.get("PDF2ZH_CODEX_FAST", False),
                    info="官方加速服务，通常增加额度消耗；需模型和账号支持。与并行模式可叠加。",
                )
            with gr.Column(visible=False) as chatgpt_panel:
                with gr.Row():
                    chatgpt_login_btn = gr.Button("连接已登录的 Chrome")
                    chatgpt_refresh_btn = gr.Button("刷新模型与连接状态")
                chatgpt_status = gr.Textbox(
                    label="ChatGPT 连接状态",
                    value="请先在普通 Chrome 登录 ChatGPT，开启远程调试，再点击连接并允许 Chrome 弹窗。",
                    interactive=False,
                    lines=2,
                )
                chatgpt_model = gr.Dropdown(
                    label="ChatGPT 模型",
                    choices=[],
                    value=None,
                    interactive=False,
                    info="从已连接 Chrome 的当前账号读取可用模型。",
                )
                chatgpt_effort = gr.Dropdown(
                    label="推理强度",
                    choices=[],
                    value=None,
                    interactive=False,
                    info="随模型变化；网页未提供此选项时保持空白。",
                )
                gr.Markdown(
                    "首次连接：在普通 **Google Chrome** 登录 ChatGPT，"
                    "在地址栏输入 `chrome://inspect/#remote-debugging` 并开启远程调试。"
                    "点击上方连接按钮后，在 Chrome 弹窗选择**允许**，再刷新模型。\n\n"
                    "翻译时保持 Chrome 运行。每份 PDF 任务的所有批次共用一个对话；"
                    "软件使用自己的翻译标签，不操作已有聊天。"
                    "账号和部分模型偏好会共享。关闭软件不清除登录，重新连接可能再次要求允许。"
                    "中断后重新连接，保持相同文件和翻译设置可恢复原对话及已有批次。"
                )
            with gr.Row():
                lang_from = gr.Dropdown(
                    label="Translate from",
                    choices=lang_map.keys(),
                    value=ConfigManager.get("PDF2ZH_LANG_FROM", "English"),
                )
                lang_to = gr.Dropdown(
                    label="Translate to",
                    choices=lang_map.keys(),
                    value=ConfigManager.get("PDF2ZH_LANG_TO", "Simplified Chinese"),
                )
            page_range = gr.Radio(
                choices=page_map.keys(),
                label="Pages",
                value=list(page_map.keys())[0],
            )

            page_input = gr.Textbox(
                label="Page range",
                visible=False,
                interactive=True,
            )

            with gr.Accordion("Open for More Experimental Options!", open=False):
                gr.Markdown("#### Experimental")
                threads = gr.Textbox(
                    label="并行数（其他翻译服务）",
                    interactive=True,
                    value="4",
                )
                skip_subset_fonts = gr.Checkbox(
                    label="Skip font subsetting", interactive=True, value=False
                )
                ignore_cache = gr.Checkbox(
                    label="Ignore cache", interactive=True, value=False
                )
                vfont = gr.Textbox(
                    label="Custom formula font regex (vfont)",
                    interactive=True,
                    value=ConfigManager.get("PDF2ZH_VFONT", ""),
                )
                prompt = gr.Textbox(
                    label="Custom Prompt for llm", interactive=True, visible=False
                )
                use_babeldoc = gr.Checkbox(
                    label="Use BabelDOC", interactive=True, value=False
                )

            def on_select_service(service, evt: gr.EventData):
                translator = service_map[service]
                is_chatgpt = translator.name == ChatGPTTranslator.name
                _envs = []
                for _ in range(MAX_ENV_FIELDS):
                    _envs.append(gr.update(visible=False, value=""))
                for i, env in enumerate(translator.envs.items()):
                    label = env[0]
                    value = ConfigManager.get_env_by_translatername(
                        translator, env[0], env[1]
                    )
                    if (
                        translator.name == "codex"
                        and label == "CODEX_REASONING_EFFORT"
                        and str(value or "").strip() in {"", "none"}
                        and not ConfigManager.get_env_by_translatername(
                            translator, "CODEX_MODEL", None
                        )
                        and not ConfigManager.get_env_by_translatername(
                            translator, "CODEX_PROFILE", None
                        )
                    ):
                        value = "low"
                    visible = not is_chatgpt and label not in {
                        "CODEX_FAST_MODE",
                        "CODEX_MAX_CONCURRENCY",
                    }
                    if hidden_gradio_details:
                        if (
                            "MODEL" not in str(label).upper()
                            and "REASONING_EFFORT" not in str(label).upper()
                            and value
                            and hidden_gradio_details
                        ):
                            visible = False
                        # Hidden Keys From Gradio
                        if "API_KEY" in label.upper():
                            value = "***"  # We use "***" Present Real API_KEY
                    _envs[i] = gr.update(
                        visible=visible,
                        label=label,
                        value=value,
                    )
                return [
                    *_envs,
                    gr.update(visible=translator.CustomPrompt),
                    (
                        gr.update(value="1", interactive=False, visible=False)
                        if is_chatgpt
                        else (
                            gr.update(value="4", interactive=True, visible=False)
                            if translator.name == "codex"
                            else gr.update(interactive=True, visible=True)
                        )
                    ),
                    gr.update(visible=is_chatgpt),
                    gr.update(visible=translator.name == "codex"),
                    (
                        gr.update(value=False, interactive=False)
                        if is_chatgpt
                        else gr.update(interactive=True)
                    ),
                    *(
                        _chatgpt_action(get_chatgpt_status)
                        if is_chatgpt
                        else (gr.update(), gr.update(), gr.update())
                    ),
                ]

            def on_select_filetype(file_type):
                return (
                    gr.update(visible=file_type == "File"),
                    gr.update(visible=file_type == "Link"),
                )

            def on_select_page(choice):
                if choice == "Others":
                    return gr.update(visible=True)
                else:
                    return gr.update(visible=False)

            def on_vfont_change(value):
                ConfigManager.set("PDF2ZH_VFONT", value)
                return value

            output_title = gr.Markdown("## Translated", visible=False)
            output_file_mono = gr.File(
                label="Download Translation (Mono)", visible=False
            )
            output_file_dual = gr.File(
                label="Download Translation (Dual)", visible=False
            )
            queue_downloads = gr.File(
                label="队列已完成文件（译文与双语 PDF）",
                file_count="multiple",
                visible=False,
            )
            recaptcha_response = gr.Textbox(
                label="reCAPTCHA Response", elem_id="verify", visible=False
            )
            recaptcha_box = gr.HTML('<div id="recaptcha-box"></div>')
            translate_btn = gr.Button("翻译 / 继续队列", variant="primary")
            cancellation_btn = gr.Button("停止队列", variant="secondary")
            with gr.Accordion("关于与更新", open=False):
                gr.Markdown(
                    f"**Codex Edition {APP_VERSION}** · [GitHub 仓库]({REPOSITORY_URL}) · [发行记录]({REPOSITORY_URL}/releases)"
                )
                update_status = gr.Textbox(
                    label="更新状态",
                    value="点击检查 GitHub 最新版本。",
                    interactive=False,
                )
                with gr.Row():
                    update_check_btn = gr.Button("检查更新")
                    update_install_btn = gr.Button(
                        "下载并安装更新", interactive=False, visible=desktop_mode
                    )
                gr.Markdown(
                    "安装更新前请完成或取消翻译。下载并校验成功后关闭软件，由安装程序完成更新；保留账号与设置。"
                )
                update_check_btn.click(
                    check_app_updates, outputs=[update_status, update_install_btn]
                )
                update_install_btn.click(install_app_update, outputs=update_status)
            tech_details_tog = gr.Markdown(
                tech_details_string,
                elem_classes=["secondary-text"],
            )
            page_range.select(on_select_page, page_range, page_input)
            service.select(
                on_select_service,
                service,
                [
                    *envs,
                    prompt,
                    threads,
                    chatgpt_panel,
                    codex_panel,
                    use_babeldoc,
                    chatgpt_status,
                    chatgpt_model,
                    chatgpt_effort,
                ],
            )
            codex_login_outputs = [codex_status, codex_login_id, codex_login_timer]
            codex_login_btn.click(
                begin_codex_account_login,
                [envs[0], codex_login_mode, codex_login_id],
                codex_login_outputs,
                queue=False,
            )
            codex_refresh_btn.click(
                refresh_codex_account, envs[0], codex_status, queue=False
            )
            codex_cancel_btn.click(
                cancel_codex_account_login,
                codex_login_id,
                codex_login_outputs,
                queue=False,
            )
            codex_login_timer.tick(
                poll_codex_account_login,
                codex_login_id,
                codex_login_outputs,
                queue=False,
            )
            chatgpt_outputs = [chatgpt_status, chatgpt_model, chatgpt_effort]
            chatgpt_login_btn.click(
                open_chatgpt_login,
                inputs=[],
                outputs=chatgpt_outputs,
            )
            chatgpt_refresh_btn.click(
                refresh_chatgpt_controls,
                inputs=[chatgpt_model, chatgpt_effort],
                outputs=chatgpt_outputs,
            )
            chatgpt_model.input(
                select_chatgpt_model,
                inputs=[chatgpt_model],
                outputs=chatgpt_outputs,
            )
            chatgpt_effort.input(
                refresh_chatgpt_controls,
                inputs=[chatgpt_model, chatgpt_effort],
                outputs=chatgpt_outputs,
            )
            vfont.change(on_vfont_change, inputs=vfont, outputs=None)
            file_type.select(
                on_select_filetype,
                file_type,
                [file_input, link_input],
                js=(
                    f"""
                    (a,b)=>{{
                        try{{
                            grecaptcha.render('recaptcha-box',{{
                                'sitekey':'{client_key}',
                                'callback':'onVerify'
                            }});
                        }}catch(error){{}}
                        return [a];
                    }}
                    """
                    if flag_demo
                    else ""
                ),
            )

        with gr.Column(scale=2, elem_id="preview-panel" if desktop_mode else None):
            gr.Markdown("## 翻译预览")
            translation_status = gr.Markdown(
                "上传 PDF 后可查看原文；翻译期间会逐页显示已完成的结果。"
            )
            queue_status = gr.Markdown("", elem_id="queue-status")
            live_preview = gr.Gallery(
                label="已翻译页面（按原文页码）",
                visible=False,
                columns=1,
                object_fit="contain",
                height=1100 if desktop_mode else 1600,
                allow_preview=True,
            )
            preview = PDF(
                label="Document Preview",
                visible=True,
                height=1200 if desktop_mode else 2000,
            )

    # Event handlers
    file_input.change(
        preview_uploaded_queue,
        inputs=file_input,
        outputs=[preview, live_preview, translation_status, queue_status],
        js=(
            f"""
            (a,b)=>{{
                try{{
                    grecaptcha.render('recaptcha-box',{{
                        'sitekey':'{client_key}',
                        'callback':'onVerify'
                    }});
                }}catch(error){{}}
                return [a];
            }}
            """
            if flag_demo
            else ""
        ),
    )

    state = gr.State({"session_id": None})

    translate_btn.click(
        translate_queue_stream,
        inputs=[
            file_type,
            file_input,
            link_input,
            service,
            lang_from,
            lang_to,
            page_range,
            page_input,
            prompt,
            threads,
            skip_subset_fonts,
            ignore_cache,
            vfont,
            use_babeldoc,
            recaptcha_response,
            state,
            chatgpt_model,
            chatgpt_effort,
            codex_fast,
            codex_speed,
            codex_parallel,
            *envs,
        ],
        outputs=[
            output_file_mono,
            preview,
            output_file_dual,
            output_file_mono,
            output_file_dual,
            output_title,
            live_preview,
            translation_status,
            state,
            queue_downloads,
            queue_status,
        ],
        api_name="translate_file_stream",
        concurrency_limit=1,
        show_progress="minimal",
    ).then(lambda: None, js="()=>{grecaptcha.reset()}" if flag_demo else "")

    demo.load(
        on_select_service,
        service,
        [
            *envs,
            prompt,
            threads,
            chatgpt_panel,
            codex_panel,
            use_babeldoc,
            chatgpt_status,
            chatgpt_model,
            chatgpt_effort,
        ],
    ).then(
        lambda: refresh_codex_account(
            ConfigManager.get_env_by_translatername(
                CodexTranslator, "CODEX_BIN", "codex"
            )
        ),
        outputs=codex_status,
    )

    cancellation_btn.click(
        stop_translate_file,
        inputs=[state],
        queue=False,
    )


def parse_user_passwd(file_path: str) -> tuple:
    """
    Parse the user name and password from the file.

    Inputs:
        - file_path: The file path to read.
    Outputs:
        - tuple_list: The list of tuples of user name and password.
        - content: The content of the file
    """
    tuple_list = []
    content = ""
    if not file_path:
        return tuple_list, content
    if len(file_path) == 2:
        try:
            with open(file_path[1], "r", encoding="utf-8") as file:
                content = file.read()
        except FileNotFoundError:
            print(f"Error: File '{file_path[1]}' not found.")
    try:
        with open(file_path[0], "r", encoding="utf-8") as file:
            tuple_list = [
                tuple(line.strip().split(",")) for line in file if line.strip()
            ]
    except FileNotFoundError:
        print(f"Error: File '{file_path[0]}' not found.")
    return tuple_list, content


def setup_gui(
    share: bool = False, auth_file: list = ["", ""], server_port: int | None = None
) -> None:
    """
    Setup the GUI with the given parameters.

    Inputs:
        - share: Whether to share the GUI.
        - auth_file: The file path to read the user name and password.

    Outputs:
        - None
    """
    user_list, html = parse_user_passwd(auth_file)
    if flag_demo:
        demo.launch(server_name="0.0.0.0", max_file_size="5mb", inbrowser=True)
    else:
        if len(user_list) == 0:
            try:
                demo.launch(
                    server_name="0.0.0.0",
                    debug=True,
                    inbrowser=True,
                    share=share,
                    server_port=server_port,
                )
            except Exception:
                print(
                    "Error launching GUI using 0.0.0.0.\nThis may be caused by global mode of proxy software."
                )
                try:
                    demo.launch(
                        server_name="127.0.0.1",
                        debug=True,
                        inbrowser=True,
                        share=share,
                        server_port=server_port,
                    )
                except Exception:
                    print(
                        "Error launching GUI using 127.0.0.1.\nThis may be caused by global mode of proxy software."
                    )
                    demo.launch(
                        debug=True, inbrowser=True, share=True, server_port=server_port
                    )
        else:
            try:
                demo.launch(
                    server_name="0.0.0.0",
                    debug=True,
                    inbrowser=True,
                    share=share,
                    auth=user_list,
                    auth_message=html,
                    server_port=server_port,
                )
            except Exception:
                print(
                    "Error launching GUI using 0.0.0.0.\nThis may be caused by global mode of proxy software."
                )
                try:
                    demo.launch(
                        server_name="127.0.0.1",
                        debug=True,
                        inbrowser=True,
                        share=share,
                        auth=user_list,
                        auth_message=html,
                        server_port=server_port,
                    )
                except Exception:
                    print(
                        "Error launching GUI using 127.0.0.1.\nThis may be caused by global mode of proxy software."
                    )
                    demo.launch(
                        debug=True,
                        inbrowser=True,
                        share=True,
                        auth=user_list,
                        auth_message=html,
                        server_port=server_port,
                    )


# For auto-reloading while developing
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    setup_gui()
