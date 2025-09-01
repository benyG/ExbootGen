import os
import json
import time
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template
from werkzeug.utils import secure_filename

from routes_pdf import detect_questions, extract_text_from_pdf, db_conn
from openai_api import generate_questions as ai_generate_questions
from config import DISTRIBUTION, API_REQUEST_DELAY

# -------- Blueprint / Templates --------
pdf_bp = Blueprint('pdf', __name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mémoire légère (session d’analyse)
SESSIONS = {}  # { session_id: { "module_id": int, "questions": [...] } }

# -------- Mappings (text -> code BD) --------
LEVEL_MAP = {"easy": 0, "medium": 1, "hard": 2}
SCENARIO_MAP = {"no": 1, "scenario": 2, "scenario-illustrated": 3}
NATURE_MAP = {
    "qcm": 1,
    "truefalse": 2,
    "short-answer": 3,
    "matching": 4,        # HOTSPOT
    "hotspot": 4,         # alias sûr
    "drag-n-drop": 5,     # DRAG DROP
    "drag drop": 5        # alias sûr
}

def to_level_code(raw):
    """Accepte 'easy'/'medium'/'hard' ou entier; renvoie 0/1/2 (défaut 1)."""
    if isinstance(raw, str):
        return LEVEL_MAP.get(raw.strip().lower(), 1)
    try:
        v = int(raw)
        return v if v in (0, 1, 2) else 1
    except Exception:
        return 1

def to_scenario_code(raw):
    """Accepte 'no'/'scenario'/'scenario-illustrated' ou entier; renvoie 1/2/3 (défaut 1)."""
    if isinstance(raw, str):
        return SCENARIO_MAP.get(raw.strip().lower(), 1)
    try:
        v = int(raw)
        return v if v in (1, 2, 3) else 1
    except Exception:
        return 1

def to_nature_code(raw):
    """
    Accepte 'qcm'/'truefalse'/'matching'/'drag-n-drop' (+ alias) ou entier.
    Renvoie 1/2/4/5 (défaut 1).
    """
    if isinstance(raw, str):
        return NATURE_MAP.get(raw.strip().lower(), 1)
    try:
        v = int(raw)
        return v if v in (1, 2, 3, 4, 5) else 1
    except Exception:
        return 1

# -------------------- APIs dropdown (schéma: provs, courses.prov, modules.course) --------------------

@pdf_bp.route("/api/providers")
def api_providers():
    conn = db_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name FROM provs ORDER BY name")
        return jsonify(cur.fetchall())
    finally:
        try: cur.close()
        except Exception: pass
        conn.close()

@pdf_bp.route("/api/certifications/<int:provider_id>")
def api_certifications(provider_id):
    conn = db_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name FROM courses WHERE prov=%s ORDER BY name", (provider_id,))
        return jsonify(cur.fetchall())
    finally:
        try: cur.close()
        except Exception: pass
        conn.close()

@pdf_bp.route("/api/modules/<int:cert_id>")
def api_modules(cert_id):
    conn = db_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name FROM modules WHERE course=%s ORDER BY name", (cert_id,))
        return jsonify(cur.fetchall())
    finally:
        try: cur.close()
        except Exception: pass
        conn.close()

# -------------------- Search PDFs --------------------

@pdf_bp.route("/api/search-pdfs")
def api_search_pdfs():
    """Return a list of PDF files under ``root`` matching query ``q``."""
    root = request.args.get("root") or ""
    query = (request.args.get("q") or "").lower()
    if not root or not os.path.isdir(root):
        return jsonify([])

    matches = []
    for dirpath, _, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            if query in name.lower():
                rel_path = os.path.relpath(os.path.join(dirpath, name), root)
                matches.append(rel_path)
                if len(matches) >= 20:
                    break
        if len(matches) >= 20:
            break

    return jsonify(matches)

# -------------------- UI --------------------

@pdf_bp.route("/")
def index():
    return render_template("upload.html")

# -------------------- PDF Question Generator --------------------

@pdf_bp.route("/generate")
def generate_index():
    """Render the question generation form."""
    return render_template("pdf_generate.html")


@pdf_bp.route("/generate-questions", methods=["POST"])
def generate_questions_from_pdf():
    """Generate questions from an uploaded PDF using the OpenAI API."""
    try:
        provider_id = int(request.form.get("provider_id", 0))
        cert_id = int(request.form.get("cert_id", 0))
        module_id = int(request.form.get("module_id", 0))
        num_questions = int(request.form.get("num_questions", 0))
    except ValueError:
        return jsonify({"status": "error", "message": "Paramètres invalides"}), 400

    use_distribution = request.form.get("use_distribution") == "on"
    q_type = request.form.get("q_type", "qcm")

    pdf_file = request.files.get("pdf_file")
    if not pdf_file or not pdf_file.filename.lower().endswith(".pdf"):
        return jsonify({"status": "error", "message": "Fichier PDF requis"}), 400

    # Taille max 20 Mo
    pdf_file.stream.seek(0, os.SEEK_END)
    size = pdf_file.stream.tell()
    pdf_file.stream.seek(0)
    if size > 20 * 1024 * 1024:
        return jsonify({"status": "error", "message": "Fichier trop volumineux (>20Mo)"}), 400

    filename = secure_filename(pdf_file.filename)
    save_path = UPLOAD_DIR / filename
    pdf_file.save(str(save_path))

    text = extract_text_from_pdf(
        str(save_path),
        use_ocr=False,
        skip_first_page=True,
        header_ratio=0.10,
        footer_ratio=0.10,
    )

    if not text.strip():
        return jsonify({"status": "error", "message": "PDF vide"}), 400

    # Récupération des noms provider et certification
    conn = db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM provs WHERE id=%s", (provider_id,))
        prov_row = cur.fetchone()
        cur.execute("SELECT name FROM courses WHERE id=%s", (cert_id,))
        cert_row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

    provider_name = prov_row[0] if prov_row else ""
    cert_name = cert_row[0] if cert_row else ""

    # Distribution des questions
    pairs = []  # (q_type, scenario, count)
    if use_distribution:
        dist = DISTRIBUTION.get("medium", {})
        base_total = sum(sum(s.values()) for s in dist.values()) or 1
        scale = num_questions / base_total
        for qt, scen_dict in dist.items():
            for scen, base_count in scen_dict.items():
                count = int(round(base_count * scale))
                if count > 0:
                    pairs.append([qt, scen, count])
        total = sum(p[2] for p in pairs)
        if pairs and total != num_questions:
            pairs[0][2] += num_questions - total
    else:
        pairs.append([q_type, "no", num_questions])

    chunk_size = 4000
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [text]

    questions = []
    chunk_idx = 0
    num_chunks = len(chunks)
    for qt, scen, count in pairs:
        remaining = count
        while remaining > 0:
            chunk = chunks[chunk_idx % num_chunks]
            chunk_idx += 1
            to_generate = min(remaining, 5)
            try:
                data = ai_generate_questions(
                    provider_name=provider_name,
                    certification=cert_name,
                    domain="PDF content",
                    domain_descr=chunk,
                    level="medium",
                    q_type=qt,
                    practical=scen,
                    scenario_illustration_type="none",
                    num_questions=to_generate,
                )
                questions.extend(data.get("questions", []))
                remaining -= len(data.get("questions", []))
                time.sleep(API_REQUEST_DELAY)
            except Exception as e:
                return jsonify({
                    "status": "error",
                    "message": str(e),
                    "json_data": {"questions": questions},
                }), 500

    session_id = os.urandom(8).hex()
    SESSIONS[session_id] = {"module_id": module_id, "questions": questions}
    return jsonify({"status": "ok", "session_id": session_id, "json_data": {"questions": questions}})

# -------------------- Upload / Analyse PDF --------------------

@pdf_bp.route("/upload-pdf", methods=["POST"])
def upload_pdf():
    module_id_raw = request.form.get("module_id")
    try:
        module_id = int(module_id_raw) if module_id_raw is not None else None
    except ValueError:
        return jsonify({"status": "error", "message": "module_id invalide"}), 400

    file = request.files.get("file")
    file_path = request.form.get("file_path")

    if file:
        filename = secure_filename(file.filename or "upload.pdf")
        save_path = UPLOAD_DIR / filename
        file.save(str(save_path))
        pdf_to_read = str(save_path)
    elif file_path:
        if not os.path.isfile(file_path):
            return jsonify({"status": "error", "message": "Fichier introuvable"}), 400
        pdf_to_read = file_path
    else:
        return jsonify({"status": "error", "message": "Aucun fichier fourni"}), 400

    text = extract_text_from_pdf(
        pdf_to_read,
        use_ocr=False,
        skip_first_page=True,
        header_ratio=0.10,
        footer_ratio=0.10
    )

    data = detect_questions(text, module_id)
    session_id = os.urandom(8).hex()
    SESSIONS[session_id] = data

    return jsonify({"status": "ok", "session_id": session_id, "json_data": data})

# -------------------- Import BD --------------------

@pdf_bp.route("/import-questions", methods=["POST"])
def import_questions():
    session_id = request.form.get("session_id")
    if not session_id or session_id not in SESSIONS:
        return jsonify({"status": "error", "message": "Session introuvable"}), 400

    data = SESSIONS[session_id]
    q_imported = 0
    q_skipped = 0
    a_imported = 0
    a_reused = 0

    conn = db_conn()
    try:
        cur = conn.cursor()

        for q in data.get("questions", []):
            # ----- Construire text (context + \n + text) -----
            base_text = (q.get("text") or "").strip()
            context   = (q.get("context") or "").strip()
            question_text = f"{context}\n{base_text}".strip() if context else base_text

            # ----- Convertir level/scenario/nature depuis CHAÎNES -> CODES BD -----
            q_level_code    = to_level_code(q.get("level", "medium"))
            q_scenario_code = to_scenario_code(q.get("scenario", "no"))  # 'ty' en BD
            q_nature_code   = to_nature_code(q.get("nature", "qcm"))

            # ----- Réponses (vider pour HOTSPOT/DRAG DROP) + maxr -----
            answers = q.get("answers") or []
            if q_nature_code in (4, 5):          # matching / drag-n-drop
                answers = []                      # on ignore TOUTES les réponses
            maxr = max(2, min(15, len(answers))) if answers else 2

            # ----- INSERT question (skip si doublon via UNIQUE) -----
            try:
                cur.execute(
                    """
                    INSERT INTO questions (text, level, descr, nature, ty, maxr, module)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (question_text, q_level_code, None, q_nature_code, q_scenario_code, maxr, data["module_id"]),
                )
                question_id = cur.lastrowid
                q_imported += 1
            except Exception as e:
                if getattr(e, "errno", None) == 1062:
                    # ❌ NE PAS faire conn.rollback() ici : ça annule les inserts précédents
                    q_skipped += 1
                    continue  # on passe juste à la question suivante
                raise

            # ----- INSERT answers + quest_ans (réutiliser si doublon) -----
            for ans in answers:
                raw_val = (ans.get("value") or ans.get("text") or "").strip()
                if not raw_val:
                    continue

                answer_data = {
                    k: v for k, v in ans.items() if k not in ("isok", "value", "text")
                }
                answer_data["value"] = raw_val
                a_json = json.dumps(answer_data, ensure_ascii=False)[:700]
                isok = 1 if int(ans.get("isok") or 0) == 1 else 0

                try:
                    cur.execute("INSERT INTO answers (text) VALUES (%s)", (a_json,))
                    answer_id = cur.lastrowid
                    a_imported += 1
                except Exception as e:
                    if getattr(e, "errno", None) == 1062:
                        # déjà présent -> récupérer l'id existant
                        cur.execute("SELECT id FROM answers WHERE text=%s LIMIT 1", (a_json,))
                        row = cur.fetchone()
                        if not row:
                            # cas pathologique : on saute cette réponse
                            continue
                        answer_id = row[0]
                        a_reused += 1
                    else:
                        raise

                # Lien quest_ans (ignore si déjà présent)
                try:
                    cur.execute(
                        "INSERT INTO quest_ans (isok, question, answer) VALUES (%s, %s, %s)",
                        (isok, question_id, answer_id),
                    )
                except Exception as e:
                    if getattr(e, "errno", None) == 1062:
                        pass  # paire déjà liée → on ignore
                    else:
                        raise

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass
        conn.close()

    return jsonify({
        "status": "ok",
        "imported_questions": q_imported,
        "skipped_questions": q_skipped,
        "imported_answers": a_imported,
        "reused_answers": a_reused
    })

