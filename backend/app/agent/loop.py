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
from . import budget as B
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
        with db.session() as s:
            self.limits = (s.get(db.Run, run_id).limits or None)
        self.is_child = bool(self.limits)
        if self.is_child:  # sub-agent: its own step / cost / ElevenLabs budget
            self.settings["max_steps"] = self.limits["max_steps"]
            self.settings["max_cost_usd"] = self.limits["max_cost_usd"]
            self.settings["el_char_budget"] = self.limits["max_el_chars"]
        caps = model_caps(creds.llm_base_url, creds.llm_model)
        self.vision = bool(caps.get("vision"))
        self.client = make_client(creds)
        if self.is_child:
            self.base_tools = available_tools(el_enabled=creds.has_elevenlabs, project_settings={**self.settings["project"], "subagent_child": True},
                                              only=self.limits["tools"])
        else:
            self.base_tools = available_tools(el_enabled=creds.has_elevenlabs, project_settings=self.settings["project"])
        self.tools = dict(self.base_tools)
        self._skills_brief: str | None = None
        self.coal = Coalescer(run_id)
        self.el_budget = ElBudget(run_id, self.settings.get("el_char_budget", 5000))
        self.include_usage = True
        self.turn = 0
        self.pricing = B.pricing(creds.llm_base_url, creds.llm_model)

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
        base = director_prompt() + "\n\n" + C.pinned_facts(self.project_id, self.pdir, self._el_status(), self.vision) + \
            C.pinned_long_job(self.run_id, self.project_id, self.pdir) + self._toolbox_brief()
        if self.is_child:
            base += ("\n\n## You are a sub-agent\nThe director delegated ONE task to you (first user message). You cannot talk to the user, "
                     "deliver or spawn agents; you share the workspace, so write into your own subfolder of work/ unless told otherwise. "
                     "End with subagent_report (structured result + summary + artifact ids).")
        return base

    # ------------------------------------------------------------------------------------ toolbox
    def _focus_text(self) -> str:
        """What the agent is working on now: the in-progress todo, else the latest user message."""
        from .. import plan

        for t in plan.todos(self.run_id):
            if t["status"] == "in_progress":
                return f"{t['title']} {t['detail']} {t['acceptance_criteria']}"
        with db.session() as s:
            from sqlalchemy import select

            m = s.scalars(select(db.Message).where(db.Message.run_id == self.run_id, db.Message.role == "user").order_by(db.Message.id.desc()).limit(1)).first()
            c = (m.content or {}).get("content") if m else ""
        return c if isinstance(c, str) else ""

    def _refresh_tools(self) -> dict:
        """Built-ins + the toolbox tools for this step (the LLM sees a tool registered last step)."""
        from .tools.toolbox_tools import toolbox_function_tools

        tools = dict(self.base_tools)
        try:
            if self.is_child:
                allowed = set(self.limits.get("tools") or [])
                extra = toolbox_function_tools(self.project_id, self.run_id, "", len(allowed), allowed)
                extra = {n: t for n, t in extra.items() if n in allowed}
            else:
                extra = toolbox_function_tools(self.project_id, self.run_id, self._focus_text(), int(self.settings.get("toolbox_max_tools", 15)),
                                               self.runner.toolbox_pinned.get(self.run_id, set()))
        except Exception:  # noqa: BLE001 — a broken toolbox index never stops the director
            log.exception("toolbox tool selection failed")
            extra = {}
        for n, t in extra.items():
            tools.setdefault(n, t)
        return tools

    def _toolbox_brief(self) -> str:
        """Relevant skills (FTS over the brief, assets and request — computed once per run) + toolbox size."""
        if self.is_child:
            return ""
        from ..toolbox import skills as K
        from ..toolbox import store

        if self._skills_brief is None:
            try:
                with db.session() as s:
                    from sqlalchemy import select

                    p = s.get(db.Project, self.project_id)
                    names = " ".join(a.filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
                                     for a in s.scalars(select(db.Asset).where(db.Asset.project_id == self.project_id)))
                    brief = f"{p.brief if p else ''} {names} {self._focus_text()}"
                self._skills_brief = K.relevant_summary(brief)
            except Exception:  # noqa: BLE001
                log.exception("skill summary failed")
                self._skills_brief = ""
        n_tools = len(store.callable_rows(self.project_id))
        listed = len([n for n in self.tools if n not in self.base_tools])
        tb = (f"\n\n## Toolbox\n{n_tools} registered toolbox tool(s) in reach ({listed} listed as functions this step; the rest via "
              f"toolbox_search → toolbox_call). Reuse before build.") if n_tools else ""
        return self._skills_brief + tb

    async def invoke(self, name: str, params: dict, parent: ToolContext):
        """``ctx.call_tool`` RPC from a running toolbox tool (budgeted, gated, no keys)."""
        from .tools.toolbox_tools import invoke

        return await invoke(self, name, params, parent)

    # ------------------------------------------------------------------------------------
    async def run(self) -> None:
        R = self.runner
        R.set_status(self.run_id, "running")
        R.finished.pop(self.run_id, None)
        R.started_at[self.run_id] = time.time()
        t_start = time.monotonic()
        budget = self.budget_status()
        bus.publish(self.run_id, "budget", budget)
        stop = self._over_cap(budget)
        if stop:
            bus.publish(self.run_id, "error", {"code": "budget", "message": stop})
            R.set_status(self.run_id, "stopped")
            return
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
            for note in R.take_notes(self.run_id):
                self._persist({"role": "user", "content": f"[System note]\n{note}"})
            self.tools = self._refresh_tools()
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
                    r.cost_usd = (r.cost_usd or 0.0) + B.step_cost(self.pricing, turn.usage)
                usage = {"steps": r.steps, "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens, "el_chars": r.el_chars,
                         "max_steps": max_steps, "cost_usd": round(r.cost_usd or 0.0, 4)}
            bus.publish(self.run_id, "usage", usage)
            budget = self.budget_status()
            bus.publish(self.run_id, "budget", budget)
            stop = self._over_cap(budget)
            if stop:
                bus.publish(self.run_id, "error", {"code": "budget", "message": stop})
                R.set_status(self.run_id, "stopped")
                return
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
        forced = self.runner.compact_requests.pop(self.run_id, None)
        if est > C.COMPACT_AT * budget or forced:
            old, recent = C.split_for_summary(history, keep_last=6 if forced else 14)
            if old:
                summary = await self._summarize(old, summary)
                db.set_setting(f"summary:{self.run_id}", summary)
                with db.session() as s:
                    for mid, _ in old:
                        s.get(db.Message, mid).archived = 1
                msgs = assemble(recent)
                after = C.estimate_tokens([{**m, "content": m["content"] if isinstance(m.get("content"), str) else "x" * 3000} for m in msgs])
                bus.publish(self.run_id, "compaction", {"summarized_messages": len(old), "tokens_before": est, "tokens_after": after,
                                                        "reason": forced or f"auto at {int(C.COMPACT_AT * 100)}% of the context budget",
                                                        "kept": C.PINNED_KINDS})
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
                    cost = (getattr(u, "model_extra", None) or {}).get("cost")
                    if isinstance(cost, (int, float)):
                        turn.usage["cost"] = float(cost)
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

    def budget_status(self) -> dict:
        return B.status(self._run_row(), self.settings, self.runner.started_at.get(self.run_id), self.creds.llm_base_url, self.creds.llm_model)

    def _over_cap(self, b: dict) -> str | None:
        if b["max_cost_usd"] and b["cost_usd"] >= b["max_cost_usd"]:
            return (f"Stopped gracefully: the run reached its cost cap (${b['cost_usd']:.2f} of ${b['max_cost_usd']:.2f}, Settings → Limits). "
                    "The workspace and plan are saved — raise the cap and send a message to continue.")
        if b["max_tokens"] and b["tokens"] >= b["max_tokens"]:
            return (f"Stopped gracefully: the run reached its token cap ({b['tokens']:,} of {b['max_tokens']:,}, Settings → Limits). "
                    "Raise the cap and send a message to continue.")
        return None

    def _gate(self, name: str, args: dict) -> None:
        """Expensive actions need the user's sign-off (request_approval) unless the project is on Autopilot."""
        st = self.settings.get("project", {})
        if st.get("autopilot") or self.is_child:  # a sub-agent runs under its parent's approved spawn
            return
        reasons: list[str] = []
        since = B.last_user_message_at(self.run_id)
        if name == "render_final":
            if int(st.get("width", 0)) >= 3840:
                reasons.append("a 4K final render")
            est = B.estimate_render_minutes(self.settings, self.runner.render_rate.get(self.run_id))
            if st.get("approval_render_minutes") and est > float(st["approval_render_minutes"]):
                reasons.append(f"a final render estimated at ~{est:.0f} min (threshold {st['approval_render_minutes']} min)")
        if name in B.EL_TOOLS:
            n = len(str(args.get(B.EL_TOOLS[name]) or ""))
            if st.get("approval_el_chars") and n > int(st["approval_el_chars"]):
                reasons.append(f"an ElevenLabs request of {n} characters (threshold {st['approval_el_chars']})")
        if name in B.EXPENSIVE or name in B.EL_TOOLS:
            b = self.budget_status()
            thr = float(st.get("approval_cost_usd") or 0)
            if thr and b["cost_usd"] > thr:
                crossed = self.runner.crossed.setdefault((self.run_id, "cost"), time.time())
                since = max(since, crossed)
                reasons.append(f"continuing past ${thr:.2f} of model cost (now ${b['cost_usd']:.2f})")
            mins = float(st.get("approval_run_minutes") or 0)
            if mins and b["elapsed_s"] > mins * 60:
                crossed = self.runner.crossed.setdefault((self.run_id, "time"), time.time())
                since = max(since, crossed)
                reasons.append(f"continuing a run past {mins:.0f} minutes")
        if reasons and not B.approved_since(self.run_id, since):
            raise ToolError("Approval needed before " + "; ".join(reasons) + ". Present the drafts (present_video / present_storyboard …), "
                            "then call request_approval with their artifact ids and a summary of the time/cost. (Project Autopilot skips this.)")

    def _toolbox_tool(self, name: str):
        """A registered toolbox tool that was not among this step's listed functions is still callable by name."""
        from ..toolbox import store
        from .tools.base import Tool
        from .tools.toolbox_tools import call_toolbox_tool

        if name in self.base_tools or (self.is_child and name not in set(self.limits.get("tools") or [])):
            return None
        try:  # any status: a disabled / failing tool answers with why it cannot run, not "unknown tool"
            row = store.effective(self.project_id).get(name)
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None

        async def handler(ctx, args):
            return await call_toolbox_tool(ctx, name, args)

        return Tool(name, row.description, (row.manifest or {}).get("parameters") or {"type": "object"}, handler)

    def _check_plan_rule(self, name: str) -> None:
        """Long requests must start with a plan: after `plan_required_after_steps` model steps without todos,
        only planning / read-only tools are accepted."""
        from .. import plan

        limit = int(self.settings.get("plan_required_after_steps", 5) or 0)
        if not limit or name in PLAN_EXEMPT:
            return
        if self._run_row().steps <= limit or plan.todos(self.run_id):
            return
        raise ToolError(f"Plan first: this request is taking more than {limit} steps and there is no plan. Call todo_write with phases, "
                        f"tasks and acceptance criteria, then continue.")

    # ------------------------------------------------------------------------------------
    async def _exec(self, c: _Call) -> ToolOutput:
        t0 = time.monotonic()
        ctx = ToolContext(self.run_id, self.project_id, self.pdir, self.creds, self.settings, self.vision, self.runner,
                          tool_call_id=c.id, coalescer=self.coal, el_budget=self.el_budget, agent=self)
        tool = self.tools.get(c.name) or self._toolbox_tool(c.name)
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
            self._check_plan_rule(c.name)
            self._gate(c.name, args)
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


PLAN_EXEMPT = {"toolbox_search", "toolbox_list", "toolbox_read", "skill_search", "skill_read", "todo_write", "todo_add", "todo_list", "ask_user", "list_files", "read_file", "inspect_asset", "view_image", "memory_read",
               "memory_search", "memory_write", "notes_append", "notes_read", "checkpoint_list", "budget_status", "context_compact",
               "media_probe", "notify", "report_progress", "finish"}


def redact_msg(msg: dict) -> dict:
    from ..secrets_store import redact_obj

    return redact_obj(msg)
