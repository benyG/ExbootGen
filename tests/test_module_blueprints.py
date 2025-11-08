import unittest
from unittest.mock import patch

from flask import Flask

import module_blueprints


class FakeCursor:
    def __init__(self, connection, dictionary=False):
        self.connection = connection
        self.dictionary = dictionary
        self._result = []

    def execute(self, query, params=None):
        statement = " ".join(query.split())
        if "SELECT 1 FROM courses" in statement:
            if self.connection.course_exists:
                self._result = [1]
            else:
                self._result = []
        elif "SELECT id, name, blueprint FROM modules" in statement:
            # params -> (cert_id, id1, id2, ...)
            ids = params[1:]
            rows = []
            for module_id in ids:
                module = self.connection.modules.get(module_id)
                if not module:
                    continue
                if self.dictionary:
                    rows.append(
                        {
                            "id": module_id,
                            "name": module["name"],
                            "blueprint": module["blueprint"],
                        }
                    )
                else:
                    rows.append((module_id, module["name"], module["blueprint"]))
            self._result = rows
        else:
            raise NotImplementedError(query)

    def executemany(self, query, params_seq):
        statement = " ".join(query.split())
        if "UPDATE modules SET blueprint" not in statement:
            raise NotImplementedError(query)
        for value, module_id in params_seq:
            if module_id in self.connection.modules:
                self.connection.modules[module_id]["blueprint"] = value
                self.connection.executed_updates.append((module_id, value))

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        if not self._result:
            return None
        return self._result[0]

    def close(self):
        pass


class FakeConnection:
    def __init__(self, course_exists=True):
        self.course_exists = course_exists
        self.modules = {
            1: {"name": "Domain 1", "blueprint": "Ancien plan"},
            2: {"name": "Domain 2", "blueprint": "Existing blueprint"},
        }
        self.committed = False
        self.rolled_back = False
        self.executed_updates = []

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary=dictionary)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class BulkBlueprintSaveApiTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(module_blueprints.module_blueprints_bp, url_prefix="/blueprints")
        self.client = app.test_client()

    def test_updates_and_unchanged_and_missing(self):
        connection = FakeConnection()
        payload = {
            "modules": [
                {"id": 1, "blueprint": "  Nouveau plan  "},
                {"id": 2, "blueprint": "Existing blueprint"},
                {"id": 3, "blueprint": "Inconnu"},
            ]
        }

        with patch("module_blueprints.mysql.connector.connect", return_value=connection):
            response = self.client.patch("/blueprints/api/certifications/9/modules", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["updated"], 1)
        self.assertEqual(data["unchanged"], 1)
        self.assertEqual(data["not_found"], 1)

        statuses = {item["module_id"]: item["status"] for item in data["results"]}
        self.assertEqual(statuses[1], "updated")
        self.assertEqual(statuses[2], "unchanged")
        self.assertEqual(statuses[3], "not_found")

        self.assertEqual(connection.modules[1]["blueprint"], "Nouveau plan")
        self.assertEqual(connection.modules[2]["blueprint"], "Existing blueprint")
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)

    def test_returns_404_when_certification_missing(self):
        connection = FakeConnection(course_exists=False)
        payload = {"modules": [{"id": 1, "blueprint": "Texte"}]}

        with patch("module_blueprints.mysql.connector.connect", return_value=connection):
            response = self.client.patch("/blueprints/api/certifications/5/modules", json=payload)

        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertIn("Certification introuvable", data["error"])
        self.assertEqual(connection.executed_updates, [])
        self.assertFalse(connection.committed)


if __name__ == "__main__":
    unittest.main()
