"""A scripted OpenAI-compatible server for tests (chat completions, streaming, tools).

Each POST /v1/chat/completions pops the next scripted turn:
  {"text": "...", "tool_calls": [{"name": ..., "arguments": "<raw json string>"}], "status": 429}
Streaming splits text and argument strings into small chunks to exercise delta handling.
Requests are recorded in ``state["requests"]`` for assertions.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


def make_app(script: list[dict], state: dict | None = None) -> FastAPI:
    app = FastAPI()
    state = state if state is not None else {}
    state.setdefault("requests", [])
    state["script"] = list(script)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "mock-director", "object": "model", "created": 0, "owned_by": "test"}]}

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        state["requests"].append(body)
        if not state["script"]:
            turn = {"text": "(script exhausted)"}
        else:
            turn = state["script"].pop(0)
        if turn.get("status"):
            return JSONResponse({"error": {"message": turn.get("error", "scripted error"), "type": "test"}}, status_code=turn["status"])
        if turn.get("delay"):
            await asyncio.sleep(turn["delay"])
        cid = "chatcmpl-" + uuid.uuid4().hex[:8]
        calls = turn.get("tool_calls") or []
        if not body.get("stream"):
            msg = {"role": "assistant", "content": turn.get("text") or None}
            if calls:
                msg["tool_calls"] = [{"id": c.get("id") or f"call_{i}_{uuid.uuid4().hex[:6]}", "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"]}} for i, c in enumerate(calls)]
            return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": body["model"],
                    "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}

        async def gen():
            def chunk(delta, finish=None, usage=None):
                d = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": body["model"],
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                if usage:
                    d["usage"] = usage
                    d["choices"] = []
                return f"data: {json.dumps(d)}\n\n"

            yield chunk({"role": "assistant", "content": ""})
            if turn.get("reasoning"):
                yield chunk({"reasoning": turn["reasoning"]})
            text = turn.get("text") or ""
            for i in range(0, len(text), 7):
                yield chunk({"content": text[i : i + 7]})
                await asyncio.sleep(0)
            for i, c in enumerate(calls):
                tid = c.get("id") or f"call_{i}_{uuid.uuid4().hex[:6]}"
                yield chunk({"tool_calls": [{"index": i, "id": tid, "type": "function", "function": {"name": c["name"], "arguments": ""}}]})
                args = c["arguments"]
                for j in range(0, len(args), 11):
                    yield chunk({"tool_calls": [{"index": i, "function": {"arguments": args[j : j + 11]}}]})
            yield chunk({}, "tool_calls" if calls else "stop")
            if (body.get("stream_options") or {}).get("include_usage"):
                yield chunk({}, usage={"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050})
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


class MockServer:
    def __init__(self, script: list[dict], port: int = 0):
        import socket

        self.state: dict = {}
        self.app = make_app(script, self.state)
        if not port:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def push(self, *turns):
        self.state["script"].extend(turns)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)


def call(_tool: str, **args) -> dict:
    return {"name": _tool, "arguments": json.dumps(args)}


if __name__ == "__main__":  # manual e2e: python tests/mock_llm.py script.json PORT
    import sys

    script = json.load(open(sys.argv[1]))
    uvicorn.run(make_app(script), host="127.0.0.1", port=int(sys.argv[2]))
