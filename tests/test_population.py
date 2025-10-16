import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Ensure API key env var before importing app
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("JOB_STORE_URL", "sqlite:///:memory:")

import app


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


if __name__ == '__main__':
    unittest.main()
