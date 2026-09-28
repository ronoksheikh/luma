"""OpenAI-compatible LLM access (official ``openai`` SDK, any ``base_url``)."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import time
from typing import Any, Awaitable, Callable

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from ..config import config
from ..secrets_store import Credentials, redact

PRESETS = {
    "openai": {"base_url": "https://api.openai.com/v1", "label": "OpenAI"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "label": "OpenRouter"},
    "custom": {"base_url": "", "label": "Custom (vLLM, Ollama, LM Studio…)"},
}


def is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "")


def make_client(creds: Credentials, timeout: float = 180.0) -> AsyncOpenAI:
    headers = {}
    if is_openrouter(creds.llm_base_url):
        # OpenRouter app attribution headers
        headers = {"HTTP-Referer": config.app_url, "X-Title": "Luma Studio"}
    return AsyncOpenAI(
        api_key=creds.llm_api_key or "sk-no-key-required",
        base_url=creds.llm_base_url or "https://api.openai.com/v1",
        default_headers=headers,
        max_retries=0,  # we retry ourselves, visibly
        timeout=httpx.Timeout(timeout, connect=20.0),
    )


def describe_error(e: Exception) -> str:
    if isinstance(e, APIStatusError):
        body = ""
        try:
            body = json.dumps(e.body)[:600] if e.body is not None else (e.response.text or "")[:600]
        except Exception:
            pass
        return redact(f"HTTP {e.status_code}: {body or e.message}")
    return redact(f"{type(e).__name__}: {e}")


def retryable(e: Exception) -> bool:
    if isinstance(e, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code == 429 or e.status_code >= 500
    return isinstance(e, (httpx.TransportError,))


def tools_unsupported(e: Exception) -> bool:
    msg = describe_error(e).lower()
    return isinstance(e, APIStatusError) and e.status_code in (400, 404, 422) and (
        "tool" in msg and ("support" in msg or "not available" in msg or "unsupported" in msg or "no endpoints" in msg)
    )


async def with_retries(fn: Callable[[], Awaitable[Any]], on_retry: Callable[[int, float, str], None] | None = None,
                       attempts: int = 6, base: float = 1.5, cap: float = 45.0):
    """Exponential backoff with jitter for 429/5xx/connection errors."""
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            if not retryable(e) or i == attempts - 1:
                raise
            delay = min(cap, base * (2**i)) * (0.75 + random.random() * 0.5)
            if isinstance(e, APIStatusError):
                ra = e.response.headers.get("retry-after") if e.response is not None else None
                if ra:
                    try:
                        delay = max(delay, min(float(ra), 120.0))
                    except ValueError:
                        pass
            if on_retry:
                on_retry(i + 1, delay, describe_error(e))
            await asyncio.sleep(delay)


async def list_models(creds: Credentials) -> list[dict]:
    client = make_client(creds, timeout=30)
    page = await client.models.list()
    out = []
    for m in page.data:
        extra = getattr(m, "model_extra", None) or {}
        item = {"id": m.id, "name": extra.get("name") or m.id}
        if extra.get("context_length"):
            item["context_length"] = extra["context_length"]
        sp = extra.get("supported_parameters")
        if isinstance(sp, list):
            item["tools"] = "tools" in sp
        arch = extra.get("architecture") or {}
        mods = arch.get("input_modalities")
        if isinstance(mods, list):
            item["vision"] = "image" in mods
        out.append(item)
    out.sort(key=lambda x: x["id"])
    return out


def tiny_png(color=(220, 30, 30)) -> str:
    from PIL import Image

    im = Image.new("RGB", (64, 64), color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


MAGIC_TOOL = {
    "type": "function",
    "function": {
        "name": "get_magic_number",
        "description": "Returns the secret magic number for a given seed. Always call this when asked for the magic number.",
        "parameters": {"type": "object", "properties": {"seed": {"type": "integer", "description": "Any integer seed"}}, "required": ["seed"]},
    },
}


async def test_connection(creds: Credentials) -> dict:
    """auth → streaming → tool calling (real round trip) → vision (warning only)."""
    checks: list[dict] = []
    client = make_client(creds, timeout=60)
    model = creds.llm_model

    async def step(name: str, fn, warn_only: bool = False):
        t0 = time.monotonic()
        try:
            detail = await fn()
            checks.append({"name": name, "status": "ok", "detail": detail, "ms": int((time.monotonic() - t0) * 1000)})
            return True
        except Exception as e:  # noqa: BLE001
            checks.append({"name": name, "status": "warn" if warn_only else "fail", "detail": describe_error(e) if not isinstance(e, AssertionError) else str(e),
                           "ms": int((time.monotonic() - t0) * 1000)})
            return False

    async def auth():
        try:
            page = await client.models.list()
            ids = [m.id for m in page.data]
            note = f"{len(ids)} models visible"
            if ids and model and model not in ids:
                note += f" (note: '{model}' not in the list — free-text IDs are allowed)"
            return note
        except APIStatusError as e:
            if e.status_code in (404, 405):  # some local servers lack /models; verify with a tiny completion
                await client.chat.completions.create(model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=1)
                return "no /models endpoint; completion accepted"
            raise

    async def streaming():
        text, n = "", 0
        stream = await client.chat.completions.create(model=model, stream=True, max_tokens=20, temperature=0,
                                                      messages=[{"role": "user", "content": "Reply with exactly one word: pong"}])
        async for chunk in stream:
            n += 1
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                text += chunk.choices[0].delta.content
        assert n > 1 or text, "no streamed chunks received"
        return f"{n} chunks: {text.strip()[:40]!r}"

    async def tools():
        msgs = [{"role": "system", "content": "You can call tools. When asked for the magic number, call get_magic_number."},
                {"role": "user", "content": "What is the magic number for seed 7? Use the tool."}]
        r = await client.chat.completions.create(model=model, messages=msgs, tools=[MAGIC_TOOL], temperature=0, max_tokens=200)
        msg = r.choices[0].message
        assert msg.tool_calls, "the model answered without calling the tool (tool calling unsupported or ignored)"
        tc = msg.tool_calls[0]
        args = json.loads(tc.function.arguments or "{}")
        assert tc.function.name == "get_magic_number", f"unexpected tool {tc.function.name}"
        msgs.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps({"magic_number": 4242})})
        r2 = await client.chat.completions.create(model=model, messages=msgs, tools=[MAGIC_TOOL], temperature=0, max_tokens=100)
        final = r2.choices[0].message.content or ""
        assert "4242" in final.replace(",", "").replace(" ", ""), f"tool result not used in the answer: {final[:80]!r}"
        return f"called get_magic_number({args}) and used the result"

    async def vision():
        r = await client.chat.completions.create(model=model, temperature=0, max_tokens=10, messages=[{"role": "user", "content": [
            {"type": "text", "text": "What colour is this image? Answer with one word."},
            {"type": "image_url", "image_url": {"url": tiny_png()}}]}])
        ans = (r.choices[0].message.content or "").lower()
        assert "red" in ans, f"answered {ans[:40]!r} (expected red)"
        return "recognised a test image"

    ok_auth = await step("auth", auth)
    ok_stream = await step("streaming", streaming) if ok_auth else False
    ok_tools = await step("tool_calling", tools) if ok_auth else False
    ok_vision = await step("vision", vision, warn_only=True) if ok_auth else False
    for name in ("streaming", "tool_calling", "vision"):
        if not any(c["name"] == name for c in checks):
            checks.append({"name": name, "status": "skip", "detail": "skipped (auth failed)", "ms": 0})
    return {"ok": ok_auth and ok_stream and ok_tools, "checks": checks,
            "capabilities": {"streaming": ok_stream, "tools": ok_tools, "vision": ok_vision}}
