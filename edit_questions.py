from flask import Blueprint, render_template, jsonify, request, g
import json
import mysql.connector

from config import DB_CONFIG

edit_question_bp = Blueprint('edit_question', __name__)


def get_db():
    """Create a MySQL connection stored on the request context."""

    if 'db' not in g:
        g.db = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            charset='utf8mb4',
        )
    return g.db


@edit_question_bp.teardown_request
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@edit_question_bp.route('/')
def index():
    return render_template('edit_question.html')


@edit_question_bp.route('/api/providers')
def api_providers():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs ORDER BY name")
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)


@edit_question_bp.route('/api/certifications/<int:prov_id>')
def api_certifications(prov_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s ORDER BY name", (prov_id,))
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)


@edit_question_bp.route('/api/search')
def api_search():
    query = (request.args.get('q') or '').strip()
    provider_id = request.args.get('provider_id')
    certification_id = request.args.get('certification_id')

    if not query:
        return jsonify({'error': 'Le texte de recherche est requis'}), 400

    db = get_db()
    cur = db.cursor(dictionary=True)

    sql = [
        "SELECT q.id, q.text, q.src_file, m.name AS module_name,",
        "c.id AS certification_id, c.name AS certification_name,",
        "p.id AS provider_id, p.name AS provider_name",
        "FROM questions q",
        "JOIN modules m ON q.module = m.id",
        "JOIN courses c ON m.course = c.id",
        "JOIN provs p ON c.prov = p.id",
        "WHERE q.text LIKE %s",
    ]
    params = [f"%{query}%"]

    if provider_id:
        sql.append("AND p.id = %s")
        params.append(provider_id)
    if certification_id:
        sql.append("AND c.id = %s")
        params.append(certification_id)

    sql.append("ORDER BY q.id DESC LIMIT 50")

    cur.execute("\n".join(sql), params)
    rows = cur.fetchall()
    cur.close()

    results = []
    for row in rows:
        text_preview = (row.get('text') or '')[:220]
        results.append({
            'id': row['id'],
            'text_preview': text_preview,
            'src_file': row.get('src_file'),
            'module_name': row.get('module_name'),
            'provider': row.get('provider_name'),
            'certification': row.get('certification_name'),
        })

    return jsonify({'results': results})


@edit_question_bp.route('/api/questions/<int:question_id>')
def api_get_question(question_id: int):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute(
        """
        SELECT q.id, q.text, q.descr, q.src_file,
               m.id AS module_id, m.name AS module_name,
               c.id AS certification_id, c.name AS certification_name,
               p.id AS provider_id, p.name AS provider_name
          FROM questions q
          JOIN modules m ON q.module = m.id
          JOIN courses c ON m.course = c.id
          JOIN provs p ON c.prov = p.id
         WHERE q.id = %s
        LIMIT 1
        """,
        (question_id,),
    )
    question = cur.fetchone()
    if not question:
        cur.close()
        return jsonify({'error': 'Question introuvable'}), 404

    cur.execute(
        """
        SELECT qa.answer, qa.isok, a.text
          FROM quest_ans qa
          JOIN answers a ON qa.answer = a.id
         WHERE qa.question = %s
        """,
        (question_id,),
    )
    answers = []
    for row in cur.fetchall():
        raw_text = row['text']
        meta: dict[str, object] = {}
        value = raw_text
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                value = parsed.get('value', raw_text)
                meta = {k: v for k, v in parsed.items() if k != 'value'}
        except json.JSONDecodeError:
            value = raw_text
        answers.append({
            'id': row['answer'],
            'value': value,
            'isok': bool(row['isok']),
            'meta': meta,
            'raw': raw_text,
        })

    cur.close()
    question['answers'] = answers

    return jsonify(question)


@edit_question_bp.route('/api/questions/<int:question_id>', methods=['PUT'])
def api_update_question(question_id: int):
    payload = request.get_json() or {}
    text = (payload.get('text') or '').strip()
    descr = (payload.get('descr') or '').strip() or None
    src_file = (payload.get('src_file') or '').strip() or None
    answers = payload.get('answers') or []

    if not text:
        return jsonify({'error': 'Le texte de la question est requis'}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE id = %s", (question_id,))
    exists = cur.fetchone()[0]
    if not exists:
        cur.close()
        return jsonify({'error': 'Question introuvable'}), 404

    try:
        cur.execute(
            "UPDATE questions SET text = %s, descr = %s, src_file = %s WHERE id = %s",
            (text, descr, src_file, question_id),
        )
        cur.execute("DELETE FROM quest_ans WHERE question = %s", (question_id,))

        for answer in answers:
            value = (answer.get('value') or '').strip()
            if not value:
                continue

            meta = answer.get('meta') or {}
            if not isinstance(meta, dict):
                meta = {}
            answer_data = {k: v for k, v in meta.items() if k != 'value'}
            answer_data['value'] = value
            answer_json = json.dumps(answer_data, ensure_ascii=False)[:700]
            isok = 1 if int(answer.get('isok') or 0) == 1 else 0

            try:
                cur.execute(
                    "INSERT INTO answers (text, created_at) VALUES (%s, NOW())",
                    (answer_json,),
                )
                answer_id = cur.lastrowid
            except mysql.connector.Error as err:
                if err.errno == 1062:
                    cur.execute("SELECT id FROM answers WHERE text = %s LIMIT 1", (answer_json,))
                    row = cur.fetchone()
                    answer_id = row[0] if row else None
                else:
                    raise

            if answer_id:
                cur.execute(
                    "INSERT INTO quest_ans (question, answer, isok) VALUES (%s, %s, %s)",
                    (question_id, answer_id, isok),
                )

        db.commit()
    except Exception:
        db.rollback()
        cur.close()
        raise

    cur.close()
    return jsonify({'status': 'updated', 'answers': len(answers)})
