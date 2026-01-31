import base64
import io
import os
import re
import json
import uuid
import html
from pathlib import Path
from typing import Dict, Any, List, Optional

import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import mysql.connector
from google.cloud import storage
from flask import Blueprint, request, jsonify
from pdf2image import convert_from_path
from config import DB_CONFIG, GCS_BUCKET_NAME, GCS_UPLOAD_FOLDER

routes_pdf = Blueprint("routes_pdf", __name__)

# Dossier d’upload partagé
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
if UPLOAD_DIR.exists() and not UPLOAD_DIR.is_dir():
    backup_path = UPLOAD_DIR.with_suffix(UPLOAD_DIR.suffix + ".bak")
    backup_path.write_bytes(UPLOAD_DIR.read_bytes())
    UPLOAD_DIR.unlink()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
else:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------- DB -------------------------------

def db_conn():
    """Connexion MySQL (paramètres dans config.DB_CONFIG)."""
    return mysql.connector.connect(**DB_CONFIG)

# ------------------------- Extraction du texte -------------------------

_OCR_CONFIDENCE_MIN = 40
_OCR_WORDS_MIN = 4
_TABLE_MIN_ROWS = 2
_TABLE_MIN_COLS = 2
_TABLE_COL_TOLERANCE = 40


def _clean_extracted_text(page_txt: str) -> str:
    page_txt = re.sub(r"(\w)-\n(\w)", r"\1\2", page_txt)
    page_txt = re.sub(r"(?im)^\s*(page\s*)?\d+\s*(/\s*\d+)?\s*$", "", page_txt)
    page_txt = re.sub(r"\n{3,}", "\n\n", page_txt).strip()
    return page_txt


def _is_garbled_text(text: str) -> bool:
    tokens = re.findall(r"[\w’']+", text)
    if len(tokens) < 10:
        return False
    single_letters = sum(1 for tok in tokens if len(tok) == 1)
    avg_len = sum(len(tok) for tok in tokens) / len(tokens)
    return single_letters / len(tokens) > 0.45 or avg_len < 2.1


def _extract_page_ocr_text(page: fitz.Page, clip: fitz.Rect) -> str:
    pix = page.get_pixmap(clip=clip, dpi=220)
    pil_img = _pixmap_to_pil(pix)
    return pytesseract.image_to_string(pil_img, lang="fra+eng")


def _pixmap_to_pil(pix: fitz.Pixmap) -> Image.Image:
    mode = "RGB" if pix.alpha == 0 else "RGBA"
    return Image.frombytes(mode, [pix.width, pix.height], pix.samples)


def _upload_pil_to_gcs(pil_img: Image.Image) -> str | None:
    buffer = io.BytesIO()
    pil_img.save(buffer, format="PNG")
    buffer.seek(0)
    object_name = f"{GCS_UPLOAD_FOLDER.rstrip('/')}/{uuid.uuid4().hex}.png"
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(object_name)
        blob.upload_from_file(buffer, content_type="image/png")
        return blob.public_url
    except Exception:
        return None


def _image_to_data_uri(pil_img: Image.Image) -> str:
    buffer = io.BytesIO()
    pil_img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _extract_ocr_words(pil_img: Image.Image) -> list[dict]:
    data = pytesseract.image_to_data(pil_img, lang="fra+eng", output_type=pytesseract.Output.DICT)
    words = []
    count = len(data.get("text", []))
    for i in range(count):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data.get("conf", [0])[i])
        except Exception:
            conf = 0.0
        words.append(
            {
                "text": text,
                "conf": conf,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
                "line_num": int(data.get("line_num", [0])[i] or 0),
            }
        )
    return words


def _infer_table_from_words(words: list[dict]) -> str | None:
    if not words:
        return None
    strong_words = [w for w in words if w["conf"] >= _OCR_CONFIDENCE_MIN]
    if len(strong_words) < _OCR_WORDS_MIN:
        return None

    lines: dict[int, list[dict]] = {}
    for w in strong_words:
        line_key = w["line_num"]
        lines.setdefault(line_key, []).append(w)
    row_candidates = [sorted(line, key=lambda w: w["left"]) for line in lines.values() if len(line) >= _TABLE_MIN_COLS]
    if len(row_candidates) < _TABLE_MIN_ROWS:
        return None

    x_centers = []
    for line in row_candidates:
        for w in line:
            x_centers.append(w["left"] + w["width"] / 2)
    x_centers.sort()
    columns = []
    for x in x_centers:
        if not columns or abs(x - columns[-1]) > _TABLE_COL_TOLERANCE:
            columns.append(x)
        else:
            columns[-1] = (columns[-1] + x) / 2
    if len(columns) < _TABLE_MIN_COLS:
        return None

    rows_html = []
    for line in row_candidates:
        cells = [""] * len(columns)
        for w in line:
            center = w["left"] + w["width"] / 2
            idx = min(range(len(columns)), key=lambda i: abs(columns[i] - center))
            if cells[idx]:
                cells[idx] += f" {w['text']}"
            else:
                cells[idx] = w["text"]
        cell_html = "".join(f"<td>{html.escape(cell.strip())}</td>" for cell in cells)
        rows_html.append(f"<tr>{cell_html}</tr>")
    return "<table>" + "".join(rows_html) + "</table>"


def _classify_image_content(pil_img: Image.Image) -> dict:
    words = _extract_ocr_words(pil_img)
    avg_conf = 0.0
    if words:
        avg_conf = sum(w["conf"] for w in words) / len(words)
    has_text = len(words) >= _OCR_WORDS_MIN and avg_conf >= _OCR_CONFIDENCE_MIN
    table_html = _infer_table_from_words(words) if has_text else None
    return {
        "has_text": has_text,
        "text": " ".join(w["text"] for w in words).strip(),
        "table_html": table_html,
    }


def _extract_visual_block(page: fitz.Page, rect: fitz.Rect) -> str:
    pix = page.get_pixmap(clip=rect, dpi=200)
    pil_img = _pixmap_to_pil(pix)
    analysis = _classify_image_content(pil_img)
    if analysis["table_html"]:
        return analysis["table_html"]
    if analysis["has_text"] and analysis["text"]:
        return analysis["text"]
    url = _upload_pil_to_gcs(pil_img)
    if not url:
        url = _image_to_data_uri(pil_img)
    return f'<img src="{url}" alt="pdf-image" />'


def _extract_mixed_content(page: fitz.Page, clip: fitz.Rect) -> str:
    parts = []
    blocks = page.get_text("blocks", clip=clip)
    blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
    for x0, y0, x1, y1, text, _, block_type in blocks:
        rect = fitz.Rect(x0, y0, x1, y1)
        if block_type == 0:
            cleaned = _clean_extracted_text(text)
            if cleaned:
                parts.append(cleaned)
        elif block_type == 1:
            parts.append(_extract_visual_block(page, rect))
    return "\n".join(parts).strip()


def extract_text_from_pdf(pdf_path: str,
                          use_ocr: bool = False,
                          skip_first_page: bool = True,
                          header_ratio: float = 0.10,
                          footer_ratio: float = 0.10,
                          detect_visuals: bool = False) -> str:
    """
    Extrait le texte utile :
      - ignore la 1ère page (skip_first_page=True)
      - supprime l'en-tête et le pied de page (10% haut/bas par défaut)
    """
    text = ""

    if use_ocr:
        images = convert_from_path(pdf_path)
        if skip_first_page and images:
            images = images[1:]
        for img in images:
            w, h = img.size
            top = int(h * header_ratio)
            bottom = int(h * (1.0 - footer_ratio))
            if bottom <= top:
                top, bottom = 0, h
            cropped = img.crop((0, top, w, bottom))
            txt = pytesseract.image_to_string(cropped, lang="fra+eng")
            text += txt + "\n"
        return text

    # Extraction native via PyMuPDF
    with fitz.open(pdf_path) as doc:
        start_idx = 1 if (skip_first_page and doc.page_count > 0) else 0
        for i in range(start_idx, doc.page_count):
            page = doc[i]
            h = page.rect.height
            top_cut = h * header_ratio
            bottom_cut = h * (1.0 - footer_ratio)

            clip = fitz.Rect(0, top_cut, page.rect.width, bottom_cut)
            if detect_visuals:
                page_txt = _extract_mixed_content(page, clip)
            else:
                page_txt = page.get_text("text", clip=clip)
                page_txt = _clean_extracted_text(page_txt)

            if _is_garbled_text(page_txt):
                ocr_txt = _extract_page_ocr_text(page, clip)
                page_txt = _clean_extracted_text(ocr_txt)

            if page_txt:
                text += page_txt + "\n"

    return text

# --------------------------- Parsing des questions ---------------------------

# --- Helpers: segmentation & parsing ---

_NEWQ_RE = re.compile(r'(?im)^\s*NEW\s+QUESTION\s+(\d+)\b')  # ancre prioritaire
_QUESTION_RE = re.compile(r'(?im)^\s*QUESTION\s*\d+\b')
_NUMBERED_Q_RE = re.compile(r'(?m)^\s*\d+\s*[.)]\s+\S')
_OPT_RE  = re.compile(r'^\s*([A-Oa-o])[\.\)]\s*(.+)$')        # A. / B) / c. ...
_ANS_RE  = re.compile(r'(?im)^\s*Answer\s*:\s*(.+)$')
_EMBEDDED_Q_RE = re.compile(r'(?im)^\s*(?:NEW\s+QUESTION\s+\d+|QUESTION\s*\d+)\b')


def analyze_question_markers(text: str) -> dict:
    """Pré-analyse : compte les marqueurs de questions pour estimer le total."""
    counts = {
        "new_question": len(_NEWQ_RE.findall(text)),
        "question_label": len(_QUESTION_RE.findall(text)),
        "numbered": len(_NUMBERED_Q_RE.findall(text)),
    }
    method = "unknown"
    total_expected = 0
    for key in ("new_question", "question_label", "numbered"):
        if counts[key] > 0:
            method = key
            total_expected = counts[key]
            break
    return {"counts": counts, "method": method, "total_expected": total_expected}

def _split_blocks(text: str) -> list[tuple[str, str]]:
    """
    Segmente en priorité sur 'NEW QUESTION X'.
    Retourne une liste [(numéro_ou_none, bloc_texte_sans_en-tête), ...]
    Repli: si aucune 'NEW QUESTION', on segmente sur 'QUESTION n' ou 'n.' (moins fiable).
    """
    # 1) Split prioritaire NEW QUESTION X
    parts = re.split(r'(?i)\bNEW\s+QUESTION\s+(\d+)\b', text)
    blocks = []
    if len(parts) > 1:
        # parts = [avant, num1, bloc1, num2, bloc2, ...]
        it = iter(parts[1:])
        for num, blk in zip(it, it):
            blocks.append((num.strip(), blk.strip()))
    else:
        # 2) Repli (legacy)
        raw_blocks = re.split(r'(?:^|\n)(?:QUESTION\s*\d+|\d+\.)\s*', text, flags=re.I)
        for blk in raw_blocks:
            blk = (blk or "").strip()
            if blk:
                blocks.append((None, blk))
    return blocks

def _strip_after_explanation(block: str) -> str:
    """
    Coupe tout ce qui suit 'Explanation' ou 'Reference' (souvent du bruit dans les dumps).
    """
    lines = [l for l in block.splitlines()]
    cut_idx = None
    for i, l in enumerate(lines):
        if re.match(r'(?i)^\s*(Explanation|Reference)\b', l):
            cut_idx = i
            break
    if cut_idx is not None:
        lines = lines[:cut_idx]
    return "\n".join(lines).strip()


def _split_embedded_blocks(block: str) -> list[str]:
    matches = list(_EMBEDDED_Q_RE.finditer(block))
    if not matches:
        return [block]
    starts = [m.start() for m in matches]
    if starts[0] != 0:
        starts.insert(0, 0)
    segments = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(block)
        seg = block[start:end].strip()
        if seg:
            segments.append(seg)
    return segments


def _strip_leading_marker(block: str) -> str:
    lines = block.splitlines()
    if lines and _EMBEDDED_Q_RE.match(lines[0]):
        cleaned = re.sub(r'(?i)^\s*(NEW\s+QUESTION\s+\d+|QUESTION\s*\d+)\b[:\s-]*', "", lines[0]).strip()
        if cleaned:
            lines[0] = cleaned
        else:
            lines = lines[1:]
    return "\n".join(lines).strip()

# --- Fonction principale: détecter les questions ---

def detect_questions(text: str, module_id: int, analysis: Optional[dict] = None) -> dict:
    """
    Parse le texte en questions/réponses.
    Priorités :
      - segmentation par 'NEW QUESTION X'
      - coupe 'Explanation' / 'Reference'
      - RÈGLE SPÉCIALE : HOTSPOT / DRAG DROP
          * nature = matching (4) / drag-n-drop (5)
          * importées SANS réponses (on ignore A./B./... et 'Answer:')
      - Pour les autres questions : au moins 2 réponses
    """
    questions = []
    dropped_too_many_answers = 0
    dropped_no_answers = 0
    if analysis is None:
        analysis = analyze_question_markers(text)

    for qnum, raw_block in _split_blocks(text):
        for embedded_block in _split_embedded_blocks(raw_block):
            block = _strip_leading_marker(embedded_block)
            block = _strip_after_explanation(block)

            # Extrait la/les réponses correctes (Answer: ...)
            correct_tokens = set()
            m_ans = _ANS_RE.search(block)
            if m_ans:
                ans_raw = m_ans.group(1)
                correct_tokens = {
                    tok.strip().upper()
                    for tok in re.findall(r'[A-O]|True|False', ans_raw, re.I)
                }
            # Retire la ligne "Answer: ..." du bloc
            block = _ANS_RE.sub("", block).strip()

            # Lignes utiles
            lines = [l.rstrip() for l in block.splitlines() if l.strip()]
            if not lines:
                continue

            # Détection HOTSPOT / DRAG DROP
            special_nature = None
            first_line = lines[0]
            if re.match(r'^\s*HOTSPOT\b', first_line, re.I):
                special_nature = "matching"  # type 4
                # retire la ligne 'HOTSPOT ...'
                lines = lines[1:]
            elif re.match(r'^\s*DRAG\s*DROP\b', first_line, re.I):
                special_nature = "drag-n-drop"  # type 5
                # retire la ligne 'DRAG DROP ...'
                lines = lines[1:]

            # Certains dumps ont une ligne "- (Topic ...)" juste après
            if lines and re.match(r'^\s*[-–—]\s*\(.*?\)\s*$', lines[0]):
                lines = lines[1:]

            # Cherche le premier choix A./B)/... pour borner l'énoncé
            first_opt_idx = None
            for i, l in enumerate(lines):
                if _OPT_RE.match(l):
                    first_opt_idx = i
                    break

            # Cas spécial HOTSPOT / DRAG DROP : on ignore toutes les réponses
            if special_nature:
                # Énoncé = tout avant les options (si présentes), sinon toutes les lignes
                if first_opt_idx is not None:
                    core_lines = lines[:first_opt_idx]
                else:
                    core_lines = lines
                question_text = " ".join(core_lines).strip()
                if not question_text:
                    continue
                questions.append({
                    "context": "",
                    "text": question_text,
                    "scenario": "no",
                    "level": "medium",
                    "nature": special_nature,  # 'matching' ou 'drag-n-drop'
                    "answers": []              # toujours vide
                })
                continue

            # --------- cas "classiques" (QCM / True-False) ----------
            answers = []
            nature = "qcm"

            if first_opt_idx is None:
                # Heuristique True/False
                has_tf_hint = any(re.search(r'\b(True|False)\b', ln, re.I) for ln in lines)
                if has_tf_hint or (correct_tokens and correct_tokens.issubset({"TRUE", "FALSE"})):
                    nature = "truefalse"
                    question_text = " ".join(lines).strip()
                    answers = [
                        {"value": "True",  "target": None, "isok": 1 if "TRUE" in correct_tokens else 0},
                        {"value": "False", "target": None, "isok": 1 if "FALSE" in correct_tokens else 0},
                    ]
                else:
                    question_text = " ".join(lines).strip()
                    answers = []
            else:
                # Énoncé
                question_text = " ".join(lines[:first_opt_idx]).strip()
                if not question_text:
                    question_text = " ".join(lines).strip()

                # Réponses multi-lignes
                cur_letter = None
                cur_text_parts = []

                def flush_current():
                    nonlocal answers, cur_letter, cur_text_parts
                    if cur_letter is None:
                        return
                    txt = " ".join(t.strip() for t in cur_text_parts).strip()
                    if not txt:
                        return
                    clean = txt.replace("*", "").replace("(Correct)", "").strip()
                    isok = 1 if cur_letter in correct_tokens else 0
                    answers.append({"value": clean[:700], "target": None, "isok": isok})
                    cur_letter, cur_text_parts = None, []

                for l in lines[first_opt_idx:]:
                    m = _OPT_RE.match(l)
                    if m:
                        flush_current()
                        cur_letter = m.group(1).upper()
                        first_text = m.group(2).strip()
                        cur_text_parts = [first_text]
                    else:
                        if cur_letter is not None:
                            cur_text_parts.append(l.strip())
                flush_current()

            if not question_text:
                continue

            if nature == "qcm" and len(answers) > 6:
                dropped_too_many_answers += 1
                continue

            if nature == "qcm":
                normalized_answers = {
                    re.sub(r"\s+", " ", (a.get("value") or "").strip().lower())
                    for a in answers
                    if a.get("value")
                }
                if normalized_answers == {"mastered", "not mastered"}:
                    nature = "drag-n-drop"
                    answers = []

            if nature == "qcm" and not answers:
                dropped_no_answers += 1
                continue

            questions.append({
                "context": "",
                "text": question_text,
                "scenario": "no",
                "level": "medium",
                "nature": nature,
                "answers": answers
            })

    extracted_count = len(questions)
    expected_count = analysis.get("total_expected") or extracted_count
    gap = expected_count - extracted_count
    report = {
        "expected_questions": expected_count,
        "extracted_questions": extracted_count,
        "gap": gap,
        "method": analysis.get("method"),
        "marker_counts": analysis.get("counts", {}),
        "questions_without_answers": sum(1 for q in questions if not q.get("answers")),
        "dropped_too_many_answers": dropped_too_many_answers,
        "dropped_no_answers": dropped_no_answers,
    }

    return {"module_id": module_id, "questions": questions, "analysis": report}

# ---------------------- Routes optionnelles (Blueprint) ----------------------

@routes_pdf.route("/upload-pdf", methods=["POST"])
def upload_pdf_route():
    module_id = request.form.get("module_id")
    try:
        module_id = int(module_id) if module_id is not None else None
    except ValueError:
        return jsonify({"status": "error", "message": "module_id invalide"}), 400

    file = request.files.get("file")
    if not file:
        return jsonify({"status": "error", "message": "Aucun fichier envoyé"}), 400

    filename = os.path.basename(file.filename or "upload.pdf")
    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)

    text = extract_text_from_pdf(
        save_path,
        use_ocr=False,
        skip_first_page=True,
        header_ratio=0.10,
        footer_ratio=0.10,
        detect_visuals=True,
    )
    data = detect_questions(text, module_id)
    session_id = str(uuid.uuid4())

    tmp_json = os.path.join(UPLOAD_DIR, f"{session_id}.json")
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"status": "ok", "session_id": session_id, "json_data": data})

@routes_pdf.route("/import-questions", methods=["POST"])
def import_questions_route():
    session_id = request.form.get("session_id")
    tmp_json = os.path.join(UPLOAD_DIR, f"{session_id}.json")
    if not os.path.exists(tmp_json):
        return jsonify({"status": "error", "message": "Session introuvable"}), 400

    with open(tmp_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    NATURE_MAP = {"qcm": 1, "truefalse": 2}
    q_imported = 0
    a_imported = 0

    conn = db_conn()
    try:
        cur = conn.cursor()
        for q in data.get("questions", []):
            q_text = (q.get("text") or "").strip()
            q_level = int(q.get("level") or 1)
            q_nature = NATURE_MAP.get((q.get("nature") or "qcm").lower(), 1)
            answers = q.get("answers") or []
            maxr = max(2, min(15, len(answers))) if answers else 2

            descr_parts = []
            if q.get("scenario"):
                descr_parts.append(f"[scenario]={q['scenario']}")
            if q.get("context"):
                descr_parts.append(str(q["context"]))
            q_descr = "\n".join(descr_parts) if descr_parts else None

            cur.execute(
                """
                INSERT INTO questions (text, level, descr, nature, maxr, module)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (q_text, q_level, q_descr, q_nature, maxr, data["module_id"]),
            )
            question_id = cur.lastrowid
            q_imported += 1

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

                cur.execute("INSERT INTO answers (text) VALUES (%s)", (a_json,))
                answer_id = cur.lastrowid
                a_imported += 1

                cur.execute(
                    "INSERT INTO quest_ans (isok, question, answer) VALUES (%s, %s, %s)",
                    (isok, question_id, answer_id),
                )

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

    return jsonify({"status": "ok", "imported_questions": q_imported, "imported_answers": a_imported})
