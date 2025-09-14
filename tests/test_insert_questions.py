import unittest
from unittest.mock import patch
import json
import db
import mysql.connector

class FakeCursor:
    def __init__(self):
        self.lastrowid = 0
        self.questions = set()
        self.answers = {}
        self.quest_ans = set()
        self._select_res = None
    def execute(self, query, params):
        q = query.strip()
        if q.startswith("INSERT INTO questions"):
            text = params[0]
            if text in self.questions:
                raise mysql.connector.errors.IntegrityError(msg="dup", errno=1062, sqlstate="23000")
            self.questions.add(text)
            self.lastrowid = len(self.questions)
        elif q.startswith("INSERT INTO answers"):
            text = params[0]
            if text in self.answers:
                raise mysql.connector.errors.IntegrityError(msg="dup", errno=1062, sqlstate="23000")
            ans_id = len(self.answers) + 1
            self.answers[text] = ans_id
            self.lastrowid = ans_id
        elif q.startswith("SELECT id FROM answers"):
            ans_id = self.answers.get(params[0])
            self._select_res = (ans_id,)
        elif q.startswith("INSERT INTO quest_ans"):
            pair = (params[0], params[1])
            if pair in self.quest_ans:
                raise mysql.connector.errors.IntegrityError(msg="dup", errno=1062, sqlstate="23000")
            self.quest_ans.add(pair)
        else:
            raise NotImplementedError(query)
    def fetchone(self):
        return self._select_res
    def close(self):
        pass

class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
    def cursor(self):
        return self.cursor_obj
    def commit(self):
        pass
    def rollback(self):
        pass
    def close(self):
        pass

class InsertQuestionsDedupTest(unittest.TestCase):
    def test_skip_and_reuse(self):
        questions_json = {
            "questions": [
                {
                    "text": "Q1",
                    "level": "medium",
                    "nature": "qcm",
                    "answers": [
                        {"value": "A1", "isok": 1},
                        {"value": "A2"}
                    ]
                },
                {
                    "text": "Q2",
                    "level": "medium",
                    "nature": "qcm",
                    "answers": [
                        {"value": "A1"},
                        {"value": "A3"}
                    ]
                },
                {
                    "text": "Q1",
                    "level": "medium",
                    "nature": "qcm",
                    "answers": [
                        {"value": "A4"},
                        {"value": "A5"}
                    ]
                }
            ]
        }

        with patch('db.get_connection', return_value=FakeConnection()):
            stats = db.insert_questions(1, questions_json, "no")

        self.assertEqual(stats['imported_questions'], 2)
        self.assertEqual(stats['skipped_questions'], 1)
        self.assertEqual(stats['imported_answers'], 3)
        self.assertEqual(stats['reused_answers'], 1)

    def test_duplicate_answers_same_question(self):
        questions_json = {
            "questions": [
                {
                    "text": "Q1",
                    "level": "medium",
                    "nature": "qcm",
                    "answers": [
                        {"value": "A1", "isok": 1},
                        {"value": "A1"}
                    ]
                },
                {
                    "text": "Q2",
                    "level": "medium",
                    "nature": "qcm",
                    "answers": [
                        {"value": "A2"}
                    ]
                }
            ]
        }

        with patch('db.get_connection', return_value=FakeConnection()):
            stats = db.insert_questions(1, questions_json, "no")

        self.assertEqual(stats['imported_questions'], 2)
        self.assertEqual(stats['skipped_questions'], 0)
        self.assertEqual(stats['imported_answers'], 2)
        self.assertEqual(stats['reused_answers'], 1)

if __name__ == '__main__':
    unittest.main()
