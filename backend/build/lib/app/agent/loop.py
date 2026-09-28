"""The director agent loop: streamed chat completions with (parallel) tool calls."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from openai import APIStatusError

from .. import db
from ..config import DEFAULT_PROJECT_SETTINGS
from ..events import Coalescer, bus
from ..routes_projects import project_dir
from ..routes_settings import director_prompt, load_settings, model_caps
from ..secrets_store import Credentials, redact
from . import context as C
from .llm import describe_error, make_client, retryable, tools_unsupported, with_retries
from .tools import available_tools
from .tools.base import ToolContext, ToolError, ToolOutput

log = logging.getLogger("luma.agent")


class RetryStream(Exception):
    pass


@dataclass
class _Call:
    index: int
    id: str = ""
    name: str = ""
    args: str = ""
    started: bool = False


@dataclass
class _Turn:
    text: str = ""
    reasoning: str = ""
    calls: dict = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict | None = None


def parse_args(raw: str) -> dict:
    """Parse tool arguments, repairing common LLM JSON mistakes."""
    s = (raw or "").strip()
    if not s:
        return {}
    try:
        v = json.loads(s)
    except ValueError:
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
        t = re.sub(r",\s*([}\]])", r"\1", t)
        try:
            v = json.loads(t)
        except ValueError as e:
            raise ValueError(f"{e.msg} at position {e.pos}") from None
    if isinstance(v, str):  # double-encoded
        try:
            v = json.loads(v)
        except ValueError:
            pass
    if not isinstance(v, dict):
        raise ValueError("arguments must be a JSON object")
    return v


class ElBudget:
    def __init__(self, run_id: str, limit: int):
        self.run_id, self.limit = run_id, int(limit)
        self.lock = asyncio.Lock()

    def used(self) -> int:
        with db.session() as s:
            return s.get(db.Run, self.run_id).el_chars or 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def charge(self, n: int) -> int:
        with db.session() as s:
            r = s.get(db.Run, self.run_id)
            r.el_chars = (r.el_chars or 0) + int(n)
            total = r.el_chars
        bus.publish(self.run_id, "usage", {"el_chars": total, "el_budget": self.limit})
        return total


class AgentLoop:
    def __init__(self, run_id: str, project_id: str, creds: Credentials, runner):
        self.run_id, self.project_id, self.creds, self.runner = run_id, project_id, creds, runner
        self.pdir = project_dir(project_id)
        self.settings = load_settings()
        with db.session() as s:
            p = s.get(db.Project, project_id)
            self.settings["project"] = {**DEFAULT_PROJECT_SETTINGS, **(p.settings or {})}
        caps = model_caps(creds.llm_base_url, creds.llm_model)
        self.vision = bool(caps.get("vision"))
        self.client = make_client(creds)
        self.tools = available_tools(el_enabled=creds.has_elevenlabs)
        self.coal = Coalescer(run_id)
        self.el_budget = ElBudget(run_id, self.settings.get("el_char_budget", 5000))
        self.include_usage = True
        self.turn = 0

    # ------------------------------------------------------------------------------------
    def _run_row(self) -> db.Run:
        with db.session() as s:
            return s.get(db.Run, self.run_id)

    def _persist(self, msg: dict) -> None:
        with db.session() as s:
            s.add(db.Message(run_id=self.run_id, role=msg["role"], content=redact_msg(msg)))

    def _el_status(self) -> str:
        if not self.creds.has_elevenlabs:
            return "not configured — use luma_engine.audio synthesis for all sound"
        caps = db.get_setting("elevenlabs_caps") or {}
        on = [k for k, v in caps.items() if v is True]
        return f"available ({', '.join(on) or 'untested'}); remaining character budget this run: {self.el_budget.remaining()}"

    def system_prompt(self) -> str:
        return director_prompt() + "\n\n" + C.pinned_facts(self.project_id, self.pdir, self._el_status(), self.vision)

    # ------------------------------------------------------------------------------------
    async def run(self) -> None:
        R = self.runner
        R.set_status(self.run_id, "running")
        R.finished.pop(self.run_id, None)
        t_start = time.monotonic()
        wall = float(self.settings.get("wall_clock_minutes", 60)) * 60
        max_steps = int(self.settings.get("max_steps", 120))
        while True:
            row = self._run_row()
            if row.steps >= max_steps:
                bus.publish(self.run_id, "error", {"code": "max_steps", "message": f"Stopped: reached the maximum of {max_steps} tool steps (Settings → max tool steps). Send a message to continue."})
                R.set_status(self.run_id, "stopped")
                return
            if time.monotonic() - t_start > wall:
                bus.publish(self.run_id, "error", {"code": "wall_clock", "message": f"Stopped: wall-clock limit of {wall / 60:.0f} min reached. Send a message to continue."})
                R.set_status(self.run_id, "stopped")
                return
            for text in R.take_inbox(self.run_id):
                self._persist({"role": "user", "content": f"[User message while you were working]\n{text}"})
            messages = await self._build_messages()
            try:
                turn = await self._stream(messages)
            except APIStatusError as e:
                if tools_unsupported(e):
                    bus.publish(self.run_id, "error", {"code": "no_tool_support", "message":
                        f"The model '{self.creds.llm_model}' does not support tool calling at this endpoint. Luma Studio needs a tool-capable "
                        f"model — pick another one in Settings (e.g. a recent GPT, Claude, Gemini, Qwen or Llama model with tools). Details: {describe_error(e)}"})
                    R.set_status(self.run_id, "failed", error="model does not support tool calling")
                    return
                raise
            with db.session() as s:
                r = s.get(db.Run, self.run_id)
                r.steps += 1
                if turn.usage:
                    r.prompt_tokens += int(turn.usage.get("prompt_tokens") or 0)
                    r.completion_tokens += int(turn.usage.get("completion_tokens") or 0)
                usage = {"steps": r.steps, "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens, "el_chars": r.el_chars,
                         "max_steps": max_steps}
            bus.publish(self.run_id, "usage", usage)
            calls = [c for _, c in sorted(turn.calls.items())]
            for c in calls:
                if not c.id:
                    c.id = "call_" + secrets.token_hex(8)
            assistant = {"role": "assistant", "content": turn.text or None}
            if calls:
                assistant["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.args or "{}"}} for c in calls]
            self._persist(assistant)
            if not calls:
                if turn.finish_reason == "length" and not turn.text.strip():
                    self._persist({"role": "user", "content": "Your last reply was cut off (length). Continue, using tools."})
                    continue
                R.set_status(self.run_id, "idle")
                return
            outputs = await asyncio.gather(*[self._exec(c) for c in calls])
            images: list[str] = []
            finished = False
            for c, out in zip(calls, outputs):
                self._persist({"role": "tool", "tool_call_id": c.id, "content": out.content})
                images += out.images
                finished = finished or out.finish
            if images and self.vision:
                parts = [{"type": "text", "text": "Images returned by your last tool calls (look carefully and critique):"}]
                parts += [{"type": "luma_image", "path": p} for p in images[:6]]
                self._persist({"role": "user", "content": parts})
            if finished:
                fin = R.finished.pop(self.run_id, {})
                if fin.get("summary"):
                    bus.publish(self.run_id, "text_delta", {"delta": "\n\n" + fin["summary"], "turn": self.turn, "final": True})
                R.set_status(self.run_id, "completed", summary=fin.get("summary"), outputs=fin.get("outputs"))
                return

    # ------------------------------------------------------------------------------------
    async def _build_messages(self) -> list[dict]:
        budget = int(self.settings.get("context_budget_tokens", 120000))
        history = C.load_history(self.run_id)
        summary = db.get_setting(f"summary:{self.run_id}")
        system = self.system_prompt()

        def assemble(hist):
            msgs = [{"role": "system", "content": system}]
            if summary:
                msgs.append({"role": "user", "content": f"[Summary of earlier work in this session]\n{summary}"})
                msgs.append({"role": "assistant", "content": "Understood — continuing from that state."})
            msgs += C.materialize([m for _, m in hist], self.pdir, self.vision)
            return msgs

        msgs = assemble(history)
        est = C.estimate_tokens([{**m, "content": m["content"] if isinstance(m.get("content"), str) else "x" * 3000} for m in msgs])
        if est > 0.8 * budget:
            old, recent = C.split_for_summary(history)
            if old:
                summary = await self._summarize(old, summary)
                db.set_setting(f"summary:{self.run_id}", summary)
                with db.session() as s:
                    for mid, _ in old:
                        s.get(db.Message, mid).archived = 1
                bus.publish(self.run_id, "context", {"summarized_messages": len(old), "tokens_before": est})
                msgs = assemble(recent)
        return msgs

    async def _summarize(self, old: list[tuple[int, dict]], prev: str | None) -> str:
        lines = []
        if prev:
            lines.append(f"[previous summary]\n{prev}")
        for _, m in old:
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "[image]") if p.get("type") == "text" else "[image]" for p in c)
            c = (c or "")
            if m.get("role") == "tool":
                c = c[:1500]
            calls = m.get("tool_calls")
            if calls:
                c += " TOOL CALLS: " + "; ".join(f"{t['function']['name']}({t['function']['arguments'][:400]})" for t in calls)
            lines.append(f"{m.get('role')}: {c[:6000]}")
        transcript = "\n\n".join(lines)[-200000:]

        async def call():
            r = await self.client.chat.completions.create(model=self.creds.llm_model, temperature=0.2, max_tokens=2000,
                                                          messages=[{"role": "system", "content": C.SUMMARY_PROMPT}, {"role": "user", "content": transcript}])
            return r.choices[0].message.content or ""

        return await with_retries(call, self._on_retry)

    def _on_retry(self, attempt: int, delay: float, err: str) -> None:
        bus.publish(self.run_id, "retry", {"attempt": attempt, "delay_s": round(delay, 1), "error": err, "turn": self.turn})

    async def _stream(self, messages: list[dict]) -> _Turn:
        attempts = 0
        while True:
            self.turn += 1
            try:
                return await self._stream_once(messages)
            except RetryStream:
                continue
            except Exception as e:  # noqa: BLE001
                if isinstance(e, APIStatusError) and e.status_code == 400 and self.include_usage and "stream_options" in describe_error(e):
                    self.include_usage = False
                    continue
                attempts += 1
                if not retryable(e) or attempts >= 6:
                    raise
                delay = min(45.0, 1.5 * 2 ** (attempts - 1))
                bus.publish(self.run_id, "retry", {"attempt": attempts, "discard_turn": self.turn, "delay_s": delay, "error": describe_error(e)})
                await asyncio.sleep(delay)

    async def _stream_once(self, messages: list[dict]) -> _Turn:
        kw = dict(model=self.creds.llm_model, messages=messages, tools=[t.schema() for t in self.tools.values()], stream=True,
                  temperature=float(self.settings.get("temperature", 0.7)))
        if self.include_usage:
            kw["stream_options"] = {"include_usage": True}
        stream = await self.client.chat.completions.create(**kw)
        turn = _Turn()
        try:
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    turn.usage = {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens}
                if not chunk.choices:
                    continue
                ch = chunk.choices[0]
                d = ch.delta
                if d is None:
                    continue
                extra = getattr(d, "model_extra", None) or {}
                rs = extra.get("reasoning") or extra.get("reasoning_content")
                if isinstance(rs, str) and rs:
                    turn.reasoning += rs
                    self.coal.add("text_delta", f"r{self.turn}", rs, turn=self.turn, kind="reasoning")
                if d.content:
                    turn.text += d.content
                    self.coal.add("text_delta", f"t{self.turn}", d.content, turn=self.turn)
                for tc in d.tool_calls or []:
                    idx = tc.index if tc.index is not None else len(turn.calls)
                    c = turn.calls.setdefault(idx, _Call(idx))
                    if tc.id:
                        c.id = tc.id
                    if tc.function is not None:
                        n = tc.function.name
                        if n:
                            if not c.name:
                                c.name = n
                            elif n != c.name and not c.started:  # name fragmented across chunks
                                c.name += n
                        if not c.started and c.name:
                            if not c.id:
                                c.id = "call_" + secrets.token_hex(8)
                            self.coal.flush()
                            bus.publish(self.run_id, "tool_call_start", {"id": c.id, "name": c.name, "index": idx, "turn": self.turn})
                            c.started = True
                        if tc.function.arguments:
                            c.args += tc.function.arguments
                            if c.started:
                                self.coal.add("tool_args_delta", c.id, tc.function.arguments, id=c.id)
                if ch.finish_reason:
                    turn.finish_reason = ch.finish_reason
        finally:
            self.coal.flush()
        for c in turn.calls.values():
            if not c.started:
                if not c.id:
                    c.id = "call_" + secrets.token_hex(8)
                bus.publish(self.run_id, "tool_call_start", {"id": c.id, "name": c.name or "?", "index": c.index, "turn": self.turn})
                if c.args:
                    bus.publish(self.run_id, "tool_args_delta", {"id": c.id, "delta": c.args})
        return turn

    # ------------------------------------------------------------------------------------
    async def _exec(self, c: _Call) -> ToolOutput:
        t0 = time.monotonic()
        ctx = ToolContext(self.run_id, self.project_id, self.pdir, self.creds, self.settings, self.vision, self.runner,
                          tool_call_id=c.id, coalescer=self.coal, el_budget=self.el_budget)
        tool = self.tools.get(c.name)
        try:
            if tool is None:
                raise ToolError(f"unknown tool '{c.name}'. Available tools: {', '.join(sorted(self.tools))}")
            try:
                args = parse_args(c.args)
            except ValueError as e:
                raise ToolError(f"your arguments for {c.name} were not valid JSON ({e}). Re-send the call with a valid JSON object "
                                f"matching the schema. Received: {c.args[:300]!r}") from None
            missing = [k for k in tool.parameters.get("required", []) if k not in args]
            if missing:
                raise ToolError(f"missing required argument(s) {missing} for {c.name}")
            out = await tool.handler(ctx, args)
        except ToolError as e:
            out = ToolOutput(f"Error: {e}", "error")
        except asyncio.CancelledError:
            self.coal.flush()
            bus.publish(self.run_id, "tool_result", {"id": c.id, "name": c.name, "status": "cancelled", "seconds": round(time.monotonic() - t0, 2)})
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", c.name)
            out = ToolOutput(f"Error: {type(e).__name__}: {redact(str(e))}", "error")
        self.coal.flush()
        bus.publish(self.run_id, "tool_result", {"id": c.id, "name": c.name, "status": out.status, "seconds": round(time.monotonic() - t0, 2),
                                                 "output": out.content[:20000], **({"ui": out.ui} if out.ui else {})})
        return out


def redact_msg(msg: dict) -> dict:
    from ..secrets_store import redact_obj

    return redact_obj(msg)
