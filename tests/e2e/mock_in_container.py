"""Run the scripted director INSIDE the container (so the backend can reach it on 127.0.0.1).

    python mock_in_container.py head|tail PORT
"""
import sys

sys.path.insert(0, "/tmp/lumamock")
import uvicorn  # noqa: E402
from mock_director import restart_head, restart_tail  # noqa: E402
from mock_llm import make_app  # noqa: E402

part, port = sys.argv[1], int(sys.argv[2])
uvicorn.run(make_app(restart_head() if part == "head" else restart_tail()), host="127.0.0.1", port=port, log_level="warning")
