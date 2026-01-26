import os
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

from routes_pdf import detect_questions, extract_text_from_pdf, db_conn
from openai_api import generate_questions
from config import DISTRIBUTION, API_REQUEST_DELAY
import db

import fitz  # PyMuPDF

# -------- Blueprint / Templates --------
pdf_bp = Blueprint('pdf', __name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Base directory allowed for PDF discovery to avoid walking arbitrary paths
PDF_SEARCH_ROOT = Path(os.environ.get("PDF_SEARCH_ROOT", UPLOAD_DIR)).resolve()
os.makedirs(PDF_SEARCH_ROOT, exist_ok=True)

# Répertoire par défaut pour le champ "Dossier PDF" côté UI
DEFAULT_PDF_DIRECTORY = os.environ.get("DEFAULT_PDF_DIR", r"C:\\dumps\\dumps")

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


# -------------------- Helpers : données certification --------------------


def _fetch_certification(conn, cert_id: int) -> dict | None:
    """Return certification info with a fallback for missing code/code_cert columns."""

    cur = conn.cursor(dictionary=True)
    try:
        try:
            cur.execute(
                "SELECT id, name, code, descr2 AS code_cert FROM courses WHERE id=%s",
                (cert_id,),
            )
        except Exception:
            cur.execute(
                "SELECT id, name, descr2 AS code_cert FROM courses WHERE id=%s",
                (cert_id,),
            )
        row = cur.fetchone()
        if not row:
            return None
        if "code" not in row or not row.get("code"):
            # Fallback to a code stored on modules or default to the name.
            cur.execute(
                "SELECT code_cert FROM modules WHERE course=%s AND code_cert IS NOT NULL AND code_cert<>'' LIMIT 1",
                (cert_id,),
            )
            code_row = cur.fetchone()
            row["code"] = (code_row or {}).get("code_cert") or row.get("name")
        if "code_cert" not in row or not row.get("code_cert"):
            row["code_cert"] = row.get("code") or row.get("name")
        return row
    finally:
        cur.close()


def _fetch_domains(conn, cert_id: int) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM modules WHERE course=%s ORDER BY name", (cert_id,))
        return [name for (name,) in cur.fetchall()]
    finally:
        cur.close()


def _fetch_default_module_id(conn, code_cert: str) -> int | None:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM modules WHERE code_cert = %s ORDER BY id DESC LIMIT 1",
            (code_cert,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        cur.close()


def _parse_answer(raw: str) -> str:
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "value" in data:
            return str(data.get("value", "")).strip() or raw
    except Exception:
        pass
    return raw


def _fetch_random_questions(conn, cert_id: int, limit: int) -> list[dict]:
    """Return random QCM questions for a certification with their answers."""

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT q.id, q.text, m.name AS module_name
            FROM questions q
            JOIN modules m ON m.id = q.module
            WHERE m.course = %s AND q.nature = 1
            ORDER BY RAND()
            LIMIT %s
            """,
            (cert_id, limit),
        )
        questions = cur.fetchall()
        if not questions:
            return []

        question_ids = [q["id"] for q in questions]
        placeholders = ",".join(["%s"] * len(question_ids))
        cur.execute(
            f"""
            SELECT qa.question, qa.isok, a.text
            FROM quest_ans qa
            JOIN answers a ON a.id = qa.answer
            WHERE qa.question IN ({placeholders})
            ORDER BY qa.question, qa.id
            """,
            question_ids,
        )
        answer_map: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            answer_map.setdefault(row["question"], []).append(
                {"text": _parse_answer(row["text"]), "isok": bool(row["isok"])}
            )

        for q in questions:
            q["answers"] = answer_map.get(q["id"], [])
        return questions
    finally:
        cur.close()


# -------------------- Helpers : PDF --------------------


def _replace_placeholder(page: fitz.Page, placeholder: str, value: str, *, fontsize: int = 14):
    areas = list(page.search_for(placeholder))
    if not areas:
        return
    expanded = [fitz.Rect(r.x0 - 2, r.y0 - 2, r.x1 + 2, r.y1 + 2) for r in areas]
    for rect in expanded:
        page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    for rect in expanded:
        page.insert_textbox(
            rect,
            value,
            fontsize=fontsize,
            fontname="helvetica",
            align=1,
            color=(0, 0, 0),
        )


def _clean_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _wrap_lines(text: str, max_width: float, *, fontname: str, fontsize: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not candidate:
            continue
        width = fitz.get_text_length(candidate, fontname=fontname, fontsize=fontsize)
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if not lines:
        lines.append("")
    return lines


def _render_question_lines(question: dict, idx: int, max_width: float, *, fontsize: int = 10) -> list[tuple[str, str]]:
    """Return [(text, fontname)] lines formatted like the sample screenshot."""

    font_regular = "helvetica"
    font_bold = "helvetica-bold"

    lines: list[tuple[str, str]] = [(f"QUESTION {idx}", font_bold)]
    lines.append(("", font_regular))

    question_text = _clean_text(question.get("text", ""))
    for line in _wrap_lines(question_text, max_width, fontname=font_regular, fontsize=fontsize):
        lines.append((line, font_regular))

    lines.append(("", font_regular))

    answers = question.get("answers", [])
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    correct_letters: list[str] = []
    for i, ans in enumerate(answers):
        label = letters[i] if i < len(letters) else f"Opt{i+1}"
        answer_text = _clean_text(ans.get("text", ""))
        for line in _wrap_lines(f"{label}. {answer_text}", max_width, fontname=font_regular, fontsize=fontsize):
            lines.append((line, font_regular))
        if ans.get("isok"):
            correct_letters.append(label)

    lines.append(("", font_regular))

    if correct_letters:
        lines.append((f"Answer: {', '.join(correct_letters)}", font_bold))

    return lines

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
        cur.execute(
            """
            SELECT c.id, c.name, c.descr2 AS code_cert
            FROM courses c
            WHERE c.prov = %s
            ORDER BY c.name
            """,
            (provider_id,),
        )
        return jsonify(cur.fetchall())
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

@pdf_bp.route("/api/modules/<int:cert_id>")
@pdf_bp.route("/api/domains/<int:cert_id>")
def api_modules(cert_id):
    conn = db_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, name, code_cert FROM modules WHERE course=%s ORDER BY name",
            (cert_id,),
        )
        return jsonify(cur.fetchall())
    finally:
        try: cur.close()
        except Exception: pass
        conn.close()


@pdf_bp.route("/api/update-code-cert", methods=["POST"])
def api_update_code_cert():
    data = request.get_json(silent=True) or {}
    cert_id = data.get("cert_id")
    module_id = data.get("module_id")
    code_cert = (data.get("code_cert") or "").strip()

    if not cert_id or not module_id or not code_cert:
        return jsonify({"status": "error", "message": "cert_id, module_id et code_cert requis."}), 400

    conn = db_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE courses SET descr2 = %s WHERE id = %s", (code_cert, cert_id))
            cur.execute("UPDATE modules SET code_cert = %s WHERE id = %s", (code_cert, module_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return jsonify({"status": "error", "message": str(exc)}), 500
        finally:
            cur.close()
    finally:
        conn.close()

    return jsonify({"status": "ok", "code_cert": code_cert})


@pdf_bp.route("/api/resolve-cert-by-code")
def api_resolve_cert_by_code():
    code_cert = (request.args.get("code_cert") or "").strip()
    if not code_cert:
        return jsonify({"cert_id": None, "provider_id": None})

    conn = db_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id AS cert_id, prov AS provider_id FROM courses WHERE descr2 = %s LIMIT 1",
            (code_cert,),
        )
        row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

    if not row:
        return jsonify({"cert_id": None, "provider_id": None})
    return jsonify(row)

# -------------------- Search PDFs --------------------

@pdf_bp.route("/api/search-pdfs")
def api_search_pdfs():
    """Return a list of PDF files under ``root`` matching query ``q``.

    The search is constrained to ``PDF_SEARCH_ROOT`` to avoid disclosing the
    server filesystem through arbitrary directory traversal.
    """

    def is_within_base(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    raw_root = (request.args.get("root") or "").strip()
    query = (request.args.get("q") or "").lower()
    if not raw_root or not query:
        return jsonify([])

    candidate = Path(raw_root)
    if candidate.is_absolute():
        # On permet désormais les chemins absolus explicitement saisis :
        # s'ils existent et pointent vers un dossier, on les parcourt
        # sans restreindre à PDF_SEARCH_ROOT (l'utilisateur fournit déjà
        # la cible exacte).
        root_path = candidate.resolve()
    else:
        root_path = (PDF_SEARCH_ROOT / candidate).resolve()

    if not root_path.exists() or not root_path.is_dir():
        return jsonify([])

    # Pour les chemins relatifs, on continue de vérifier qu'ils restent
    # confinés au répertoire autorisé.
    if not candidate.is_absolute() and not is_within_base(root_path, PDF_SEARCH_ROOT):
        return jsonify([])

    matches = []
    for dirpath, _, files in os.walk(root_path):
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            if query in name.lower():
                rel_path = os.path.relpath(os.path.join(dirpath, name), root_path)
                matches.append(rel_path)
                if len(matches) >= 20:
                    break
        if len(matches) >= 20:
            break

    return jsonify(matches)


@pdf_bp.route("/api/sync-code-cert", methods=["POST"])
def api_sync_code_cert():
    """Synchronize default modules and their ``code_cert`` values."""
    conn = db_conn()
    try:
        cur = conn.cursor()
        conn.start_transaction()

        cur.execute(
            """
            INSERT INTO modules (name, descr, course, code_cert)
            SELECT
              LEFT(CONCAT(c.name, '-default'), 255) AS name,
              CONCAT('Généré depuis la certification "', c.name, '"') AS descr,
              23 AS course,
              c.descr2 AS code_cert
            FROM courses c
            LEFT JOIN modules m
              ON m.course = 23
             AND m.name   = LEFT(CONCAT(c.name, '-default'), 255)
            WHERE c.id <> 23
              AND m.id IS NULL
            """
        )
        inserted = cur.rowcount

        cur.execute(
            """
            UPDATE modules m
            JOIN courses c
              ON m.course = 23
             AND m.name   = LEFT(CONCAT(c.name, '-default'), 255)
            SET m.code_cert = c.descr2
            WHERE c.id <> 23
            """
        )
        updated = cur.rowcount

        conn.commit()
        return jsonify({"status": "ok", "inserted": inserted, "updated": updated})
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

# -------------------- MCP API --------------------

@pdf_bp.route("/api/mcp/import-local", methods=["POST"])
def api_mcp_import_local():
    """Import questions from local PDFs into a default or specified module."""

    payload = request.get_json(silent=True) or {}
    file_paths = payload.get("file_paths")
    search_root = (payload.get("search_root") or "").strip()
    module_id = payload.get("module_id")
    cert_id = payload.get("cert_id")
    code_cert = (payload.get("code_cert") or "").strip()

    try:
        module_id = int(module_id) if module_id is not None else None
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "module_id invalide"}), 400

    if module_id is None and (cert_id is None and not code_cert):
        return jsonify(
            {
                "status": "error",
                "message": "module_id ou cert_id/code_cert requis",
            }
        ), 400

    if file_paths is not None and not isinstance(file_paths, list):
        return jsonify({"status": "error", "message": "file_paths doit être une liste"}), 400

    conn = db_conn()
    try:
        if module_id is None:
            if not code_cert:
                try:
                    cert_id = int(cert_id)
                except (TypeError, ValueError):
                    return jsonify(
                        {"status": "error", "message": "cert_id invalide"}
                    ), 400
                cert = _fetch_certification(conn, cert_id)
                if not cert:
                    return jsonify(
                        {"status": "error", "message": "Certification introuvable"}
                    ), 404
                code_cert = str(cert.get("code_cert") or "").strip()
            module_id = _fetch_default_module_id(conn, code_cert)

        if module_id is None:
            return jsonify(
                {
                    "status": "error",
                    "message": "Domaine default introuvable (code_cert).",
                }
            ), 404

        def normalize_path(raw_path: str) -> tuple[Path, bool]:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                return candidate.resolve(), True
            return (PDF_SEARCH_ROOT / candidate).resolve(), False

        def resolve_search_root() -> Path:
            if not search_root:
                return PDF_SEARCH_ROOT
            resolved_root, is_absolute = normalize_path(search_root)
            if not resolved_root.exists() or not resolved_root.is_dir():
                raise FileNotFoundError(f"Répertoire introuvable: {search_root}")
            if not is_absolute and not str(resolved_root).startswith(str(PDF_SEARCH_ROOT)):
                raise ValueError(f"Chemin non autorisé: {search_root}")
            return resolved_root

        def collect_files() -> list[Path]:
            if file_paths:
                resolved_files: list[Path] = []
                for raw_path in file_paths:
                    if not isinstance(raw_path, str) or not raw_path.strip():
                        continue
                    resolved, is_absolute = normalize_path(raw_path.strip())
                    if not resolved.exists() or not resolved.is_file():
                        raise FileNotFoundError(f"Fichier introuvable: {raw_path}")
                    if not is_absolute and not str(resolved).startswith(str(PDF_SEARCH_ROOT)):
                        raise ValueError(f"Chemin non autorisé: {raw_path}")
                    if resolved.suffix.lower() != ".pdf":
                        raise ValueError(f"Extension invalide (PDF requis): {raw_path}")
                    resolved_files.append(resolved)
                return resolved_files

            if not code_cert:
                raise ValueError("code_cert requis pour la recherche automatique")

            root_path = resolve_search_root()
            pattern = f"{code_cert.lower()}_"
            matches: list[Path] = []
            for dirpath, _, files in os.walk(root_path):
                for name in files:
                    if not name.lower().endswith(".pdf"):
                        continue
                    lower_name = name.lower()
                    if lower_name.startswith(pattern):
                        matches.append(Path(dirpath) / name)
                if len(matches) >= 200:
                    break
            if not matches:
                raise FileNotFoundError(
                    f"Aucun PDF trouvé pour code_cert {code_cert} dans {root_path}"
                )
            return matches

        totals = {
            "imported_questions": 0,
            "skipped_questions": 0,
            "imported_answers": 0,
            "reused_answers": 0,
            "files": [],
        }

        try:
            resolved_files = collect_files()
        except FileNotFoundError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

        for resolved in resolved_files:
            text = extract_text_from_pdf(
                str(resolved),
                use_ocr=False,
                skip_first_page=True,
                header_ratio=0.10,
                footer_ratio=0.10,
                detect_visuals=True,
            )
            data = detect_questions(text, module_id)
            filename = resolved.name
            for q in data.get("questions", []):
                q.setdefault("src_file", filename)
            stats = db.insert_questions(module_id, data, "no")
            totals["files"].append(
                {
                    "filename": filename,
                    "imported_questions": stats.get("imported_questions", 0),
                    "skipped_questions": stats.get("skipped_questions", 0),
                }
            )
            for key in ("imported_questions", "skipped_questions", "imported_answers", "reused_answers"):
                totals[key] += stats.get(key, 0)

        return jsonify(
            {
                "status": "ok",
                "module_id": module_id,
                "files_count": len(resolved_files),
                **totals,
            }
        )
    finally:
        conn.close()

# -------------------- UI --------------------

@pdf_bp.route("/")
def index():
    return render_template("upload.html", default_pdf_dir=DEFAULT_PDF_DIRECTORY)


@pdf_bp.route("/export")
def export_index():
    return render_template("pdf_export.html")

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
        detect_visuals=True,
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


@pdf_bp.route("/export/questions", methods=["POST"])
def export_questions_pdf():
    try:
        cert_id = int(request.form.get("cert_id", 0))
        num_questions = int(request.form.get("num_questions", 0))
    except ValueError:
        return jsonify({"status": "error", "message": "Paramètres invalides"}), 400

    if cert_id <= 0 or num_questions <= 0:
        return jsonify({"status": "error", "message": "Certification et nombre de questions requis"}), 400

    user_name = (request.form.get("user_name") or "ExamBoot User").strip()
    user_email = (request.form.get("user_email") or "user@example.com").strip()

    template_path = BASE_DIR / "docs" / "PDF-Template-ExamBoot.pdf"
    if not template_path.exists():
        return jsonify({"status": "error", "message": "Template PDF introuvable"}), 500

    conn = db_conn()
    try:
        cert = _fetch_certification(conn, cert_id)
        if not cert:
            return jsonify({"status": "error", "message": "Certification introuvable"}), 404

        domains = _fetch_domains(conn, cert_id)
        questions = _fetch_random_questions(conn, cert_id, num_questions)
        if not questions:
            return jsonify({"status": "error", "message": "Aucune question disponible pour cette certification"}), 400
        if len(questions) < num_questions:
            return jsonify({"status": "error", "message": "Pas assez de questions pour le nombre demandé"}), 400
    finally:
        conn.close()

    export_id = uuid.uuid4().hex
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cert_name = cert.get("name") or ""
    cert_code = cert.get("code") or cert_name
    domain_block = "\n".join(f"- {d}" for d in domains) if domains else "Aucun domaine défini"

    template = fitz.open(template_path)
    output = fitz.open()

    # Page 1 (couverture) + page 2 (sommaire)
    output.insert_pdf(template, from_page=0, to_page=1)

    cover = output[0]
    summary = output[1]

    for page in (cover, summary):
        _replace_placeholder(page, "[username_name]", user_name)
        _replace_placeholder(page, "[user_email]", user_email)
        _replace_placeholder(page, "[date_time_UTC]", now_utc)
        _replace_placeholder(page, "[UUID]", export_id)
        _replace_placeholder(page, "[Certification_Name]", cert_name)
    # Zone CODE sur la couverture
    for rect in cover.search_for("CODE"):
        cover.insert_textbox(rect, cert_code, fontsize=12, fontname="helvetica", align=1, color=(0, 0, 0))

    _replace_placeholder(summary, "[DOMAINES]", domain_block)
    _replace_placeholder(summary, "[EXPORT ID]", export_id)
    _replace_placeholder(summary, "[NOM PRÉNOM]", user_name)
    _replace_placeholder(summary, "[EMAIL]", user_email)

    # Pages des questions (plusieurs questions par page)
    question_template_rect = template[2].rect
    margin = 56
    content_rect = fitz.Rect(
        question_template_rect.x0 + margin,
        question_template_rect.y0 + 140,
        question_template_rect.x1 - margin,
        question_template_rect.y1 - margin,
    )
    max_width = content_rect.width
    fontsize = 10
    line_height = fontsize * 1.5

    def new_question_page():
        page = output.new_page(width=question_template_rect.width, height=question_template_rect.height)
        page.show_pdf_page(page.rect, template, 2)
        _replace_placeholder(page, "[EXPORT ID]", export_id)
        _replace_placeholder(page, "[NOM PRÉNOM]", user_name)
        _replace_placeholder(page, "[EMAIL]", user_email)
        _replace_placeholder(page, "[UUID]", export_id)
        return page

    page = new_question_page()
    cursor_y = content_rect.y0

    for idx, question in enumerate(questions, start=1):
        q_lines = _render_question_lines(question, idx, max_width, fontsize=fontsize)
        needed_height = len(q_lines) * line_height

        if cursor_y + needed_height > content_rect.y1:
            page = new_question_page()
            cursor_y = content_rect.y0

        for text, fontname in q_lines:
            if text:
                page.insert_text(
                    (content_rect.x0, cursor_y),
                    text,
                    fontsize=fontsize,
                    fontname=fontname,
                    color=(0, 0, 0),
                )
            cursor_y += line_height

    # Dernière page
    output.insert_pdf(template, from_page=3, to_page=3)

    closing_page = output[-1]
    _replace_placeholder(closing_page, "[UUID]", export_id)
    _replace_placeholder(closing_page, "[EXPORT ID]", export_id)
    _replace_placeholder(closing_page, "[NOM PRÉNOM]", user_name)
    _replace_placeholder(closing_page, "[EMAIL]", user_email)

    output_path = UPLOAD_DIR / f"export_{export_id}.pdf"
    output.save(output_path)
    output.close()
    template.close()

    download_name = f"{cert_code or 'questions'}.pdf"
    return send_file(
        output_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=download_name,
    )

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
            footer_ratio=0.10,
            detect_visuals=True,
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
