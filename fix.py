from flask import Blueprint, render_template, request
import db
from openai_api import assign_correct_answers, generate_missing_answers

fix_bp = Blueprint('fix', __name__)

@fix_bp.route('/', methods=['GET', 'POST'])
def index():
    providers = db.get_providers()
    message = None
    if request.method == 'POST':
        provider_id = int(request.form.get('provider_id', 0))
        cert_id = int(request.form.get('cert_id', 0))
        action = request.form.get('action')
        provider_name = db.get_provider_name(provider_id)
        cert_name = db.get_certification_name(cert_id)

        if action == 'assign':
            questions = db.get_questions_without_correct_answer(cert_id)
            if questions:
                result = assign_correct_answers(provider_name, cert_name, questions)
                for resp in result.get('responses', []):
                    db.mark_correct_answers(resp.get('question_id'), resp.get('answer_ids', []))
                message = f"Processed {len(result.get('responses', []))} questions."
            else:
                message = "No questions found without correct answer."
        elif action == 'drag':
            questions = db.get_questions_without_answers(cert_id, 'drag-n-drop')
            if questions:
                result = generate_missing_answers(provider_name, cert_name, questions, 'drag-n-drop')
                for resp in result.get('responses', []):
                    db.add_answers(resp.get('question_id'), resp.get('answers', []))
                message = f"Completed {len(result.get('responses', []))} drag-n-drop questions."
            else:
                message = "No drag-n-drop questions without answers."
        elif action == 'matching':
            questions = db.get_questions_without_answers(cert_id, 'matching')
            if questions:
                result = generate_missing_answers(provider_name, cert_name, questions, 'matching')
                for resp in result.get('responses', []):
                    db.add_answers(resp.get('question_id'), resp.get('answers', []))
                message = f"Completed {len(result.get('responses', []))} matching questions."
            else:
                message = "No matching questions without answers."
    return render_template('fix.html', providers=providers, message=message)
