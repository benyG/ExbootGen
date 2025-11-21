import os
import json
import time
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template
from werkzeug.utils import secure_filename

from routes_pdf import detect_questions, extract_text_from_pdf, db_conn
from openai_api import generate_questions
from config import DISTRIBUTION, API_REQUEST_DELAY
import db

# -------- Blueprint / Templates --------
pdf_bp = Blueprint('pdf', __name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mémoire légère (session d’analyse)
# Chaque session stocke l'identifiant du module/domaine sous la clé
# ``domain_id`` (nouveau flux) ou ``module_id`` (ancien flux /upload-pdf).
# On gère donc les deux pour rétrocompatibilité.
SESSIONS = {}  # { session_id: { "domain_id"|"module_id": int, "questions": [...] } }

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
@pdf_bp.route("/api/domains/<int:cert_id>")
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
        domain_id = int(request.form.get("domain_id", 0))
        num_questions = int(request.form.get("num_questions", 0))
    except ValueError:
        return jsonify({"status": "error", "message": "Paramètres invalides"}), 400

    use_distribution = request.form.get("use_distribution") == "on"
    q_type = request.form.get("q_type", "qcm")
    level = request.form.get("level", "medium")
    scenario = request.form.get("scenario", "no")
    scenario_illustration_type = request.form.get("scenario_illustration_type", "none")

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

    # Récupération des noms provider, certification et domaine
    conn = db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM provs WHERE id=%s", (provider_id,))
        prov_row = cur.fetchone()
        cur.execute("SELECT name FROM courses WHERE id=%s", (cert_id,))
        cert_row = cur.fetchone()
        cur.execute("SELECT name FROM modules WHERE id=%s", (domain_id,))
        dom_row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

    provider_name = prov_row[0] if prov_row else ""
    cert_name = cert_row[0] if cert_row else ""
    domain_name = dom_row[0] if dom_row else ""

    # Distribution des questions
    pairs = []  # (q_type, scenario, scenario_illu, count)
    if use_distribution:
        dist = DISTRIBUTION.get(level, {})
        base_total = sum(sum(s.values()) for s in dist.values()) or 1
        scale = num_questions / base_total
        for qt, scen_dict in dist.items():
            for scen, base_count in scen_dict.items():
                count = int(round(base_count * scale))
                if count > 0:
                    pairs.append([qt, scen, "none", count])
        total = sum(p[3] for p in pairs)
        if pairs and total != num_questions:
            pairs[0][3] += num_questions - total
    else:
        pairs.append([q_type, scenario, scenario_illustration_type, num_questions])

    chunk_size = 4000
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [text]

    questions = []
    chunk_idx = 0
    num_chunks = len(chunks)
    for qt, scen, scen_illu, count in pairs:
        remaining = count
        while remaining > 0:
            chunk = chunks[chunk_idx % num_chunks]
            chunk_idx += 1
            to_generate = min(remaining, 5)
            try:
                data = generate_questions(
                    provider_name=provider_name,
                    certification=cert_name,
                    domain=domain_name,
                    domain_descr=chunk,
                    level=level,
                    q_type=qt,
                    practical=scen,
                    scenario_illustration_type=scen_illu,
                    num_questions=to_generate,
                    use_text=True,
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
    SESSIONS[session_id] = {"domain_id": domain_id, "questions": questions}
    return jsonify({"status": "ok", "session_id": session_id, "json_data": {"questions": questions}})

# -------------------- Upload / Analyse PDF --------------------

@pdf_bp.route("/upload-pdf", methods=["POST"])
def upload_pdf():
    module_id_raw = request.form.get("module_id")
    try:
        module_id = int(module_id_raw) if module_id_raw is not None else None
    except ValueError:
        return jsonify({"status": "error", "message": "module_id invalide"}), 400

    uploaded_files = request.files.getlist("file")
    file_paths_field = request.form.get("file_paths")
    legacy_file_path = request.form.get("file_path")

    file_paths = []
    if file_paths_field:
        try:
            parsed = json.loads(file_paths_field)
            if isinstance(parsed, list):
                file_paths.extend([p for p in parsed if isinstance(p, str) and p.strip()])
        except Exception:
            return jsonify({"status": "error", "message": "Liste de fichiers invalide"}), 400
    elif legacy_file_path:
        file_paths.append(legacy_file_path)

    for file in uploaded_files:
        if not file:
            continue
        filename = secure_filename(file.filename or "upload.pdf")
        save_path = UPLOAD_DIR / filename
        file.save(str(save_path))
        file_paths.append(str(save_path))

    if not file_paths:
        return jsonify({"status": "error", "message": "Aucun fichier fourni"}), 400

    def process_pdf(path):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Fichier introuvable: {path}")
        text = extract_text_from_pdf(
            path,
            use_ocr=False,
            skip_first_page=True,
            header_ratio=0.10,
            footer_ratio=0.10
        )
        data = detect_questions(text, module_id)
        filename = os.path.basename(path)
        for q in data.get("questions", []):
            q["src_file"] = filename
        return {"filename": filename, "json_data": data}

    results = []
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(file_paths))) as executor:
            futures = {executor.submit(process_pdf, p): p for p in file_paths}
            for fut in futures:
                results.append(fut.result())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    session_id = os.urandom(8).hex()
    SESSIONS[session_id] = {"domain_id": module_id, "files": results}

    return jsonify({"status": "ok", "session_id": session_id, "files": results})

# -------------------- Import BD --------------------

@pdf_bp.route("/import-questions", methods=["POST"])
def import_questions():
    session_id = request.form.get("session_id")
    if not session_id or session_id not in SESSIONS:
        return jsonify({"status": "error", "message": "Session introuvable"}), 400

    data = SESSIONS[session_id]
    module_id = data.get("domain_id") or data.get("module_id")
    if module_id is None:
        return jsonify({"status": "error", "message": "Aucun module/domaine dans la session"}), 400

    try:
        if "files" in data:
            totals = {
                "imported_questions": 0,
                "skipped_questions": 0,
                "imported_answers": 0,
                "reused_answers": 0,
            }
            for entry in data.get("files", []):
                json_data = entry.get("json_data") or {}
                filename = entry.get("filename")
                for q in json_data.get("questions", []):
                    q.setdefault("src_file", filename)
                stats = db.insert_questions(module_id, json_data, "no")
                for key in totals:
                    totals[key] += stats.get(key, 0)
            return jsonify({"status": "ok", **totals})
        else:
            questions = data.get("questions", [])
            for q in questions:
                q.setdefault("src_file", data.get("filename"))
            stats = db.insert_questions(module_id, {"questions": questions}, "no")
            return jsonify({"status": "ok", **stats})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
