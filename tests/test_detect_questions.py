import unittest
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import routes_pdf

class DetectQuestionsCorrectAnswerTest(unittest.TestCase):
    def test_correct_answers_marked(self):
        text = (
            "NEW QUESTION 1\n"
            "What are even numbers?\n"
            "A. 1\n"
            "B. 2\n"
            "C. 3\n"
            "D. 4\n"
            "Answer: B,D\n"
            "\n"
            "NEW QUESTION 2\n"
            "Sky is blue.\n"
            "Answer: True\n"
        )
        data = routes_pdf.detect_questions(text, module_id=1)
        self.assertEqual(len(data['questions']), 2)
        q1 = data['questions'][0]
        isoks = [a['isok'] for a in q1['answers']]
        self.assertEqual(isoks, [0,1,0,1])
        q2 = data['questions'][1]
        self.assertEqual(q2['nature'], 'truefalse')
        self.assertEqual([a['isok'] for a in q2['answers']], [1,0])

if __name__ == '__main__':
    unittest.main()
