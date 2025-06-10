from flask import Flask, render_template, request, jsonify
import mysql.connector
import json
from config import DB_CONFIG

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('import_questions.html')

# --- Dropdown APIs ---
@app.route('/api/providers')
def api_providers():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/modules/<int:cert_id>')
def api_modules(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM modules WHERE course = %s", (cert_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

# --- Insert question ---
@app.route('/api/questions', methods=['POST'])
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
    try:
        cur.execute(
            "INSERT INTO questions (text, descr, level, module, nature, ty, created_at) VALUES (%s,%s,%s,%s,%s,%s,NOW())",
            (question_text, diagram_descr, level_num, module_id, nature_num, ty_num)
        )
        question_id = cur.lastrowid
        for ans in question.get('answers', []):
            ans_data = {k:v for k,v in ans.items() if k != 'isok'}
            ans_json = json.dumps(ans_data, ensure_ascii=False)
            isok = ans.get('isok',0)
            try:
                cur.execute("INSERT INTO answers (text, created_at) VALUES (%s,NOW())", (ans_json,))
                ans_id = cur.lastrowid
            except mysql.connector.Error as e:
                if e.errno == 1062:
                    cur.execute("SELECT id FROM answers WHERE text=%s", (ans_json,))
                    res = cur.fetchone()
                    ans_id = res[0] if res else None
                else:
                    raise
            if ans_id:
                cur.execute("INSERT INTO quest_ans (question, answer, isok) VALUES (%s,%s,%s)", (question_id, ans_id, isok))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close(); conn.close()
        return jsonify({'error': str(e)}), 500
    cur.close(); conn.close()
    return jsonify({'id': question_id})

if __name__ == '__main__':
    app.run(debug=True, port=9002)
