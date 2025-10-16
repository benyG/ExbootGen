# reloc.py

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
from time import sleep

from flask import Blueprint, Response, render_template, request
import mysql.connector
import requests

from config import DB_CONFIG, OPENAI_API_KEY, OPENAI_MODEL

OPENAI_ENDPOINT = 'https://api.openai.com/v1/chat/completions'

reloc_bp = Blueprint('reloc', __name__)

# -- Routes pour remplir les dropdowns --
@reloc_bp.route('/')
def index():
    return render_template('reloc.html')

@reloc_bp.route('/api/providers')
def api_providers():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}

@reloc_bp.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}

@reloc_bp.route('/api/modules/<int:cert_id>')
def api_modules(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM modules WHERE course = %s", (cert_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}


@reloc_bp.route('/api/question_count/<int:module_id>')
def api_question_count(module_id):
    """Retourne le nombre de questions restantes dans un module."""
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE module = %s", (module_id,))
    count = cur.fetchone()[0]
    cur.close(); conn.close()
    return json.dumps({'count': count}, ensure_ascii=False), 200, {'Content-Type': 'application/json'}

# -- SSE pour le streaming de la relocalisation --
@reloc_bp.route('/api/stream_relocate', methods=['GET'])
def stream_relocate():
    src_module = request.args.get('source_module_id',    type=int)
    dst_cert    = request.args.get('destination_cert_id', type=int)
    batch_size  = request.args.get('batch_size',          default=10, type=int)
    requested_workers = request.args.get('workers', type=int)

    if not src_module or not dst_cert:
        return {"error": "source_module_id et destination_cert_id sont requis"}, 400

    def generate():
        # 1) Charger la liste des modules de destination (une seule fois)
        conn0 = mysql.connector.connect(**DB_CONFIG)
        cur0 = conn0.cursor(dictionary=True)
        cur0.execute("SELECT id, name FROM modules WHERE course = %s", (dst_cert,))
        modules = cur0.fetchall()
        cur0.close(); conn0.close()

        conn1 = mysql.connector.connect(**DB_CONFIG)
        cur1 = conn1.cursor(dictionary=True)
        cur1.execute(
            "SELECT id, text FROM questions WHERE module = %s",
            (src_module,)
        )
        all_questions = cur1.fetchall()
        cur1.close(); conn1.close()

        if not all_questions:
            yield "data: Aucun traitement (0 questions trouvées)\n\n"
            return

        batches = [
            all_questions[i : i + max(1, batch_size)]
            for i in range(0, len(all_questions), max(1, batch_size))
        ]
        default_workers = max(1, min(os.cpu_count() or 1, 8))
        env_workers = os.getenv("RELOC_MAX_WORKERS")
        if env_workers:
            try:
                default_workers = max(1, min(int(env_workers), 32))
            except ValueError:
                pass
        if requested_workers:
            default_workers = max(1, min(requested_workers, 32))

        max_workers = min(default_workers, len(batches))
        if max_workers > 1:
            yield f"data: Traitement parallèle avec {max_workers} worker(s)\n\n"

        modules_info = json.dumps(modules, ensure_ascii=False)

        def _process_batch(batch_index: int, questions_batch):
            questions_info = json.dumps(
                [{'question_id': q['id'], 'text': q['text']} for q in questions_batch],
                ensure_ascii=False
            )
            prompt = (
                "TASK: As a coach proficient in the exam curriculum, assign each question to the correct domain using the module ID.\n"
                "FORMAT: JSON array of objects {question_id: X, domain_to_affect: Y} where Y is module id.\n"
                f"Exam id: {dst_cert}\n"
                f"Domains: {modules_info}\n"
                f"Questions: {questions_info}\n"
            )

            payload = {
                "model": OPENAI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
            }

            resp = requests.post(
                OPENAI_ENDPOINT,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {OPENAI_API_KEY}'
                },
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()

            choices = resp.json().get('choices', [])
            if not choices or 'message' not in choices[0]:
                raise ValueError("réponse OpenAI invalide")

            content = choices[0]['message']['content']
            cleaned = re.sub(r"```json|```", "", content).strip()
            try:
                mapping = json.loads(cleaned)
            except json.JSONDecodeError:
                cleaned2 = cleaned.replace("\n", "").replace("\\", "")
                mapping = json.loads(cleaned2)

            conn2 = mysql.connector.connect(**DB_CONFIG)
            cur2 = conn2.cursor()
            moved = 0
            for item in mapping:
                qid = item.get('question_id')
                mod_id = item.get('domain_to_affect')
                if isinstance(qid, int) and isinstance(mod_id, int):
                    cur2.execute(
                        "UPDATE questions SET module=%s WHERE id=%s",
                        (mod_id, qid),
                    )
                    moved += cur2.rowcount
            conn2.commit()
            cur2.close()
            conn2.close()

            sleep(0.1)
            return moved

        total_moved = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_process_batch, idx, batch): idx
                for idx, batch in enumerate(batches, start=1)
            }
            for future in as_completed(futures):
                batch_index = futures[future]
                try:
                    moved = future.result()
                except Exception as exc:
                    yield f"data: ERREUR batch #{batch_index}: {exc}\n\n"
                    return
                total_moved += moved
                yield f"data: Batch #{batch_index} moved={moved}, total={total_moved}\n\n"

        if total_moved == 0:
            yield "data: Aucun traitement (0 questions trouvées)\n\n"
        else:
            yield f"data: DONE, total moved={total_moved}\n\n"

    return Response(generate(), mimetype='text/event-stream')


