import unittest
from unittest.mock import patch

from app import _execute_mcp_plan


class _FakeContext:
    def __init__(self):
        self.logs = []
        self.counters = []

    def wait_if_paused(self):
        return None

    def update_counters(self, **kwargs):
        self.counters.append(kwargs)

    def log(self, message):
        self.logs.append(message)


class McpBreakLineExecutionTest(unittest.TestCase):
    def setUp(self):
        self.plan_data = {
            "plan": [
                {
                    "step": 4,
                    "name": "import_pdf_local",
                    "endpoint": "/pdf/api/mcp/import-local",
                    "method": "POST",
                    "payload": {},
                    "cert_id": 42,
                },
                {
                    "step": 5,
                    "name": "relocate_questions",
                    "endpoint": "/reloc/api/mcp/relocate",
                    "method": "POST",
                    "payload": {},
                    "cert_id": 42,
                },
                {
                    "step": 6,
                    "name": "fix_questions",
                    "endpoint": "/api/mcp/fix",
                    "method": "POST",
                    "payload": {},
                    "cert_id": 42,
                },
                {
                    "step": 9,
                    "name": "populate_questions",
                    "endpoint": "/api/mcp/certifications/42/populate-questions",
                    "method": "POST",
                    "payload": {},
                    "cert_id": 42,
                },
            ]
        }

    def test_break_line_skips_relocate_and_fix_but_runs_populate(self):
        context = _FakeContext()

        responses = {
            "/pdf/api/mcp/import-local": (
                {"status": "error", "message": "Aucun PDF trouvé pour code_cert AZ-900"},
                404,
            ),
            "/api/mcp/certifications/42/populate-questions": ({"status": "queued", "job_id": "job-1"}, 200),
        }

        with patch("app._call_internal", side_effect=lambda endpoint, method, payload: responses[endpoint]):
            results = _execute_mcp_plan(
                context,
                payload={"break_line": True},
                plan_data=self.plan_data,
                stop_on_error=False,
            )

        self.assertEqual(results[1]["status_code"], 204)
        self.assertEqual(results[2]["status_code"], 204)
        self.assertEqual(results[3]["status_code"], 200)

    def test_without_break_line_skips_every_following_step(self):
        context = _FakeContext()

        with patch(
            "app._call_internal",
            return_value=({"status": "error", "message": "Aucun PDF trouvé pour code_cert AZ-900"}, 404),
        ):
            results = _execute_mcp_plan(
                context,
                payload={"break_line": False},
                plan_data=self.plan_data,
                stop_on_error=False,
            )

        self.assertEqual(results[1]["status_code"], 204)
        self.assertEqual(results[2]["status_code"], 204)
        self.assertEqual(results[3]["status_code"], 204)


if __name__ == "__main__":
    unittest.main()
