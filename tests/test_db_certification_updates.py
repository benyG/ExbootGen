import unittest
from unittest.mock import MagicMock, patch

import db


class CertificationUpdateGuardTest(unittest.TestCase):
    def _mock_connection(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    @patch("db.get_connection")
    def test_update_certification_id_23_does_not_touch_code_or_descr2(self, mock_get_connection):
        conn, cursor = self._mock_connection()
        mock_get_connection.return_value = conn

        db.update_certification(23, "Fixed", "NEWCODE", "NEWDESCR")

        args = cursor.execute.call_args.args
        self.assertIn("SET name = %s", args[0])
        self.assertNotIn("code_cert_key", args[0])
        self.assertEqual(args[1], ("Fixed", 23))

    @patch("db.get_connection")
    def test_update_certification_non_23_updates_all_fields(self, mock_get_connection):
        conn, cursor = self._mock_connection()
        mock_get_connection.return_value = conn

        db.update_certification(24, "Cert", "CODE", "DESC")

        args = cursor.execute.call_args.args
        self.assertIn("code_cert_key", args[0])
        self.assertEqual(args[1], ("Cert", "CODE", "DESC", 24))

    @patch("db.get_connection")
    def test_update_code_cert_key_skips_course_23(self, mock_get_connection):
        conn, cursor = self._mock_connection()
        mock_get_connection.return_value = conn

        db.update_certification_code_cert_key(23, "NEW", old_code="OLD")

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("UPDATE courses SET code_cert_key" in stmt for stmt in executed))
        self.assertTrue(any("UPDATE modules SET code_cert" in stmt for stmt in executed))


if __name__ == "__main__":
    unittest.main()
