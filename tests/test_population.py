import os
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Ensure API key env var before importing app
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("JOB_STORE_URL", "sqlite:///test_job_store.db")

JOB_CACHE_DIR = os.path.join(os.path.dirname(__file__), "job_cache_data")
shutil.rmtree(JOB_CACHE_DIR, ignore_errors=True)
os.makedirs(JOB_CACHE_DIR, exist_ok=True)
os.environ.setdefault("JOB_STATUS_CACHE_DIR", JOB_CACHE_DIR)

import app
import jobs


class DummyContext:
    def __init__(self):
        self.logs = []
        self.counters = {}

    def log(self, message):
        self.logs.append(message)

    def update_counters(self, **kwargs):  # pragma: no cover - helper for completeness
        self.counters.update(kwargs)

    def wait_if_paused(self):
        return


class ProcessDomainByDifficultyTest(unittest.TestCase):
    def test_generates_and_inserts_questions_with_secondaries(self):
        inserted = []
        generated_args = {}

        def fake_count_questions_in_category(domain_id, level, qtype, scenario_type):
            return 0

        def fake_generate_questions(**kwargs):
            generated_args.update(kwargs)
            return {"questions": [{"text": "q"}]}

        def fake_insert_questions(domain_id, questions, scenario_type):
            inserted.append((domain_id, scenario_type, questions))

        with patch('app.db.count_questions_in_category', side_effect=fake_count_questions_in_category), \
             patch('app.generate_questions', side_effect=fake_generate_questions), \
             patch('app.db.insert_questions', side_effect=fake_insert_questions), \
             patch('app.pick_secondary_domains', return_value=['Sec']), \
             patch('app.time.sleep', return_value=None):
            context = DummyContext()
            app.process_domain_by_difficulty(
                context,
                domain_id=1,
                domain_name='Dom',
                difficulty='easy',
                distribution={'qcm': {'scenario': 1}},
                provider_name='Prov',
                cert_name='Cert',
                analysis={'case': '1'},
                all_domain_names=['Dom', 'Sec'],
                domain_descriptions={1: 'desc'}
            )

        self.assertEqual(generated_args['domain'], 'main domain :Dom; includes context from domains: Sec')
        self.assertEqual(inserted[0][0], 1)
        self.assertEqual(inserted[0][1], 'scenario')
        self.assertTrue(any('Secondary domains' in entry for entry in context.logs))


    def test_uses_progress_cache_when_available(self):
        class DummyProgress:
            def __init__(self):
                self.counts = {('easy', 'qcm', 'scenario'): 5}
                self.total = 5
                self.recorded = []

            def category_total(self, difficulty, qtype, scenario):
                return self.counts.get((difficulty, qtype, scenario), 0)

            def record_insertion(self, difficulty, qtype, scenario, imported):
                self.recorded.append((difficulty, qtype, scenario, imported))
                self.counts[(difficulty, qtype, scenario)] = self.counts.get(
                    (difficulty, qtype, scenario), 0
                ) + imported
                self.total += imported

            def total_questions(self):
                return self.total

        context = DummyContext()
        progress = DummyProgress()

        with patch('app.db.count_questions_in_category') as count_mock:
            inserted = app.process_domain_by_difficulty(
                context,
                domain_id=1,
                domain_name='Dom',
                difficulty='easy',
                distribution={'qcm': {'scenario': 1}},
                provider_name='Prov',
                cert_name='Cert',
                analysis={},
                all_domain_names=['Dom'],
                domain_descriptions={1: 'desc'},
                progress=progress,
            )

        self.assertEqual(inserted, 0)
        count_mock.assert_not_called()

class JobStatusFallbackTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def tearDown(self) -> None:
        # Ensure the in-memory cache is cleared between tests to exercise disk reloads.
        with jobs._JOB_CACHE._lock:
            jobs._JOB_CACHE._jobs.clear()
        return super().tearDown()

    def test_returns_cached_status_when_store_unavailable(self):
        job_id = uuid.uuid4().hex
        app.initialise_job(
            app.job_store,
            job_id=job_id,
            description="populate-certification",
            metadata={},
        )
        context = app.JobContext(app.job_store, job_id)
        context.set_status("running")
        context.log("Started")
        context.update_counters(progress=1)

        with patch.object(app.job_store, "get_status", side_effect=app.JobStoreError("down")):
            response = self.client.get(f"/populate/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertIn("Started", payload["log"])
        self.assertEqual(payload["counters"].get("progress"), 1)

    def test_returns_cached_status_when_store_reports_missing_job(self):
        job_id = uuid.uuid4().hex
        app.initialise_job(
            app.job_store,
            job_id=job_id,
            description="populate-certification",
            metadata={},
        )
        context = app.JobContext(app.job_store, job_id)
        context.set_status("completed")

        with patch.object(app.job_store, "get_status", return_value=None):
            response = self.client.get(f"/fix/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "completed")

    def test_returns_error_when_no_cache_available(self):
        unknown_id = uuid.uuid4().hex
        with patch.object(app.job_store, "get_status", side_effect=app.JobStoreError("down")):
            response = self.client.get(f"/populate/status/{unknown_id}")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error"], "job store unavailable")

    def test_uses_disk_cache_when_memory_missing(self):
        job_id = uuid.uuid4().hex
        app.initialise_job(
            app.job_store,
            job_id=job_id,
            description="populate-certification",
            metadata={},
        )
        context = app.JobContext(app.job_store, job_id)
        context.set_status("running")
        context.log("Started")
        context.update_counters(progress=1)

        # Simulate a fresh process by clearing the in-memory cache only.
        with jobs._JOB_CACHE._lock:
            jobs._JOB_CACHE._jobs.clear()

        with patch.object(app.job_store, "get_status", side_effect=app.JobStoreError("down")):
            response = self.client.get(f"/populate/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertIn("Started", payload["log"])
        self.assertEqual(payload["counters"].get("progress"), 1)


class PopulateQueueDisableTest(unittest.TestCase):
    def setUp(self):
        app._reset_task_queue_state_for_testing()
        self.client = app.app.test_client()

    def tearDown(self) -> None:
        app._reset_task_queue_state_for_testing()
        with jobs._JOB_CACHE._lock:
            jobs._JOB_CACHE._jobs.clear()
        return super().tearDown()

    def test_queue_disabled_after_operational_error(self):
        with patch.object(
            app.run_population_job,
            "apply_async",
            side_effect=app.OperationalError("max clients"),
        ), patch.object(app, "_run_population_thread") as run_thread:
            run_thread.return_value = None
            response = self.client.post(
                "/populate/process",
                data={"provider_id": "1", "cert_id": "2"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["mode"], "local")
        self.assertTrue(app._is_task_queue_disabled())
        run_thread.assert_called_once()

        with patch.object(app.run_population_job, "apply_async") as apply_mock, \
             patch.object(app, "_run_population_thread") as run_thread_again:
            run_thread_again.return_value = None
            response_again = self.client.post(
                "/populate/process",
                data={"provider_id": "1", "cert_id": "2"},
            )

        apply_mock.assert_not_called()
        run_thread_again.assert_called_once()
        self.assertEqual(response_again.status_code, 200)
        self.assertEqual(response_again.get_json()["mode"], "local")


class PopulateMcpQuestionsTest(unittest.TestCase):
    def setUp(self):
        app._reset_task_queue_state_for_testing()
        self.client = app.app.test_client()

    def tearDown(self) -> None:
        app._reset_task_queue_state_for_testing()
        with jobs._JOB_CACHE._lock:
            jobs._JOB_CACHE._jobs.clear()
        return super().tearDown()

    def test_mcp_populate_enqueues_job(self):
        os.environ["MCP_API_TOKEN"] = "test-token"
        with patch.object(app.run_population_mcp_job, "apply_async") as apply_mock:
            response = self.client.post(
                "/api/mcp/certifications/2/populate-questions",
                json={"provider_id": 1},
                headers={"X-MCP-Token": "test-token"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "queued")
        self.assertIn("job_id", payload)
        apply_mock.assert_called_once()

if __name__ == '__main__':
    unittest.main()
