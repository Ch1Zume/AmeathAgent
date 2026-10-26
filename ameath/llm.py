"""The single model core shared by reading, vision, and game decisions."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import urlparse

from .config import Config, Settings, validate_endpoint


class ModelError(Exception):
    pass


class ModelResponseError(ModelError):
    def __init__(self, reason: str):
        self.reason = reason
        messages = {
            "empty_final": "模型服务返回了空的最终回答；请求已到达服务，但没有可显示的内容。",
            "length": "模型达到本次输出额度后停止，最终回答尚未完成。深度思考也会占用输出额度。",
            "content_filter": "模型服务过滤了本次输出，没有返回可显示的最终回答。",
            "interrupted": "模型服务在生成完成前中断了请求，请稍后重试。",
            "tool_calls": "服务返回了工具调用，但当前对话没有相应工具结果，请检查模型接口。",
            "invalid_shape": "模型响应格式与所选接口不匹配，请检查接口类型。",
            "service_error": "模型服务在响应中报告了错误，请稍后重试。",
        }
        super().__init__(messages.get(reason, "模型没有返回完整的最终回答。"))


_DIAGNOSTIC_LOCK = threading.Lock()


def record_diagnostic(config: Config, settings: Settings, *, status: int, data=None, reason=""):
    """Only structural metadata; never persist prompts, reply text, reasoning or credentials."""
    data = data if isinstance(data, dict) else {}
    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    finish = first.get("finish_reason")
    if finish not in (None, "stop", "length", "content_filter", "tool_calls", "function_call", "insufficient_system_resource", "aborted"):
        finish = "unknown"
    response_status = data.get("status")
    if response_status not in (None, "completed", "failed", "incomplete", "in_progress"):
        response_status = "unknown"
    record = {"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "model": settings.model, "protocol": settings.protocol, "http_status": status,
              "finish_reason": finish, "response_status": response_status, "reason": reason}
    try:
        path = config.directory / "model-diagnostics.jsonl"
        with _DIAGNOSTIC_LOCK:
            lines = path.read_text(encoding="utf-8").splitlines()[-19:] if path.exists() else []
            lines.append(json.dumps(record, ensure_ascii=False))
            temporary = path.with_suffix(".tmp")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temporary.replace(path)
    except OSError:
        pass  # Diagnostics must not prevent an otherwise successful reply.


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def is_deepseek(settings: Settings) -> bool:
    return urlparse(settings.endpoint).hostname == "api.deepseek.com"


def tune_payload(payload: dict, settings: Settings, *, thinking: bool | None = None) -> dict:
    """DeepSeek has its own reasoning switch and output-budget parameter."""
    payload = dict(payload)
    if is_deepseek(settings):
        enabled = settings.thinking if thinking is None else thinking
        if settings.protocol == "chat":
            if "max_completion_tokens" in payload:
                payload["max_tokens"] = payload.pop("max_completion_tokens")
            payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
            if enabled:
                payload["reasoning_effort"] = settings.reasoning_effort
            else:
                payload.pop("reasoning_effort", None)
        else:
            payload["reasoning"] = {"effort": settings.reasoning_effort if enabled else "none"}
    return payload


@dataclass(frozen=True)
class Completion:
    text: str
    usage: dict
    model: str


def extract_text(data: dict, protocol: str) -> str:
    if not isinstance(data, dict):
        raise ModelResponseError("invalid_shape")
    if data.get("error"):
        raise ModelResponseError("service_error")
    if protocol == "chat":
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelResponseError("invalid_shape")
        choice = choices[0]
        reason = choice.get("finish_reason")
        if reason == "length":
            raise ModelResponseError("length")
        if reason == "content_filter":
            raise ModelResponseError("content_filter")
        if reason in ("insufficient_system_resource", "aborted"):
            raise ModelResponseError("interrupted")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelResponseError("invalid_shape")
        if message.get("tool_calls") or message.get("function_call"):
            raise ModelResponseError("tool_calls")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts = [part["text"] for part in content if isinstance(part, dict)
                     and part.get("type") in ("text", "output_text") and isinstance(part.get("text"), str)]
            if any(text.strip() for text in texts):
                return "\n\n".join(texts).strip()
        if message.get("refusal"):
            raise ModelResponseError("content_filter")
    else:
        if data.get("status") in ("failed", "incomplete"):
            details = data.get("incomplete_details") or {}
            reason = details.get("reason") if isinstance(details, dict) else ""
            raise ModelResponseError("length" if reason == "max_output_tokens" else "content_filter" if reason == "content_filter" else "interrupted")
        output = data.get("output")
        if not isinstance(output, list):
            raise ModelResponseError("invalid_shape")
        texts = [part["text"] for item in output if isinstance(item, dict) and item.get("type") == "message"
                 for part in (item.get("content") or []) if isinstance(part, dict)
                 and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
        if any(text.strip() for text in texts):
            return "\n\n".join(texts).strip()
        if any(isinstance(item, dict) and item.get("type") in ("function_call", "custom_tool_call") for item in output):
            raise ModelResponseError("tool_calls")
    raise ModelResponseError("empty_final")


class ModelClient:
    def __init__(self, config: Config):
        self.config = config

    def request(self, payload: dict, cancelled: threading.Event, *, thinking: bool | None = None,
                settings: Settings | None = None) -> Completion:
        settings = replace(settings or self.config.settings)
        if cancelled.is_set():
            raise ModelError("请求已取消。")
        if settings.endpoint != self.config.settings.endpoint:
            raise ModelError("模型服务设置已经变化，请重新发起请求。")
        endpoint = validate_endpoint(settings.endpoint)
        key = self.config.key()
        if not key and urlparse(endpoint).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ModelError("请在 Ameath 的模型设置中填写 DeepSeek API 密钥。")
        payload = tune_payload(payload, settings, thinking=thinking)
        route = "/chat/completions" if settings.protocol == "chat" else "/responses"
        headers = {"Content-Type": "application/json", "User-Agent": "Ameath/0.6"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(endpoint + route, json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers)
        if cancelled.is_set():
            raise ModelError("请求已取消。")
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=90) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ModelError("模型响应过大，请减少输入内容。")
                data = json.loads(raw)
        except urllib.error.HTTPError as error:
            record_diagnostic(self.config, settings, status=error.code, reason="http_error")
            messages = {401: "密钥无效或已过期，请检查模型设置。", 402: "模型服务余额不足。",
                        403: "该账号没有访问此模型的权限。", 404: "未找到接口或模型，请检查服务地址、模型名和接口类型。",
                        429: "模型服务限流或额度不足，请稍后重试。"}
            raise ModelError(messages.get(error.code, f"模型服务返回错误（HTTP {error.code}），请检查接口配置。")) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelError("暂时连接不上模型服务，请检查网络和服务地址。") from None
        except (ValueError, TypeError):
            raise ModelError("服务返回了无法解析的内容，请检查模型接口。") from None
        if cancelled.is_set():
            raise ModelError("请求已取消。")
        if not isinstance(data, dict):
            raise ModelError("模型响应格式不正确。")
        # reasoning_content is deliberately neither persisted nor shown as commentary.
        try:
            final = extract_text(data, settings.protocol)
        except ModelResponseError as error:
            record_diagnostic(self.config, settings, status=200, data=data, reason=error.reason)
            raise
        record_diagnostic(self.config, settings, status=200, data=data, reason="completed")
        return Completion(final, data.get("usage") or {}, data.get("model") or settings.model)

    def complete_json(self, system: str, material: dict, cancelled: threading.Event) -> Completion:
        settings = replace(self.config.settings)
        settings.protocol = "chat"
        payload = {"model": settings.model, "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(material, ensure_ascii=False, separators=(",", ":"))}],
                   "response_format": {"type": "json_object"}, "max_completion_tokens": 8192, "stream": False}
        return self.request(payload, cancelled, thinking=settings.game_thinking, settings=settings)
