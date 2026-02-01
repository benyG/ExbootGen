# reloc.py

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from time import sleep

from flask import Blueprint, Response, render_template, request
import mysql.connector
import requests

from config import DB_CONFIG, OPENAI_API_KEY, OPENAI_MODEL

OPENAI_ENDPOINT = 'https://api.openai.com/v1/responses'

RELOC_MAPPING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question_id", "domain_to_affect"],
                "properties": {
                    "question_id": {"type": "integer"},
                    "domain_to_affect": {"type": "integer"},
                },
            },
        },
    },
}

def _json_schema_format(schema: dict, name: str) -> dict:
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": schema,
    }


def _build_response_payload(prompt: str, *, text_format: dict | None = None) -> dict:
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ],
    }
    if text_format is not None:
        payload["text"] = {"format": text_format}
    return payload


def _extract_response_text(resp_json: dict) -> str:
    output_text = resp_json.get("output_text")
    if output_text:
        return output_text.strip()

    output = resp_json.get("output", [])
    for item in output:
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text") and content.get("text"):
                return content["text"].strip()

    raise ValueError(f"réponse OpenAI invalide: {resp_json}")

reloc_bp = Blueprint('reloc', __name__)


def _relocate_questions(
    *,
    src_module: int,
    dst_cert: int,
    batch_size: int,
    requested_workers: int | None = None,
) -> dict:
    conn0 = mysql.connector.connect(**DB_CONFIG)
    cur0 = conn0.cursor(dictionary=True)
    cur0.execute("SELECT id, name FROM modules WHERE course = %s", (dst_cert,))
    modules = cur0.fetchall()
    cur0.close()
    conn0.close()

    conn1 = mysql.connector.connect(**DB_CONFIG)
    cur1 = conn1.cursor(dictionary=True)
    cur1.execute(
        "SELECT id, text FROM questions WHERE module = %s",
        (src_module,)
    )
    all_questions = cur1.fetchall()
    cur1.close()
    conn1.close()

    if not all_questions:
        return {"moved": 0, "total_questions": 0, "batches": 0, "errors": []}

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
    modules_info = json.dumps(modules, ensure_ascii=False)
    errors = []

    def _process_batch(questions_batch):
        questions_info = json.dumps(
            [{'question_id': q['id'], 'text': q['text']} for q in questions_batch],
            ensure_ascii=False
        )
        prompt = (
            "TASK: As a coach proficient in the exam curriculum, assign each question to the correct domain using the module ID.\n"
            "FORMAT: JSON object with an items array of objects {question_id: X, domain_to_affect: Y} where Y is module id.\n"
            f"Exam id: {dst_cert}\n"
            f"Domains: {modules_info}\n"
            f"Questions: {questions_info}\n"
        )

        payload = _build_response_payload(
            prompt,
            text_format=_json_schema_format(RELOC_MAPPING_SCHEMA, "reloc_mapping"),
        )

        resp = requests.post(
            OPENAI_ENDPOINT,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {OPENAI_API_KEY}'
            },
            json=payload,
            timeout=60,
        )
        if not resp.ok:
            raise ValueError(f"OpenAI error {resp.status_code}: {resp.text}")

        content = _extract_response_text(resp.json())
        mapping = json.loads(content)
        mapping_items = mapping.get("items", []) if isinstance(mapping, dict) else []

        conn2 = mysql.connector.connect(**DB_CONFIG)
        cur2 = conn2.cursor()
        moved = 0
        for item in mapping_items:
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
        return moved

    total_moved = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                total_moved += future.result()
            except Exception as exc:
                errors.append(str(exc))

    return {
        "moved": total_moved,
        "total_questions": len(all_questions),
        "batches": len(batches),
        "errors": errors,
    }

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
    cur.execute("SELECT id, name, code_cert_key AS code_cert FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}

@reloc_bp.route('/api/modules/<int:cert_id>')
def api_modules(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name, code_cert FROM modules WHERE course = %s", (cert_id,))
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
                "FORMAT: JSON object with an items array of objects {question_id: X, domain_to_affect: Y} where Y is module id.\n"
                f"Exam id: {dst_cert}\n"
                f"Domains: {modules_info}\n"
                f"Questions: {questions_info}\n"
            )

            payload = _build_response_payload(
                prompt,
                text_format=_json_schema_format(RELOC_MAPPING_SCHEMA, "reloc_mapping"),
            )

            resp = requests.post(
                OPENAI_ENDPOINT,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {OPENAI_API_KEY}'
                },
                json=payload,
                timeout=60,
            )
            if not resp.ok:
                raise ValueError(f"OpenAI error {resp.status_code}: {resp.text}")

            content = _extract_response_text(resp.json())
            mapping = json.loads(content)
            mapping_items = mapping.get("items", []) if isinstance(mapping, dict) else []

            conn2 = mysql.connector.connect(**DB_CONFIG)
            cur2 = conn2.cursor()
            moved = 0
            for item in mapping_items:
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


@reloc_bp.route('/api/mcp/relocate', methods=['POST'])
def api_mcp_relocate():
    payload = request.get_json(silent=True) or {}
    src_module = payload.get('source_module_id')
    dst_cert = payload.get('destination_cert_id')
    batch_size = payload.get('batch_size', 10)
    requested_workers = payload.get('workers')

    try:
        src_module = int(src_module)
        dst_cert = int(dst_cert)
        batch_size = int(batch_size)
        requested_workers = int(requested_workers) if requested_workers is not None else None
    except (TypeError, ValueError):
        return {"error": "source_module_id, destination_cert_id invalides"}, 400

    if batch_size <= 0:
        return {"error": "batch_size invalide"}, 400

    result = _relocate_questions(
        src_module=src_module,
        dst_cert=dst_cert,
        batch_size=batch_size,
        requested_workers=requested_workers,
    )
    status = "ok" if not result.get("errors") else "partial"
    return json.dumps({"status": status, **result}, ensure_ascii=False), 200, {'Content-Type': 'application/json'}


