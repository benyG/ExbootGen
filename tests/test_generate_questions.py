import sys
import os
import unittest
from unittest.mock import patch
import json

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import openai_api

class DummyResponse:
    def __init__(self, questions):
        self._questions = questions
        self.status_code = 200
    def json(self):
        content = json.dumps({"questions": [{"text": q} for q in self._questions]})
        return {"output_text": content}
    def raise_for_status(self):
        pass

class GenerateQuestionsBatchingTest(unittest.TestCase):
    def test_batching(self):
        call_counts = []

        def fake_post(url, headers, json=None, timeout=0):
            import re
            prompt = json["input"][0]["content"][0]["text"]
            m = re.search(r"generate (\d+) questions", prompt)
            n = int(m.group(1)) if m else 1
            call_counts.append(n)
            qs = [f"q{len(call_counts)}_{i}" for i in range(n)]
            return DummyResponse(qs)

        with patch('openai_api.requests.post', side_effect=fake_post):
            result = openai_api.generate_questions(
                provider_name='prov',
                certification='cert',
                domain='dom',
                domain_descr='descr',
                level='easy',
                q_type='qcm',
                practical='no',
                scenario_illustration_type='none',
                num_questions=12,
                batch_size=5
            )

        self.assertEqual(len(result['questions']), 12)
        self.assertEqual(call_counts, [5,5,2])

if __name__ == '__main__':
    unittest.main()
