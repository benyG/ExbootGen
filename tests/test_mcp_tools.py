import os
import shutil
import sys

from flask import jsonify

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("JOB_STORE_URL", "sqlite:///test_job_store.db")
os.environ.setdefault("MCP_API_TOKEN", "test-token")

JOB_CACHE_DIR = os.path.join(os.path.dirname(__file__), "mcp_job_cache")
shutil.rmtree(JOB_CACHE_DIR, ignore_errors=True)
os.makedirs(JOB_CACHE_DIR, exist_ok=True)
os.environ.setdefault("JOB_STATUS_CACHE_DIR", JOB_CACHE_DIR)

import app


MCP_HEADER = {"X-MCP-Token": os.environ["MCP_API_TOKEN"]}


def _route_for_tool(endpoint: str) -> str:
    return endpoint.replace("{cert_id}", "<int:cert_id>")


def _endpoint_name_for_route(route: str) -> str | None:
    for rule in app.app.url_map.iter_rules():
        if rule.rule == route:
            return rule.endpoint
    return None


def test_mcp_tools_list_and_routes():
    client = app.app.test_client()
    response = client.get("/api/mcp/tools", headers=MCP_HEADER)
    assert response.status_code == 200

    payload = response.get_json()
    tool_names = {tool["name"] for tool in payload["tools"]}
    assert tool_names == set(app.MCP_TOOLS.keys())

    for config in app.MCP_TOOLS.values():
        route = _route_for_tool(config["endpoint"])
        assert _endpoint_name_for_route(route) is not None


def test_mcp_call_executes_each_tool(monkeypatch):
    client = app.app.test_client()

    for tool_name, config in app.MCP_TOOLS.items():
        route = _route_for_tool(config["endpoint"])
        endpoint_name = _endpoint_name_for_route(route)
        assert endpoint_name is not None

        def _make_stubbed_response(name):
            def _stubbed_response(**kwargs):
                return jsonify({"status": "ok", "tool": name, "kwargs": kwargs})

            return _stubbed_response

        monkeypatch.setitem(
            app.app.view_functions,
            endpoint_name,
            _make_stubbed_response(tool_name),
        )

        tool_payload = {}
        if "{cert_id}" in config["endpoint"]:
            tool_payload["cert_id"] = 123

        response = client.post(
            "/api/mcp/call",
            json={"tool": tool_name, "payload": tool_payload},
            headers=MCP_HEADER,
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert payload["result"]["tool"] == tool_name
        assert payload["status_code"] == 200
