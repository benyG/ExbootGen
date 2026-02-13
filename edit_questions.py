from flask import Blueprint, render_template, jsonify, request, g
import json
import mimetypes
import re
from urllib.parse import urlparse, unquote
import mysql.connector
from google.cloud import storage
from werkzeug.utils import secure_filename
from uuid import uuid4
from pathlib import Path

from config import DB_CONFIG, GCS_BUCKET_NAME, GCS_UPLOAD_FOLDER

edit_question_bp = Blueprint('edit_question', __name__)

IMG_TAG_RE = re.compile(r"\s*<img[^>]*>\s*", re.IGNORECASE)
BR_TAG_RE = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)
IMG_SRC_RE = re.compile(r"<img[^>]+src=[\"']?([^\"'>\s]+)[\"']?[^>]*>", re.IGNORECASE)


def normalize_question_text(text: str) -> str:
    if not text:
        return ''
    normalized = IMG_TAG_RE.sub(' ', text)
    normalized = BR_TAG_RE.sub(' ', normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def normalize_answer_text(text: str) -> str:
    if not text:
        return ''
    normalized = BR_TAG_RE.sub(' ', text)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def normalize_meta_value(value: object) -> object:
    if isinstance(value, str):
        return normalize_answer_text(value)
    if isinstance(value, dict):
        return {k: normalize_meta_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_meta_value(v) for v in value]
    return value


def parse_answer_payload(raw_text: str) -> tuple[str, dict]:
    if not raw_text:
        return '', {}
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            value = normalize_answer_text(str(parsed.get('value', '') or ''))
            meta = {k: normalize_meta_value(v) for k, v in parsed.items() if k != 'value'}
            return value, meta
    except json.JSONDecodeError:
        pass
    return normalize_answer_text(raw_text), {}


def build_answer_signature(answers: list[dict]) -> str:
    normalized = []
    for answer in answers:
        value, meta = parse_answer_payload(answer.get('text') or '')
        normalized.append({
            'value': value,
            'meta': meta,
            'isok': 1 if answer.get('isok') else 0,
        })
    normalized.sort(
        key=lambda item: (
            item['value'],
            json.dumps(item['meta'], ensure_ascii=False, sort_keys=True),
            item['isok'],
        )
    )
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def build_payload_answer_signature(answers: list[dict]) -> str:
    normalized = []
    for answer in answers:
        value = normalize_answer_text(str(answer.get('value', '') or ''))
        if not value:
            continue
        meta = answer.get('meta') or {}
        if not isinstance(meta, dict):
            meta = {}
        clean_meta = {k: normalize_meta_value(v) for k, v in meta.items() if k != 'value'}
        normalized.append({
            'value': value,
            'meta': clean_meta,
            'isok': 1 if int(answer.get('isok') or 0) == 1 else 0,
        })
    normalized.sort(
        key=lambda item: (
            item['value'],
            json.dumps(item['meta'], ensure_ascii=False, sort_keys=True),
            item['isok'],
        )
    )
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def extract_image_urls(text: str) -> list[str]:
    if not text:
        return []
    return [match.group(1) for match in IMG_SRC_RE.finditer(text)]


def question_has_image(text: str) -> bool:
    if not text:
        return False
    return IMG_SRC_RE.search(text) is not None


def gcs_object_name_from_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    path = unquote(parsed.path or '').lstrip('/')
    host = parsed.netloc or ''
    if url.startswith('gs://'):
        return url[len('gs://'):].split('/', 1)[1]
    if host == 'storage.googleapis.com':
        if path.startswith(f"{GCS_BUCKET_NAME}/"):
            return path[len(GCS_BUCKET_NAME) + 1:]
        return None
    if host.endswith('.storage.googleapis.com'):
        bucket = host.split('.')[0]
        if bucket == GCS_BUCKET_NAME:
            return path
    if host.startswith(f"{GCS_BUCKET_NAME}."):
        return path
    return None


def delete_gcs_images(urls: list[str]) -> None:
    if not urls:
        return
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    for url in urls:
        object_name = gcs_object_name_from_url(url)
        if not object_name:
            continue
        try:
            bucket.blob(object_name).delete()
        except Exception:
            continue


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


@edit_question_bp.route('/api/modules/<int:cert_id>')
def api_modules(cert_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM modules WHERE course = %s ORDER BY name", (cert_id,))
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
            'has_image': question_has_image(row.get('text') or ''),
        })

    return jsonify({'results': results})


@edit_question_bp.route('/api/correction')
def api_correction_list():
    certification_id = request.args.get('certification_id')
    if not certification_id:
        return jsonify({'error': 'La certification est requise'}), 400

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(
        """
        SELECT q.id,
               q.text,
               q.src_file,
               m.name AS module_name,
               c.name AS certification_name,
               p.name AS provider_name,
               COUNT(qa.answer) AS answer_count,
               COALESCE(SUM(CASE WHEN qa.isok = 1 THEN 1 ELSE 0 END), 0) AS correct_count
          FROM questions q
          JOIN modules m ON q.module = m.id
          JOIN courses c ON m.course = c.id
          JOIN provs p ON c.prov = p.id
          LEFT JOIN quest_ans qa ON qa.question = q.id
         WHERE c.id = %s
         GROUP BY q.id, q.text, q.src_file, m.name, c.name, p.name
        HAVING COUNT(qa.answer) = 0
            OR COALESCE(SUM(CASE WHEN qa.isok = 1 THEN 1 ELSE 0 END), 0) = 0
         ORDER BY q.id DESC
        """,
        (certification_id,),
    )
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
            'answer_count': row.get('answer_count', 0),
            'correct_count': row.get('correct_count', 0),
            'has_image': question_has_image(row.get('text') or ''),
        })

    return jsonify({'results': results, 'total': len(results)})


@edit_question_bp.route('/api/duplicates')
def api_duplicate_list():
    certification_id = request.args.get('certification_id')
    if not certification_id:
        return jsonify({'error': 'La certification est requise'}), 400

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(
        """
        SELECT q.id,
               q.text,
               q.src_file,
               m.name AS module_name,
               c.name AS certification_name,
               p.name AS provider_name
          FROM questions q
          JOIN modules m ON q.module = m.id
          JOIN courses c ON m.course = c.id
          JOIN provs p ON c.prov = p.id
         WHERE c.id = %s
         ORDER BY q.id DESC
        """,
        (certification_id,),
    )
    rows = cur.fetchall()
    cur.close()

    groups: dict[str, list[dict]] = {}
    for row in rows:
        normalized = normalize_question_text(row.get('text') or '')
        if not normalized:
            continue
        groups.setdefault(normalized, []).append(row)

    results = []
    for normalized, items in groups.items():
        if len(items) < 2:
            continue
        for item in items:
            text_preview = normalized[:220]
            results.append({
                'id': item['id'],
                'text_preview': text_preview,
                'src_file': item.get('src_file'),
                'module_name': item.get('module_name'),
                'provider': item.get('provider_name'),
                'certification': item.get('certification_name'),
                'duplicate_count': len(items),
                'has_image': question_has_image(item.get('text') or ''),
            })

    results.sort(key=lambda item: (-item['duplicate_count'], item['id']), reverse=False)
    return jsonify({'results': results, 'total': len(results)})


@edit_question_bp.route('/api/duplicates/verify')
def api_duplicate_verify():
    certification_id = request.args.get('certification_id')
    if not certification_id:
        return jsonify({'error': 'La certification est requise'}), 400

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(
        """
        SELECT q.id,
               q.text,
               q.src_file,
               m.name AS module_name,
               c.name AS certification_name,
               p.name AS provider_name,
               qa.isok AS answer_isok,
               a.text AS answer_text
          FROM questions q
          JOIN modules m ON q.module = m.id
          JOIN courses c ON m.course = c.id
          JOIN provs p ON c.prov = p.id
          LEFT JOIN quest_ans qa ON qa.question = q.id
          LEFT JOIN answers a ON qa.answer = a.id
         WHERE c.id = %s
         ORDER BY q.id DESC
        """,
        (certification_id,),
    )
    rows = cur.fetchall()
    cur.close()

    questions: dict[int, dict] = {}
    for row in rows:
        qid = row['id']
        question = questions.setdefault(
            qid,
            {
                'id': qid,
                'text': row.get('text') or '',
                'src_file': row.get('src_file'),
                'module_name': row.get('module_name'),
                'provider': row.get('provider_name'),
                'certification': row.get('certification_name'),
                'answers': [],
            },
        )
        if row.get('answer_text') is not None:
            question['answers'].append({
                'text': row.get('answer_text') or '',
                'isok': bool(row.get('answer_isok')),
            })

    grouped_by_text: dict[str, list[dict]] = {}
    for question in questions.values():
        normalized = normalize_question_text(question.get('text') or '')
        if not normalized:
            continue
        question['normalized_text'] = normalized
        grouped_by_text.setdefault(normalized, []).append(question)

    candidate_groups: list[list[dict]] = []
    for items in grouped_by_text.values():
        if len(items) < 2:
            continue
        signature_map: dict[str, list[dict]] = {}
        for item in items:
            signature = build_answer_signature(item.get('answers', []))
            signature_map.setdefault(signature, []).append(item)
        for signature_items in signature_map.values():
            if len(signature_items) < 2:
                continue
            signature_items.sort(key=lambda entry: entry['id'])
            candidate_groups.append(signature_items)

    candidate_groups.sort(key=lambda group: (-len(group), group[0]['id']))

    results_groups = []
    total_items = 0
    for group_index, group_items in enumerate(candidate_groups, start=1):
        items_payload = []
        for item in group_items:
            total_items += 1
            items_payload.append({
                'id': item['id'],
                'text_preview': (item.get('normalized_text') or '')[:220],
                'src_file': item.get('src_file'),
                'module_name': item.get('module_name'),
                'provider': item.get('provider'),
                'certification': item.get('certification'),
                'has_image': question_has_image(item.get('text') or ''),
            })
        results_groups.append({
            'group_id': group_index,
            'count': len(group_items),
            'items': items_payload,
        })

    return jsonify({'groups': results_groups, 'total': total_items})


@edit_question_bp.route('/api/questions', methods=['POST'])
def api_create_question():
    payload = request.get_json() or {}
    module_id = payload.get('module_id')
    text = (payload.get('text') or '').strip()
    descr = (payload.get('descr') or '').strip() or None
    src_file = (payload.get('src_file') or '').strip() or None
    nature = payload.get('nature', 1)
    answers = payload.get('answers') or []

    if not module_id:
        return jsonify({'error': 'Le module est requis'}), 400
    if not text:
        return jsonify({'error': 'Le texte de la question est requis'}), 400
    try:
        nature = int(nature)
    except (TypeError, ValueError):
        return jsonify({'error': 'Le type de question est invalide'}), 400
    if nature not in {1, 2, 3, 4, 5}:
        return jsonify({'error': 'Le type de question est invalide'}), 400

    sanitized_answers = []
    for answer in answers:
        value = (answer.get('value') or '').strip()
        if not value:
            continue
        meta = answer.get('meta') or {}
        if not isinstance(meta, dict):
            meta = {}
        sanitized_answers.append({
            'value': value,
            'meta': meta,
            'isok': 1 if int(answer.get('isok') or 0) == 1 else 0,
        })

    if not sanitized_answers:
        return jsonify({'error': 'Au moins une réponse est requise'}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM modules WHERE id = %s", (module_id,))
    module_exists = cur.fetchone()[0]
    if not module_exists:
        cur.close()
        return jsonify({'error': 'Module introuvable'}), 404

    cur.execute("SELECT course FROM modules WHERE id = %s", (module_id,))
    course_row = cur.fetchone()
    course_id = course_row[0] if course_row else None
    normalized_text = normalize_question_text(text)
    if course_id:
        cur.execute(
            """
            SELECT q.id, q.text
              FROM questions q
              JOIN modules m ON q.module = m.id
             WHERE m.course = %s
            """,
            (course_id,),
        )
        for existing_id, existing_text in cur.fetchall():
            if normalize_question_text(existing_text) == normalized_text:
                cur.close()
                return jsonify({
                    'error': 'Une question similaire existe déjà',
                    'existing_id': existing_id,
                }), 409

    try:
        cur.execute(
            """
            INSERT INTO questions (text, descr, module, src_file, level, nature, ty, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (text, descr, module_id, src_file, 1, nature, 1),
        )
        question_id = cur.lastrowid

        for answer in sanitized_answers:
            answer_data = {k: v for k, v in (answer.get('meta') or {}).items() if k != 'value'}
            answer_data['value'] = answer['value']
            answer_json = json.dumps(answer_data, ensure_ascii=False)[:700]
            isok = answer['isok']

            try:
                cur.execute(
                    """
                    INSERT INTO answers (text, created_at, updated_at)
                    VALUES (%s, NOW(), NOW())
                    """,
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
    return jsonify({'status': 'created', 'id': question_id, 'answers': len(sanitized_answers)}), 201


@edit_question_bp.route('/api/questions/<int:question_id>')
def api_get_question(question_id: int):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute(
        """
        SELECT q.id, q.text, q.descr, q.src_file,
               q.nature,
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
    nature = payload.get('nature')
    answers = payload.get('answers') or []

    if not text:
        return jsonify({'error': 'Le texte de la question est requis'}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT q.nature, m.course FROM questions q JOIN modules m ON q.module = m.id WHERE q.id = %s",
        (question_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        return jsonify({'error': 'Question introuvable'}), 404
    current_nature, course_id = row

    normalized_text = normalize_question_text(text)
    new_answers_signature = build_payload_answer_signature(answers)
    if course_id:
        cur.execute(
            """
            SELECT q.id, q.text
              FROM questions q
              JOIN modules m ON q.module = m.id
             WHERE m.course = %s AND q.id != %s
            """,
            (course_id, question_id),
        )
        candidate_ids = []
        for existing_id, existing_text in cur.fetchall():
            if normalize_question_text(existing_text) == normalized_text:
                candidate_ids.append(existing_id)

        if candidate_ids:
            placeholders = ','.join(['%s'] * len(candidate_ids))
            cur.execute(
                f"""
                SELECT qa.question AS question_id,
                       qa.isok AS answer_isok,
                       a.text AS answer_text
                  FROM quest_ans qa
                  JOIN answers a ON a.id = qa.answer
                 WHERE qa.question IN ({placeholders})
                """,
                tuple(candidate_ids),
            )
            answers_rows = cur.fetchall()

            candidate_answers_map: dict[int, list[dict]] = {qid: [] for qid in candidate_ids}
            for candidate_id, answer_isok, answer_text in answers_rows:
                candidate_answers_map.setdefault(candidate_id, []).append({
                    'text': answer_text or '',
                    'isok': bool(answer_isok),
                })

            for existing_id in candidate_ids:
                existing_signature = build_answer_signature(candidate_answers_map.get(existing_id, []))
                if existing_signature == new_answers_signature:
                    cur.close()
                    return jsonify({
                        'error': 'Une question identique (texte + réponses) existe déjà',
                        'existing_id': existing_id,
                    }), 409

    if nature is None:
        nature = current_nature
    try:
        nature = int(nature)
    except (TypeError, ValueError):
        return jsonify({'error': 'Le type de question est invalide'}), 400
    if nature not in {1, 2, 3, 4, 5}:
        return jsonify({'error': 'Le type de question est invalide'}), 400

    try:
        cur.execute(
            """
            UPDATE questions
               SET text = %s,
                   descr = %s,
                   src_file = %s,
                   nature = %s,
                   updated_at = NOW()
             WHERE id = %s
            """,
            (text, descr, src_file, nature, question_id),
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
                    """
                    INSERT INTO answers (text, created_at, updated_at)
                    VALUES (%s, NOW(), NOW())
                    """,
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


@edit_question_bp.route('/api/questions/<int:question_id>', methods=['DELETE'])
def api_delete_question(question_id: int):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT text FROM questions WHERE id = %s", (question_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        return jsonify({'error': 'Question introuvable'}), 404
    question_text = row[0] or ''
    image_urls = extract_image_urls(question_text)

    try:
        cur.execute("DELETE FROM quest_ans WHERE question = %s", (question_id,))
        cur.execute("DELETE FROM questions WHERE id = %s", (question_id,))
        db.commit()
    except Exception:
        db.rollback()
        cur.close()
        raise

    cur.close()
    delete_gcs_images(image_urls)
    return jsonify({'status': 'deleted', 'id': question_id})


@edit_question_bp.route('/api/upload-image', methods=['POST'])
def api_upload_image():
    """Upload an image to Google Cloud Storage and return its public URL."""

    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier image reçu.'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Le fichier est vide.'}), 400

    mimetype = file.mimetype or ''
    if not mimetype.startswith('image/'):
        return jsonify({'error': "Seuls les fichiers d'image sont autorisés."}), 400

    # Build a safe filename and storage path
    original_name = secure_filename(file.filename) or 'image'
    extension = Path(original_name).suffix
    if not extension:
        guessed = mimetypes.guess_extension(mimetype) or '.png'
        extension = guessed
    object_name = f"{GCS_UPLOAD_FOLDER.rstrip('/')}/{uuid4().hex}{extension}"

    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(object_name)

        file.stream.seek(0)
        blob.upload_from_file(file.stream, content_type=mimetype)

        # ``blob.make_public`` is not compatible with buckets using uniform
        # access control and triggers ``storage.objects.getIamPolicy``
        # permission errors.  The bucket already grants public read access, so
        # the object URL is directly accessible without modifying ACLs.
        public_url = blob.public_url

        return jsonify({
            'url': public_url,
            'bucket': GCS_BUCKET_NAME,
            'path': object_name,
        })
    except Exception as exc:  # pragma: no cover - runtime safety
        return jsonify({'error': f"Échec de l'upload : {exc}"}), 500
