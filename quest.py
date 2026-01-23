from flask import Blueprint, render_template, request, jsonify
import mysql.connector
import json
from config import DB_CONFIG

quest_bp = Blueprint('quest', __name__)

@quest_bp.route('/')
def index():
    return render_template('import_questions.html')

# --- Dropdown APIs ---
@quest_bp.route('/api/providers')
def api_providers():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@quest_bp.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@quest_bp.route('/api/modules/<int:cert_id>')
def api_modules(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM modules WHERE course = %s", (cert_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

# --- Insert question (une par une) ---
@quest_bp.route('/api/questions', methods=['POST'])
def api_questions():
    data = request.get_json() or {}
    module_id = data.get('module_id')
    question = data.get('question')
    if not module_id or not question:
        return jsonify({'error': 'module_id and question are required'}), 400

    scenario = question.get('scenario', 'no')
    ty_mapping = {'no':1, 'scenario':2, 'scenario-illustrated':3}
    level_mapping = {'easy':0, 'medium':1, 'hard':2}
    nature_mapping = {
        'qcm':1, 'truefalse':2, 'short-answer':3,
        'matching':4, 'drag-n-drop':5
    }

    ty_num = ty_mapping.get(scenario, 1)
    level_num = level_mapping.get(question.get('level', 'medium'), 1)
    nature_num = nature_mapping.get(question.get('nature', 'qcm'), 1)

    context = (question.get('context') or '').strip()
    diagram_descr = (question.get('diagram_descr') or '').strip()
    image = (question.get('image') or '').strip()
    text = (question.get('text') or '').strip()

    # Même logique que le fichier d’origine : context/image préfixés dans text
    if context or image:
        question_text = ''
        if context:
            question_text += context + '\n'
        if image:
            question_text += image + '<br>'
        question_text += text
    else:
        question_text = text

    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    question_id = None
    try:
        try:
            cur.execute(
                "INSERT INTO questions (text, descr, level, module, nature, ty, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,NOW())",
                (question_text, diagram_descr, level_num, module_id, nature_num, ty_num)
            )
            question_id = cur.lastrowid
        except mysql.connector.Error as e:
            # DUPLICATE question -> on passe à la suivante (skip)
            if e.errno == 1062:
                # On récupère l'id existant pour info
                cur.execute(
                    "SELECT id FROM questions WHERE module=%s AND text=%s LIMIT 1",
                    (module_id, question_text)
                )
                row = cur.fetchone()
                existing_id = row[0] if row else None
                conn.rollback()
                cur.close(); conn.close()
                return jsonify({'status': 'skipped-duplicate', 'existing_id': existing_id}), 200
            else:
                raise

        # Réponses stockées en JSON + liaisons quest_ans
        for ans in question.get('answers', []):
            raw_val = (ans.get('value') or ans.get('text') or '').strip()
            if not raw_val:
                continue

            answer_data = {k: v for k, v in ans.items() if k not in ('isok', 'value', 'text')}
            answer_data['value'] = raw_val
            a_json = json.dumps(answer_data, ensure_ascii=False)[:700]
            isok = 1 if int(ans.get('isok') or 0) == 1 else 0

            # Insert ou réutilise answer
            try:
                cur.execute("INSERT INTO answers (text, created_at) VALUES (%s,NOW())", (a_json,))
                ans_id = cur.lastrowid
            except mysql.connector.Error as e:
                if e.errno == 1062:
                    cur.execute("SELECT id FROM answers WHERE text=%s LIMIT 1", (a_json,))
                    r = cur.fetchone()
                    ans_id = r[0] if r else None
                else:
                    raise

            if ans_id:
                # Lien quest_ans (ignore si déjà présent)
                try:
                    cur.execute(
                        "INSERT INTO quest_ans (question, answer, isok) VALUES (%s,%s,%s)",
                        (question_id, ans_id, isok)
                    )
                except mysql.connector.Error as e:
                    if e.errno == 1062:
                        # paire (question, answer) déjà liée -> on ignore
                        pass
                    else:
                        raise

        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close(); conn.close()
        return jsonify({'error': str(e)}), 500

    cur.close(); conn.close()
    return jsonify({'id': question_id, 'status': 'inserted'})

