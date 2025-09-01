import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Ensure API key env var before importing app
os.environ.setdefault("OPENAI_API_KEY", "test")

import app


class ProcessDomainByDifficultyTest(unittest.TestCase):
    def test_generates_and_inserts_questions_with_secondaries(self):
        inserted = []
        generated_args = {}

        def fake_count_questions_in_category(domain_id, level, qtype, scenario_type):
            return 0

        def fake_get_domains_description_by_certif(cert_id):
            return [{"id": 1, "descr": "desc"}]

        def fake_generate_questions(**kwargs):
            generated_args.update(kwargs)
            return {"questions": [{"text": "q"}]}

        def fake_insert_questions(domain_id, questions, scenario_type):
            inserted.append((domain_id, scenario_type, questions))

        with patch('app.db.count_questions_in_category', side_effect=fake_count_questions_in_category), \
             patch('app.db.get_domains_description_by_certif', side_effect=fake_get_domains_description_by_certif), \
             patch('app.generate_questions', side_effect=fake_generate_questions), \
             patch('app.db.insert_questions', side_effect=fake_insert_questions), \
             patch('app.pick_secondary_domains', return_value=['Sec']), \
             patch('app.pause_event.wait', return_value=True), \
             patch('app.time.sleep', return_value=None):
            log = app.process_domain_by_difficulty(
                domain_id=1,
                domain_name='Dom',
                difficulty='easy',
                distribution={'qcm': {'scenario': 1}},
                provider_name='Prov',
                cert_id=10,
                cert_name='Cert',
                analysis={'case': '1'},
                progress_log=[],
                all_domain_names=['Dom', 'Sec']
            )

        self.assertEqual(generated_args['domain'], 'main domain :Dom; includes context from domains: Sec')
        self.assertEqual(inserted[0][0], 1)
        self.assertEqual(inserted[0][1], 'scenario')
        self.assertTrue(any('Secondary domains' in entry for entry in log))


if __name__ == '__main__':
    unittest.main()
