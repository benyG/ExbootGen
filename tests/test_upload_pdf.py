import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import app

class UploadPdfPathValidationTest(unittest.TestCase):
    def test_rejects_disallowed_file_path(self):
        with app.app.test_client() as client, \
             patch('pdf_importer.extract_text_from_pdf', return_value=''), \
             patch('pdf_importer.detect_questions', return_value={}):
            resp = client.post('/pdf/upload-pdf', data={'module_id': '1', 'file_path': '/etc/passwd'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json.get('status'), 'error')

if __name__ == '__main__':
    unittest.main()
