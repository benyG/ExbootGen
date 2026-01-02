import os
import re
import json
import uuid
from typing import Dict, Any, List

import fitz  # PyMuPDF
import pytesseract
import mysql.connector
from flask import Blueprint, request, jsonify
from pdf2image import convert_from_path
from config import DB_CONFIG

routes_pdf = Blueprint("routes_pdf", __name__)

# Dossier d’upload partagé
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
# En cas de collision avec un fichier existant (notamment en environnements de test),
# supprime le fichier pour recréer le dossier attendu par le reste de l'application.
if os.path.isfile(UPLOAD_DIR):
    os.remove(UPLOAD_DIR)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ------------------------------- DB -------------------------------

def db_conn():
    """Connexion MySQL (paramètres dans config.DB_CONFIG)."""
    return mysql.connector.connect(**DB_CONFIG)

# ------------------------- Extraction du texte -------------------------

def extract_text_from_pdf(pdf_path: str,
                          use_ocr: bool = False,
                          skip_first_page: bool = True,
                          header_ratio: float = 0.10,
                          footer_ratio: float = 0.10) -> str:
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

            # Récupère les blocks et ne garde que ceux entièrement dans la zone "corps"
            blocks = page.get_text("blocks")  # (x0,y0,x1,y1, text, ...)
            page_txt_parts = []
            for b in blocks:
                if len(b) < 5:
                    continue
                x0, y0, x1, y1, btxt = b[:5]
                if y0 >= top_cut and y1 <= bottom_cut and btxt and btxt.strip():
                    page_txt_parts.append(btxt)

            page_txt = "\n".join(page_txt_parts)

            # Nettoyage : lignes "Page 3", "3/10", etc.
            page_txt = re.sub(r"(?im)^\s*(page\s*)?\d+\s*(/\s*\d+)?\s*$", "", page_txt)
            page_txt = re.sub(r"\n{3,}", "\n\n", page_txt).strip()

            if page_txt:
                text += page_txt + "\n"

    return text

# --------------------------- Parsing des questions ---------------------------

# --- Helpers: segmentation & parsing ---

_NEWQ_RE = re.compile(r'(?im)^\s*NEW\s+QUESTION\s+(\d+)\b')  # ancre prioritaire
_OPT_RE  = re.compile(r'^\s*([A-Oa-o])[\.\)]\s*(.+)$')        # A. / B) / c. ...
_ANS_RE  = re.compile(r'(?im)^\s*Answer\s*:\s*(.+)$')

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

# --- Fonction principale: détecter les questions ---

def detect_questions(text: str, module_id: int) -> dict:
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

    for qnum, raw_block in _split_blocks(text):
        block = _strip_after_explanation(raw_block)

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
            special_nature = "drag-n-drop" # type 5   
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
                # pas exploitable → on ignore
                continue
        else:
            # Énoncé
            question_text = " ".join(lines[:first_opt_idx]).strip()

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

        # Filtre : au moins 2 réponses pour les cas "classiques"
        if len(answers) < 2:
            continue
        if not question_text:
            continue

        questions.append({
            "context": "",
            "text": question_text,
            "scenario": "no",
            "level": "medium",
            "nature": nature,
            "answers": answers
        })

    return {"module_id": module_id, "questions": questions}

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

    text = extract_text_from_pdf(save_path, use_ocr=False, skip_first_page=True, header_ratio=0.10, footer_ratio=0.10)
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
