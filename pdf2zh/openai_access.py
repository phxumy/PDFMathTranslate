"""Chat Completions and discovery for the OpenAI settings used by the desktop app."""

import re
import time

import httpx
import openai

from pdf2zh.openai_keys import endpoint


class OpenAIServiceError(RuntimeError):
    """A completed request or a bounded series of retries failed."""


def client_for(url, key, *, timeout=120):
    return openai.OpenAI(
        api_key=key,
        base_url=endpoint(url),
        max_retries=0,
        timeout=httpx.Timeout(timeout, connect=8),
        http_client=httpx.Client(follow_redirects=False),
    )


def chat_options(model):
    # Preserve deterministic sampling and existing caches for the classic GPT
    # chat families. New model IDs and private deployment names use SDK defaults.
    if re.match(r"^gpt-(?:3\.5-turbo|4(?:o|\.1)?)(?:-|$)", model or ""):
        return {"temperature": 0}
    return {}


def _reason(error):
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    if code in (
        "insufficient_quota",
        "billing_hard_limit_reached",
        "billing_not_active",
    ):
        return "项目额度不足或计费未启用", False
    if isinstance(error, openai.APITimeoutError):
        return "连接或响应超时", True
    if isinstance(error, openai.APIConnectionError):
        return "无法连接，请检查 Base URL 和网络", True
    if status in (401, 403):
        return "请检查密钥及项目权限", False
    if status == 404 or code == "model_not_found":
        return "请检查 Base URL、模型名称和访问权限", False
    if status == 429:
        return "请求频率受限，请稍后重试", True
    if status in (408, 409) or (isinstance(status, int) and 500 <= status < 600):
        return "服务暂时不可用", True
    param = getattr(error, "param", None)
    if param in (
        "temperature",
        "messages",
        "model",
        "max_tokens",
        "max_completion_tokens",
        "reasoning_effort",
    ):
        return f"所选模型拒绝请求参数 {param}", False
    return "接口拒绝了请求，请检查模型是否支持 Chat Completions 及当前配置", False


def _error_message(exc):
    reason, _ = _reason(exc)
    status = getattr(exc, "status_code", None)
    return f"OpenAI HTTP {status} · {reason}" if status else f"OpenAI · {reason}"


def chat_request(client, model, messages, options, *, test=False):
    # The SDK's own retries are disabled for this client. Do not multiply
    # provider retries by segment retries, especially for insufficient quota.
    params = dict(options)
    if test:
        params["max_completion_tokens"] = 16
    attempts = 1 if test else 3
    for attempt in range(attempts):
        try:
            while True:
                try:
                    response = client.chat.completions.create(
                        model=model, messages=messages, **params
                    )
                    break
                except openai.BadRequestError as exc:
                    if exc.param == "temperature" and "temperature" in params:
                        del params["temperature"]
                        continue
                    if (
                        test
                        and exc.param == "max_completion_tokens"
                        and "max_completion_tokens" in params
                    ):
                        params["max_tokens"] = params.pop("max_completion_tokens")
                        continue
                    raise
            if not response.choices:
                raise OpenAIServiceError("OpenAI 未返回可用的文本选项。")
            content = response.choices[0].message.content
            if test:
                return response.choices[0].finish_reason
            if not isinstance(content, str) or not content.strip():
                raise OpenAIServiceError("OpenAI 返回了空译文，请检查模型。")
            return content.strip()
        except openai.APIError as exc:
            if not _reason(exc)[1] or attempt + 1 == attempts:
                raise OpenAIServiceError(_error_message(exc)) from None
            time.sleep(2**attempt)


def model_ids(url, key):
    try:
        with client_for(url, key, timeout=10) as client:
            return sorted(
                {
                    item.id
                    for item in client.models.list()
                    if isinstance(item.id, str) and item.id.strip()
                }
            )
    except openai.APIError as exc:
        raise OpenAIServiceError(_error_message(exc)) from None


def test_model_connection(url, key, model):
    if not (model or "").strip():
        raise ValueError("请先选择或填写要测试的模型。")
    with client_for(url, key, timeout=20) as client:
        chat_request(
            client,
            model.strip(),
            [{"role": "user", "content": "OK"}],
            chat_options(model.strip()),
            test=True,
        )
