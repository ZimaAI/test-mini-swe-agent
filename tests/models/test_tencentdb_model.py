import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from minisweagent.models import get_model
from minisweagent.models.tencentdb_model import TencentDBModel


@pytest.fixture
def proxy_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            response = json.dumps(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "Inspect the code",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old = os.environ.get("TEST_TDAI_KEY")
        os.environ["TEST_TDAI_KEY"] = "test-memory-key"
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()
            if old is None:
                del os.environ["TEST_TDAI_KEY"]
            else:
                os.environ["TEST_TDAI_KEY"] = old


@pytest.mark.parametrize(("enabled", "mode"), [(True, "enabled"), (False, "disabled")])
def test_real_http_protocol_and_trajectory(proxy_server, enabled, mode):
    base, received = proxy_server
    model = get_model(
        config={
            "model_class": "tencentdb",
            "model_name": "openai/gpt-4o-mini",
            "proxy_url": base,
            "api_key_env": "TEST_TDAI_KEY",
            "knowledge_enabled": enabled,
            "team_id": "team-test",
            "agent_id": "agent-test",
            "knowledge_url": "http://host.docker.internal:8424/v3",
            "model_kwargs": {"temperature": 0, "api_base": "http://unused.invalid", "api_key": "old-secret"},
        }
    )
    messages = [model.format_message(role="system", content="Use bash"), {"role": "user", "content": "Fix the bug"}]
    response = model.query(messages)
    observations = model.format_observation_messages(
        response, [{"output": "/testbed", "returncode": 0, "exception_info": ""}]
    )
    model.query(messages + [response, *observations])
    assert response["extra"]["actions"] == [{"command": "pwd", "tool_call_id": "call-1"}]
    assert response["extra"]["cost"] > 0
    path, headers, body = received[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert path == "/mini-swe-agent/default/v1/chat/completions"
    assert headers["authorization"] == "Bearer test-memory-key"
    assert headers["x-tdai-knowledge"] == mode
    assert ("x-team-id" in headers) is enabled
    assert ("x-tdai-knowledge-url" in headers) is enabled
    assert body["messages"] == messages
    assert body["tools"][0]["function"]["name"] == "bash"
    assert body["temperature"] == 0
    assert received[1][2]["messages"][-1]["tool_call_id"] == "call-1"
    assert all("extra" not in msg for msg in received[1][2]["messages"])
    assert headers["x-conversation-id"] == {k.lower(): v for k, v in received[1][1].items()}["x-conversation-id"]
    serialized = model.serialize()
    assert serialized["info"]["tencentdb"] == {"session_id": model.session_id, "knowledge_enabled": enabled}
    assert "test-memory-key" not in json.dumps(serialized)
    assert "old-secret" not in json.dumps(serialized)


def test_sessions_are_separate_per_instance_and_reused_agent_run(proxy_server):
    base, _ = proxy_server
    config = {"model_name": "openai/gpt-4o-mini", "proxy_url": base, "api_key_env": "TEST_TDAI_KEY"}
    first, second = TencentDBModel(**config), TencentDBModel(**config)
    assert first.session_id != second.session_id
    old = first.session_id
    first.format_message(role="system", content="New task")
    assert first.session_id != old
    resumed = TencentDBModel(**config, session_id="resume-test")
    resumed.format_message(role="system", content="Resume")
    assert resumed.session_id == "resume-test"


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"knowledge_enabled": True}, "Knowledge requires"),
        ({"proxy_url": "file:///private"}, "HTTP"),
        ({"knowledge_url": "http://user:secret@localhost/v3"}, "credentials"),
        ({"space_id": ""}, "must not be empty"),
        ({"api_key_env": "MISSING_TEST_TDAI_KEY"}, "Set MISSING_TEST_TDAI_KEY"),
    ],
)
def test_invalid_configuration_fails_before_requests(proxy_server, options, error):
    base, received = proxy_server
    with pytest.raises(ValueError, match=error):
        TencentDBModel(
            **({"model_name": "openai/gpt-4o-mini", "proxy_url": base, "api_key_env": "TEST_TDAI_KEY"} | options)
        )
    assert received == []
