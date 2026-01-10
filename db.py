import mysql.connector
import logging
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, time, timedelta
from typing import Iterable, Optional, Union
from config import DB_CONFIG

# Valeurs de niveau : easy→0, medium→1, hard→2
level_mapping = {"easy": 0, "medium": 1, "hard": 2}

# Mapping de type de question et de scénario en codes numériques
nature_mapping = {"qcm": 1, "truefalse": 2, "short-answer": 3, "matching": 4, "drag-n-drop": 5}
ty_mapping = {"no": 1, "scenario": 2, "scenario-illustrated": 3}

# Reverse mappings used when projecting aggregated database results.
reverse_level_mapping = {value: key for key, value in level_mapping.items()}
reverse_nature_mapping = {value: key for key, value in nature_mapping.items()}
reverse_ty_mapping = {value: key for key, value in ty_mapping.items()}

executor = ThreadPoolExecutor(max_workers=8)
_SCHEDULE_COLUMNS: set[str] | None = None


def _safe_json_loads(raw, default=None):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _json_dumps(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def execute_async(func, *args, **kwargs):
    """Run a database function in a background thread."""
    return executor.submit(func, *args, **kwargs)


def get_connection():
    return mysql.connector.connect(
        host=DB_CONFIG["host"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
    )


def _dict_from_schedule_row(row, columns):
    """Build a schedule entry dict from a database row."""

    def _decode_schedule_note(raw_note):
        add_image = True
        if not raw_note:
            return "", add_image
        try:
            parsed = json.loads(raw_note)
        except (TypeError, json.JSONDecodeError):
            return raw_note or "", add_image
        if isinstance(parsed, dict):
            text = parsed.get("text")
            add_image = parsed.get("addImage", True)
            return (text if isinstance(text, str) else "") or "", bool(add_image)
        if isinstance(parsed, str):
            return parsed, add_image
        return str(parsed), add_image

    def _format_timestamp(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (date,)):
            return value.isoformat()
        if isinstance(value, time):
            return value.isoformat(timespec="seconds")
        if isinstance(value, timedelta):
            return (datetime.min + value).isoformat()
        try:
            return value.isoformat()  # type: ignore[attr-defined]
        except Exception:
            return str(value)

    data = {columns[index]: value for index, value in enumerate(row)}
    note, add_image = _decode_schedule_note(data.get("note"))
    last_run_at = data.get("last_run_at") or data.get("lastRunAt")
    job_id = data.get("job_id") or data.get("jobId")
    result_summary = data.get("result_summary") or data.get("summary")

    def _format_time_of_day(raw_time):
        if not raw_time:
            return None
        if isinstance(raw_time, time):
            return raw_time.isoformat(timespec="minutes")
        if isinstance(raw_time, timedelta):
            combined = datetime.min + raw_time
            return combined.time().isoformat(timespec="minutes")
        if isinstance(raw_time, datetime):
            return raw_time.time().isoformat(timespec="minutes")
        try:
            parsed = time.fromisoformat(str(raw_time))
            return parsed.isoformat(timespec="minutes")
        except Exception:
            return str(raw_time)

    return {
        "id": data.get("id"),
        "day": data.get("day").isoformat() if data.get("day") else None,
        "time": _format_time_of_day(data.get("time_of_day")),
        "providerId": data.get("provider_id"),
        "providerName": data.get("provider_name"),
        "certId": data.get("cert_id"),
        "certName": data.get("cert_name"),
        "subject": data.get("subject"),
        "subjectLabel": data.get("subject_label"),
        "contentType": data.get("content_type"),
        "contentTypeLabel": data.get("content_label"),
        "link": data.get("link"),
        "channels": json.loads(data.get("channels")) if data.get("channels") else [],
        "note": note,
        "addImage": add_image,
        "status": data.get("status") or "queued",
        "lastRunAt": _format_timestamp(last_run_at),
        "jobId": job_id,
        "resultSummary": result_summary,
    }


def _load_schedule_columns() -> set[str]:
    """Return available columns for the ``schedule_entries`` table."""

    global _SCHEDULE_COLUMNS
    if _SCHEDULE_COLUMNS is not None:
        return _SCHEDULE_COLUMNS

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW COLUMNS FROM schedule_entries")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    _SCHEDULE_COLUMNS = {row[0] for row in rows}
    return _SCHEDULE_COLUMNS


def get_schedule_entries():
    """Fetch all stored schedule entries."""

    optional_columns = []
    available_columns = _load_schedule_columns()
    if "job_id" in available_columns:
        optional_columns.append("job_id")
    if "result_summary" in available_columns:
        optional_columns.append("result_summary")

    columns = [
        "id",
        "day",
        "time_of_day",
        "provider_id",
        "provider_name",
        "cert_id",
        "cert_name",
        "subject",
        "subject_label",
        "content_type",
        "content_label",
        "link",
        "channels",
        "note",
        "status",
        "last_run_at",
        *optional_columns,
    ]

    conn = get_connection()
    cursor = conn.cursor()
    query = f"""
        SELECT
            {', '.join(columns)}
        FROM schedule_entries
        ORDER BY day, time_of_day
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [_dict_from_schedule_row(row, columns) for row in rows]


def upsert_schedule_entry(entry):
    """Insert or replace a schedule entry."""

    conn = get_connection()
    cursor = conn.cursor()
    query = """
        INSERT INTO schedule_entries (
            id, day, time_of_day, provider_id, provider_name,
            cert_id, cert_name, subject, subject_label,
            content_type, content_label, link, channels, note,
            status, last_run_at
        ) VALUES (
            %(id)s, %(day)s, %(time)s, %(providerId)s, %(providerName)s,
            %(certId)s, %(certName)s, %(subject)s, %(subjectLabel)s,
            %(contentType)s, %(contentTypeLabel)s, %(link)s, %(channels)s, %(note)s,
            %(status)s, %(last_run_at)s
        )
        ON DUPLICATE KEY UPDATE
            day = VALUES(day),
            time_of_day = VALUES(time_of_day),
            provider_id = VALUES(provider_id),
            provider_name = VALUES(provider_name),
            cert_id = VALUES(cert_id),
            cert_name = VALUES(cert_name),
            subject = VALUES(subject),
            subject_label = VALUES(subject_label),
            content_type = VALUES(content_type),
            content_label = VALUES(content_label),
            link = VALUES(link),
            channels = VALUES(channels),
            note = VALUES(note),
            status = VALUES(status),
            last_run_at = VALUES(last_run_at)
    """

    def _normalize_timestamp(value: Optional[Union[str, datetime]]) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    payload = {
        **entry,
        "channels": json.dumps(entry.get("channels") or []),
        "status": entry.get("status") or "queued",
        "last_run_at": _normalize_timestamp(entry.get("lastRunAt") or entry.get("last_run_at")),
    }
    cursor.execute(query, payload)
    conn.commit()
    cursor.close()
    conn.close()


def delete_schedule_entry(entry_id: str):
    """Delete a single schedule entry by id."""

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schedule_entries WHERE id = %s", (entry_id,))
    conn.commit()
    cursor.close()
    conn.close()


def update_schedule_status(
    entry_ids: Iterable[str], status: str, *, last_run_at: Optional[datetime] = None
) -> None:
    """Update the status (and optionally timestamp) of schedule entries."""

    ids = list(entry_ids)
    if not ids:
        return

    conn = get_connection()
    cursor = conn.cursor()
    payloads = [
        {"status": status, "last_run_at": last_run_at, "id": entry_id}
        for entry_id in ids
    ]
    cursor.executemany(
        """
        UPDATE schedule_entries
        SET status = %(status)s, last_run_at = %(last_run_at)s
        WHERE id = %(id)s
        """,
        payloads,
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_public_certifications():
    """Return certifications marked as published along with their provider."""

    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT c.id, c.name, c.prov AS provider_id, p.name AS provider_name
        FROM courses c
        LEFT JOIN provs p ON p.id = c.prov
        WHERE c.pub = 1
        ORDER BY c.name
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {"id": row[0], "name": row[1], "provider_id": row[2], "provider_name": row[3]}
        for row in rows
    ]


def get_providers():
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name FROM provs"
    cursor.execute(query)
    providers = cursor.fetchall()
    cursor.close()
    conn.close()
    return providers


def get_certifications_by_provider(provider_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name FROM courses WHERE prov = %s"
    cursor.execute(query, (provider_id,))
    certifications = cursor.fetchall()
    cursor.close()
    conn.close()
    return certifications


def get_certifications_without_domains():
    """Return certifications that do not have any associated domains."""

    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT c.id, c.name
        FROM courses c
        LEFT JOIN modules m ON m.course = c.id
        GROUP BY c.id, c.name
        HAVING COUNT(m.id) = 0
        ORDER BY c.name
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [{"id": row[0], "name": row[1]} for row in rows]


def get_domains_by_certification(cert_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name FROM modules WHERE course = %s"
    cursor.execute(query, (cert_id,))
    domains = cursor.fetchall()
    cursor.close()
    conn.close()
    return domains


def get_domain_question_counts_for_cert(cert_id):
    """Return question counts per domain for a certification."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT m.id, m.name, COUNT(q.id) AS total_questions
        FROM modules m
        LEFT JOIN questions q ON q.module = m.id
        WHERE m.course = %s
        GROUP BY m.id, m.name
        ORDER BY m.name
    """
    cursor.execute(query, (cert_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    results = []
    for row in rows:
        total = int(row[2] or 0)
        if total <= 0:
            continue
        results.append({"id": row[0], "name": row[1], "total_questions": total})
    return results

def get_certifications_missing_correct_answers():
    """Return certifications that still miss correct answers on questions."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT c.id, c.name, COUNT(DISTINCT q.id) AS missing_questions
        FROM courses c
        JOIN modules m ON m.course = c.id
        JOIN questions q ON q.module = m.id
        WHERE NOT EXISTS (
            SELECT 1 FROM quest_ans qa WHERE qa.question = q.id AND qa.isok = 1
        )
          AND EXISTS (
            SELECT 1 FROM quest_ans qa WHERE qa.question = q.id
          )
        GROUP BY c.id, c.name
        HAVING missing_questions > 0
        ORDER BY missing_questions DESC, c.name
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {"id": row[0], "name": row[1], "missing_questions": int(row[2] or 0)}
        for row in rows
    ]


def get_domains_missing_correct_answers(cert_id):
    """Return domains of a certification that miss a correct answer on questions."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT m.id, m.name, COUNT(DISTINCT q.id) AS missing_questions
        FROM modules m
        JOIN questions q ON q.module = m.id
        WHERE m.course = %s
          AND NOT EXISTS (
            SELECT 1 FROM quest_ans qa WHERE qa.question = q.id AND qa.isok = 1
          )
          AND EXISTS (
            SELECT 1 FROM quest_ans qa WHERE qa.question = q.id
          )
        GROUP BY m.id, m.name
        HAVING missing_questions > 0
        ORDER BY missing_questions DESC, m.name
    """
    cursor.execute(query, (cert_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {"id": row[0], "name": row[1], "missing_questions": int(row[2] or 0)}
        for row in rows
    ]


def get_domains_missing_answers_by_type():
    """Return domains with counts of questions missing answers grouped by type."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """

        SELECT m.id, m.name, c.id, c.name, q.nature, COUNT(*) AS missing_count
        FROM modules m
        JOIN courses c ON c.id = m.course

        JOIN questions q ON q.module = m.id
        WHERE NOT EXISTS (
            SELECT 1 FROM quest_ans qa WHERE qa.question = q.id
        )
          AND q.nature IN (%s, %s, %s)
        GROUP BY m.id, m.name, c.id, c.name, q.nature
        ORDER BY m.name
    """
    cursor.execute(query, (
        nature_mapping['qcm'],
        nature_mapping['matching'],
        nature_mapping['drag-n-drop'],
    ))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    results = {}

    for domain_id, domain_name, course_id, course_name, nature_code, missing_count in rows:
        domain_entry = results.setdefault(
            domain_id,
            {
                "id": domain_id,
                "name": domain_name,
                "certification_id": course_id,
                "certification_name": course_name,
                "counts": {"qcm": 0, "matching": 0, "drag-n-drop": 0},
            },
        )
        if nature_code == nature_mapping['qcm']:
            key = 'qcm'
        elif nature_code == nature_mapping['matching']:
            key = 'matching'
        else:
            key = 'drag-n-drop'
        domain_entry['counts'][key] += int(missing_count or 0)

    for domain_entry in results.values():
        domain_entry['total'] = sum(domain_entry['counts'].values())

    # Sort domains by certification then name for consistent display
    return sorted(
        results.values(), key=lambda d: (d['certification_name'], d['name'])
    )


def count_total_questions(domain_id):
    """
    Renvoie le nombre total de questions dans le domaine (module) donné.
    """
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT COUNT(*) FROM questions WHERE module = %s"
    cursor.execute(query, (domain_id,))
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total


def get_domain_question_snapshot(domain_id):
    """Return total and per-category question counts for a domain."""

    conn = get_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT level, nature, ty, COUNT(*)
            FROM questions
            WHERE module = %s
            GROUP BY level, nature, ty
        """
        cursor.execute(query, (domain_id,))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    total = 0
    categories = {}
    for level_num, nature_num, ty_num, count in rows:
        value = int(count or 0)
        total += value

        difficulty = reverse_level_mapping.get(int(level_num))
        qtype = reverse_nature_mapping.get(int(nature_num))
        scenario = reverse_ty_mapping.get(int(ty_num))

        if difficulty and qtype and scenario:
            categories[(difficulty, qtype, scenario)] = value

    return total, categories


def count_questions_in_category(domain_id, level, qtype, scenario_type):
    """
    Compte le nombre de questions dans la table 'questions' correspondant aux critères donnés.
    On filtre sur module, level, nature et ty.
    """
    # Conversion du niveau en code numérique
    level_num = level_mapping.get(level, 1)  # défaut à medium
    # Conversion de nature et scénario
    nature_num = nature_mapping.get(qtype, 0)
    ty_num = ty_mapping.get(scenario_type, 1)

    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT COUNT(*) FROM questions 
        WHERE module = %s AND level = %s AND nature = %s AND ty = %s
    """
    cursor.execute(query, (domain_id, level_num, nature_num, ty_num))
    count = cursor.fetchone()[0]
    logging.info(f"Count for module {domain_id}, level {level_num}, nature {nature_num}, ty {ty_num}: {count}")
    cursor.close()
    conn.close()
    return count


def insert_questions(domain_id, questions_json, scenario_type_str):
    """
    Insère les questions et leurs réponses depuis la structure JSON dans la base.
    Chaque réponse est sérialisée en JSON (hors champ ``isok``) et stockée dans
    la colonne ``answers.text``. Le champ ``isok`` détermine la valeur à insérer
    dans ``quest_ans``. En cas de doublon sur la table ``answers`` (unicité du
    JSON), l'id existant est réutilisé. La colonne ``descr`` de ``questions``
    reçoit la valeur de ``diagram_descr``.
    """
    # Mappage pour la conversion
    ty_num = ty_mapping.get(scenario_type_str, 1)

    conn = get_connection()
    cursor = conn.cursor()
    q_imported = q_skipped = a_imported = a_reused = 0
    try:
        num_questions = len(questions_json.get("questions", []))
        logging.info(f"Inserting {num_questions} questions into domain {domain_id}.")
        for question in questions_json.get("questions", []):
            # Assemblage du texte final
            context = question.get("context", "").strip()
            diagram_descr = question.get("diagram_descr", "").strip()
            image = question.get("image", "").strip()
            src_file = (question.get("src_file") or "").strip() or None
            text = question.get("text", "").strip()
            if context or image:
                full_text = ""
                if context:
                    full_text += context + "\n"
                if image:
                    full_text += image + "<br>"
                full_text += text
                question_text = full_text
            else:
                question_text = text

            # Conversion du niveau
            level_num = level_mapping.get(question.get("level", "medium"), 1)
            # Conversion de la nature
            nature_num = nature_mapping.get(question.get("nature", "qcm"), 0)

            # Insertion de la question avec le champ descr
            query_question = """
                INSERT INTO questions (text, descr, level, module, nature, ty, src_file, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """
            try:
                cursor.execute(query_question, (
                    question_text,
                    diagram_descr,
                    level_num,
                    domain_id,
                    nature_num,
                    ty_num,
                    src_file
                ))
                question_id = cursor.lastrowid
                q_imported += 1
                logging.info(f"Inserted question ID: {question_id}")
            except mysql.connector.Error as err:
                if err.errno == 1062:
                    logging.info("Duplicate question skipped")
                    q_skipped += 1

                    # If the existing question has no source file recorded yet,
                    # update it with the current file name to keep provenance.
                    if src_file:
                        try:
                            cursor.execute(
                                "SELECT id, src_file FROM questions WHERE text = %s AND module = %s LIMIT 1",
                                (question_text, domain_id),
                            )
                            row = cursor.fetchone()
                            if row:
                                existing_id, existing_src_file = row
                                if not (existing_src_file or "").strip():
                                    cursor.execute(
                                        "UPDATE questions SET src_file = %s WHERE id = %s",
                                        (src_file, existing_id),
                                    )
                                    logging.info(
                                        f"Updated empty src_file for duplicate question ID: {existing_id}"
                                    )
                        except Exception as update_err:
                            logging.warning(
                                "Failed to update src_file for duplicate question: %s", update_err
                            )
                    continue
                else:
                    raise

            # Insertion des réponses
            for answer in question.get("answers", []):
                raw_val = (answer.get("value") or answer.get("text") or "").strip()
                if not raw_val:
                    continue

                # Construit un objet JSON sans le champ isok, en normalisant la clé 'value'
                answer_data = {
                    k: v for k, v in answer.items() if k not in ("isok", "value", "text")
                }
                answer_data["value"] = raw_val
                answer_json = json.dumps(answer_data, ensure_ascii=False)[:700]
                isok = int(answer.get("isok", 0))

                try:
                    query_answer = "INSERT INTO answers (text, created_at) VALUES (%s, NOW())"
                    cursor.execute(query_answer, (answer_json,))
                    answer_id = cursor.lastrowid
                    a_imported += 1
                    logging.info(f"  Inserted answer ID: {answer_id}")
                except mysql.connector.Error as err:
                    if err.errno == 1062:
                        query_select = "SELECT id FROM answers WHERE text = %s"
                        cursor.execute(query_select, (answer_json,))
                        result = cursor.fetchone()
                        if result:
                            answer_id = result[0]
                            a_reused += 1
                            logging.info(f"  Duplicate found, using existing answer ID: {answer_id}")
                        else:
                            raise
                    else:
                        raise

                query_link = "INSERT INTO quest_ans (question, answer, isok) VALUES (%s, %s, %s)"
                try:
                    cursor.execute(query_link, (question_id, answer_id, isok))
                except mysql.connector.Error as err:
                    if err.errno == 1062:
                        logging.info("  Duplicate question-answer link skipped")
                    else:
                        raise
        conn.commit()
        logging.info("Insertion completed")
        return {
            "imported_questions": q_imported,
            "skipped_questions": q_skipped,
            "imported_answers": a_imported,
            "reused_answers": a_reused,
        }
    except Exception as e:
        conn.rollback()
        logging.error("Error during insertion: " + str(e))
        raise
    finally:
        cursor.close()
        conn.close()


def get_domains_description_by_certif(cert_id):
    """
    Renvoie la liste des domaines (modules) pour la certification donnée,
    en préférant le contenu du blueprint lorsqu'il est disponible.
    """

    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name, descr, blueprint FROM modules WHERE course = %s"
    cursor.execute(query, (cert_id,))
    domains = cursor.fetchall()
    cursor.close()
    conn.close()

    results = []
    for domain_id, name, descr, blueprint in domains:
        blueprint_text = (blueprint or "").strip() if isinstance(blueprint, str) else blueprint
        if isinstance(blueprint_text, str) and not blueprint_text:
            blueprint_text = None
        descr_text = (descr or "").strip() if isinstance(descr, str) else descr
        preferred = blueprint_text if blueprint_text else (descr_text or "")
        results.append(
            {
                "id": domain_id,
                "name": name,
                "descr": preferred,
                "blueprint": blueprint_text,
                "fallback_descr": descr_text,
            }
        )
    return results


def get_questions_without_correct_answer(cert_id):
    """Return questions that have answers but none marked as correct."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT q.id AS question_id, q.text AS qtext, a.id AS answer_id, a.text AS atext
        FROM questions q
        JOIN modules m ON q.module = m.id
        JOIN quest_ans qa ON qa.question = q.id
        JOIN answers a ON qa.answer = a.id
        WHERE m.course = %s
          AND q.id NOT IN (SELECT question FROM quest_ans WHERE isok = 1)
        ORDER BY q.id
    """
    cursor.execute(query, (cert_id,))
    rows = cursor.fetchall()
    cursor.close(); conn.close()
    questions = {}
    for row in rows:
        qid = row['question_id']
        if qid not in questions:
            questions[qid] = {"id": qid, "text": row['qtext'], "answers": []}
        try:
            ans_text = json.loads(row['atext']).get('value', '')
        except Exception:
            ans_text = row['atext']
        questions[qid]['answers'].append({"id": row['answer_id'], "value": ans_text})
    return list(questions.values())


def count_questions_with_answers(cert_id):
    """Count questions of a certification that already have at least one answer."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT COUNT(DISTINCT q.id)
        FROM questions q
        JOIN modules m ON q.module = m.id
        JOIN quest_ans qa ON qa.question = q.id
        WHERE m.course = %s
    """
    cursor.execute(query, (cert_id,))
    total = cursor.fetchone()[0] or 0
    cursor.close(); conn.close()
    return int(total)


def count_questions_missing_correct_answer(cert_id):
    """Count questions that still have no correct answer assigned."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT COUNT(DISTINCT q.id)
        FROM questions q
        JOIN modules m ON q.module = m.id
        JOIN quest_ans qa ON qa.question = q.id
        WHERE m.course = %s
          AND q.id NOT IN (SELECT question FROM quest_ans WHERE isok = 1)
    """
    cursor.execute(query, (cert_id,))
    total = cursor.fetchone()[0] or 0
    cursor.close(); conn.close()
    return int(total)


def get_questions_without_answers_by_nature(cert_id, nature_code):
    """Return questions of a given nature that currently have no answers."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT q.id AS question_id, q.text AS qtext
        FROM questions q
        JOIN modules m ON q.module = m.id
        WHERE m.course = %s AND q.nature = %s
          AND NOT EXISTS (SELECT 1 FROM quest_ans qa WHERE qa.question = q.id)
    """
    cursor.execute(query, (cert_id, nature_code))
    rows = cursor.fetchall()
    cursor.close(); conn.close()
    return [{"id": r['question_id'], "text": r['qtext']} for r in rows]


def count_questions_by_nature(cert_id, nature_code):
    """Count questions for a certification filtered by their nature."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT COUNT(*)
        FROM questions q
        JOIN modules m ON q.module = m.id
        WHERE m.course = %s AND q.nature = %s
    """
    cursor.execute(query, (cert_id, nature_code))
    total = cursor.fetchone()[0] or 0
    cursor.close(); conn.close()
    return int(total)


def count_questions_without_answers_by_nature(cert_id, nature_code):
    """Count questions of a given nature that still have no answers."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT COUNT(*)
        FROM questions q
        JOIN modules m ON q.module = m.id
        WHERE m.course = %s AND q.nature = %s
          AND NOT EXISTS (SELECT 1 FROM quest_ans qa WHERE qa.question = q.id)
    """
    cursor.execute(query, (cert_id, nature_code))
    total = cursor.fetchone()[0] or 0
    cursor.close(); conn.close()
    return int(total)


def mark_answers_correct(question_id, answer_ids):
    """Mark given answers as correct for a question."""
    if not answer_ids:
        return
    conn = get_connection()
    cursor = conn.cursor()
    for aid in answer_ids:
        cursor.execute(
            "UPDATE quest_ans SET isok = 1 WHERE question = %s AND answer = %s",
            (question_id, aid),
        )
    conn.commit()
    cursor.close(); conn.close()


def add_answers(question_id, answers):
    """Insert new answers for a question."""
    if not answers:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for ans in answers:
            ans_json = json.dumps(
                {k: v for k, v in ans.items() if k != 'isok'}, ensure_ascii=False
            )[:700]
            isok = int(ans.get('isok', 0))
            try:
                cursor.execute(
                    "INSERT INTO answers (text, created_at) VALUES (%s, NOW())",
                    (ans_json,),
                )
                ans_id = cursor.lastrowid
            except mysql.connector.Error as err:
                if err.errno == 1062:
                    cursor.execute(
                        "SELECT id FROM answers WHERE text = %s", (ans_json,)
                    )
                    res = cursor.fetchone()
                    ans_id = res[0] if res else None
                else:
                    raise
            if ans_id:
                cursor.execute(
                    "INSERT INTO quest_ans (question, answer, isok) VALUES (%s,%s,%s)",
                    (question_id, ans_id, isok),
                )
        conn.commit()
    finally:
        cursor.close(); conn.close()


def _build_user_filter_clause(alias, plan, cert_id, user_query, exclude_guest=True):
    conditions = []
    params = {}
    if exclude_guest:
        conditions.append(f"COALESCE({alias}.`type`, '') <> 'Guest'")
    if plan is not None:
        conditions.append(f"{alias}.ex = %(plan)s")
        params["plan"] = plan
    if cert_id is not None:
        conditions.append(
            f"EXISTS (SELECT 1 FROM users_course uc WHERE uc.user = {alias}.id AND uc.course = %(cert_id)s)"
        )
        params["cert_id"] = cert_id
    if user_query:
        conditions.append(
            f"({alias}.name LIKE %(user_query)s OR {alias}.email LIKE %(user_query)s OR {alias}.usn LIKE %(user_query)s)"
        )
        params["user_query"] = f"%{user_query}%"
    clause = " AND ".join(conditions)
    return clause, params


def get_dashboard_snapshot(start_dt, end_dt, plan=None, cert_id=None, user_query=None):
    conn = get_connection()
    cursor = conn.cursor()

    user_filters, params = _build_user_filter_clause("u", plan, cert_id, user_query)
    base_params = {
        "start": start_dt,
        "end": end_dt,
        "now": datetime.utcnow(),
        **params,
    }

    def _apply_filters(base_query):
        if not user_filters:
            return base_query
        insertion = f" AND {user_filters}\n"
        if "GROUP BY" in base_query:
            return base_query.replace("GROUP BY", f"{insertion}GROUP BY")
        if "ORDER BY" in base_query:
            return base_query.replace("ORDER BY", f"{insertion}ORDER BY")
        if "LIMIT" in base_query:
            return base_query.replace("LIMIT", f"{insertion}LIMIT")
        return f"{base_query} AND {user_filters}"

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM users u
            WHERE u.created_at BETWEEN %(start)s AND %(end)s
            """
        ),
        base_params,
    )
    new_users = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM users u
            WHERE u.id IS NOT NULL
            """
        ),
        params,
    )
    total_users = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(DISTINCT j.user)
            FROM journs j
            JOIN users u ON u.id = j.user
            WHERE j.created_at BETWEEN %(start)s AND %(end)s
            """
        ),
        base_params,
    )
    active_users = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM orders o
            JOIN users u ON u.id = o.user
            WHERE o.type = 0 AND o.exp > %(now)s
            """
        ),
        base_params,
    )
    active_subscriptions = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COALESCE(SUM(o.amount), 0)
            FROM orders o
            JOIN users u ON u.id = o.user
            WHERE o.type = 0 AND o.created_at BETWEEN %(start)s AND %(end)s
            """
        ),
        base_params,
    )
    revenue = cursor.fetchone()[0] or 0

    exam_params = {
        "start": start_dt,
        "end": end_dt,
    }
    guest_condition = "COALESCE(u.`type`, '') <> 'Guest'"
    exam_conditions = ["eu.comp_at BETWEEN %(start)s AND %(end)s", guest_condition]
    if plan is not None:
        exam_params["plan"] = plan
        exam_conditions.append("u.ex = %(plan)s")
    if cert_id is not None:
        exam_params["cert_id"] = cert_id
        exam_conditions.append("e.certi = %(cert_id)s")
    if user_query:
        exam_params["user_query"] = f"%{user_query}%"
        exam_conditions.append(
            "(u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)"
        )
    exam_where = " AND ".join(exam_conditions)

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM exam_users eu
        JOIN users u ON u.id = eu.user
        JOIN exams e ON e.id = eu.exam
        WHERE {exam_where}
        """,
        exam_params,
    )
    completed_exams = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM journs j
            JOIN users u ON u.id = j.user
            WHERE j.created_at BETWEEN %(start)s AND %(end)s
            """
        ),
        base_params,
    )
    total_sessions = cursor.fetchone()[0] or 0
    engagement = total_sessions / active_users if active_users else 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(DISTINCT j.user)
            FROM journs j
            JOIN users u ON u.id = j.user
            WHERE j.created_at BETWEEN %(start)s AND %(end)s
              AND u.created_at < %(start)s
            """
        ),
        base_params,
    )
    returning_users = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COALESCE(j.city, j.loc, 'Inconnu') AS location, COUNT(*) AS total
            FROM journs j
            JOIN users u ON u.id = j.user
            WHERE j.created_at BETWEEN %(start)s AND %(end)s
            GROUP BY location
            ORDER BY total DESC
            LIMIT 5
            """
        ),
        base_params,
    )
    locations = [{"label": row[0], "total": row[1]} for row in cursor.fetchall()]

    cursor.execute(
        f"""
        SELECT c.id, c.name, COUNT(eu.id) AS completions
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        JOIN courses c ON c.id = e.certi
        JOIN users u ON u.id = eu.user
        WHERE {exam_where}
        GROUP BY c.id, c.name
        ORDER BY completions DESC
        LIMIT 5
        """,
        exam_params,
    )
    completions_by_cert = [
        {"id": row[0], "name": row[1], "completions": row[2]} for row in cursor.fetchall()
    ]

    cursor.execute(
        f"""
        SELECT AVG(TIMESTAMPDIFF(MINUTE, eu.start_at, eu.comp_at))
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        JOIN users u ON u.id = eu.user
        WHERE eu.start_at IS NOT NULL
          AND eu.comp_at IS NOT NULL
          AND {exam_where}
        """,
        exam_params,
    )
    avg_exam_duration = cursor.fetchone()[0]

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        JOIN users u ON u.id = eu.user
        WHERE eu.added BETWEEN %(start)s AND %(end)s
        AND {guest_condition}
        {"AND u.ex = %(plan)s" if plan is not None else ""}
        {"AND e.certi = %(cert_id)s" if cert_id is not None else ""}
        {"AND (u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)" if user_query else ""}
        """,
        exam_params,
    )
    total_exam_assignments = cursor.fetchone()[0] or 0

    cert_activity_conditions = ["eu.comp_at BETWEEN %(start)s AND %(end)s", guest_condition]
    if cert_id is not None:
        cert_activity_conditions.append("e.certi = %(cert_id)s")
    if plan is not None:
        cert_activity_conditions.append("u.ex = %(plan)s")
    if user_query:
        cert_activity_conditions.append(
            "(u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)"
        )
    cert_activity_where = " AND ".join(cert_activity_conditions)

    cursor.execute(
        f"""
        SELECT cert_counts.user_id, cert_counts.cert_name, cert_counts.cert_completions
        FROM (
            SELECT eu.user AS user_id, c.name AS cert_name, COUNT(*) AS cert_completions
            FROM exam_users eu
            JOIN exams e ON e.id = eu.exam
            JOIN courses c ON c.id = e.certi
            JOIN users u ON u.id = eu.user
            WHERE {cert_activity_where}
            GROUP BY eu.user, c.id, c.name
        ) cert_counts
        JOIN (
            SELECT user_id, MAX(cert_completions) AS max_completions
            FROM (
                SELECT eu.user AS user_id, c.id AS cert_id, COUNT(*) AS cert_completions
                FROM exam_users eu
                JOIN exams e ON e.id = eu.exam
                JOIN courses c ON c.id = e.certi
                JOIN users u ON u.id = eu.user
                WHERE {cert_activity_where}
                GROUP BY eu.user, c.id
            ) max_counts
            GROUP BY user_id
        ) top_counts
          ON cert_counts.user_id = top_counts.user_id
         AND cert_counts.cert_completions = top_counts.max_completions
        """,
        exam_params,
    )
    top_cert_map = {
        row[0]: {"cert_name": row[1], "cert_completions": row[2]} for row in cursor.fetchall()
    }

    cursor.execute(
        f"""
        SELECT u.id, u.name, u.email, u.ex,
               MAX(j.created_at) AS last_activity,
               COUNT(DISTINCT j.id) AS sessions,
               COUNT(DISTINCT eu.id) AS exams_completed
        FROM users u
        LEFT JOIN journs j
          ON j.user = u.id AND j.created_at BETWEEN %(start)s AND %(end)s
        LEFT JOIN exam_users eu
          ON eu.user = u.id AND eu.comp_at BETWEEN %(start)s AND %(end)s
        {"LEFT JOIN users_course uc ON uc.user = u.id" if cert_id is not None else ""}
        WHERE 1=1
        AND {guest_condition}
        {"AND u.ex = %(plan)s" if plan is not None else ""}
        {"AND uc.course = %(cert_id)s" if cert_id is not None else ""}
        {"AND (u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)" if user_query else ""}
        GROUP BY u.id, u.name, u.email, u.ex
        ORDER BY last_activity DESC
        LIMIT 8
        """,
        exam_params,
    )
    top_users = []
    for row in cursor.fetchall():
        top_cert = top_cert_map.get(row[0], {})
        top_users.append(
            {
                "name": row[1],
                "email": row[2],
                "plan": row[3],
                "last_activity": row[4],
                "sessions": row[5],
                "exams_completed": row[6],
                "top_cert": top_cert.get("cert_name"),
                "top_cert_completions": top_cert.get("cert_completions"),
            }
        )

    cert_popularity_conditions = ["uc.created_at BETWEEN %(start)s AND %(end)s", guest_condition]
    if plan is not None:
        cert_popularity_conditions.append("u.ex = %(plan)s")
    if user_query:
        cert_popularity_conditions.append(
            "(u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)"
        )
    cert_popularity_where = " AND ".join(cert_popularity_conditions)

    cursor.execute(
        f"""
        SELECT c.id, c.name, COUNT(*) AS user_count
        FROM users_course uc
        JOIN users u ON u.id = uc.user
        JOIN courses c ON c.id = uc.course
        WHERE {cert_popularity_where}
        GROUP BY c.id, c.name
        ORDER BY user_count DESC
        LIMIT 5
        """,
        base_params,
    )
    cert_popularity = [
        {"id": row[0], "name": row[1], "user_count": row[2]} for row in cursor.fetchall()
    ]

    cursor.close()
    conn.close()

    completion_rate = (
        (completed_exams / total_exam_assignments) * 100 if total_exam_assignments else 0
    )

    return {
        "kpis": {
            "active_users": active_users,
            "new_users": new_users,
            "conversion_rate": (active_subscriptions / total_users * 100 if total_users else 0),
            "completed_exams": completed_exams,
            "revenue": revenue,
            "engagement": engagement,
        },
        "acquisition": {
            "new_users": new_users,
            "returning_users": returning_users,
            "active_subscriptions": active_subscriptions,
        },
        "performance": {
            "completion_rate": completion_rate,
            "avg_exam_duration": avg_exam_duration,
            "completions_by_cert": completions_by_cert,
        },
        "locations": locations,
        "top_users": top_users,
        "cert_popularity": cert_popularity,
    }


# ---------------------------------------------------------------------------
# Automation: event-driven rules engine
# ---------------------------------------------------------------------------


def get_automation_event_types():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_event_types ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["filters"] = _safe_json_loads(row.get("filters"), [])
        row["payload_columns"] = _safe_json_loads(row.get("payload_columns"), [])
    return rows


def get_automation_event_type(event_type_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_event_types WHERE id = %s", (event_type_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["filters"] = _safe_json_loads(row.get("filters"), [])
        row["payload_columns"] = _safe_json_loads(row.get("payload_columns"), [])
    return row


def create_automation_event_type(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_event_types (
            code, name, description, enabled, source_table, source_query,
            cursor_type, cursor_value,
            filters, payload_columns, created_at, updated_at
        ) VALUES (
            %(code)s, %(name)s, %(description)s, %(enabled)s, %(source_table)s, %(source_query)s,
            %(cursor_type)s, %(cursor_value)s,
            %(filters)s, %(payload_columns)s, NOW(), NOW()
        )
        """,
        {
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "enabled": 1 if payload.get("enabled", True) else 0,
            "source_table": payload.get("source_table"),
            "source_query": payload.get("source_query"),
            "cursor_type": payload.get("cursor_type") or "id",
            "cursor_value": payload.get("cursor_value"),
            "filters": _json_dumps(payload.get("filters")),
            "payload_columns": _json_dumps(payload.get("payload_columns")),
        },
    )
    conn.commit()
    event_type_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return event_type_id


def update_automation_event_type(event_type_id: int, payload: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_event_types
        SET code=%(code)s,
            name=%(name)s,
            description=%(description)s,
            enabled=%(enabled)s,
            source_table=%(source_table)s,
            source_query=%(source_query)s,
            cursor_type=%(cursor_type)s,
            cursor_value=%(cursor_value)s,
            filters=%(filters)s,
            payload_columns=%(payload_columns)s,
            updated_at=NOW()
        WHERE id=%(id)s
        """,
        {
            "id": event_type_id,
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "enabled": 1 if payload.get("enabled", True) else 0,
            "source_table": payload.get("source_table"),
            "source_query": payload.get("source_query"),
            "cursor_type": payload.get("cursor_type") or "id",
            "cursor_value": payload.get("cursor_value"),
            "filters": _json_dumps(payload.get("filters")),
            "payload_columns": _json_dumps(payload.get("payload_columns")),
        },
    )
    conn.commit()
    cursor.close()
    conn.close()


def update_automation_event_cursor(event_type_id: int, cursor_value: str | None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE auto_event_types SET cursor_value=%s, updated_at=NOW() WHERE id=%s",
        (cursor_value, event_type_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def delete_automation_event_type(event_type_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_event_types WHERE id = %s", (event_type_id,))
    conn.commit()
    cursor.close()
    conn.close()


def get_automation_actions():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_action_types ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["config"] = _safe_json_loads(row.get("config"), {})
        row["action_overrides"] = _safe_json_loads(row.get("action_overrides"), {})
    return rows


def get_automation_action(action_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_action_types WHERE id = %s", (action_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["config"] = _safe_json_loads(row.get("config"), {})
        row["action_overrides"] = _safe_json_loads(row.get("action_overrides"), {})
    return row


def create_automation_action(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_action_types (
            code, name, description, action_type, config, action_overrides, enabled, created_at, updated_at
        ) VALUES (
            %(code)s, %(name)s, %(description)s, %(action_type)s, %(config)s, %(action_overrides)s, %(enabled)s,
            NOW(), NOW()
        )
        """,
        {
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "action_type": payload.get("action_type"),
            "config": _json_dumps(payload.get("config")),
            "action_overrides": _json_dumps(payload.get("action_overrides")),
            "enabled": 1 if payload.get("enabled", True) else 0,
        },
    )
    conn.commit()
    action_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return action_id


def update_automation_action(action_id: int, payload: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_action_types
        SET code=%(code)s,
            name=%(name)s,
            description=%(description)s,
            action_type=%(action_type)s,
            config=%(config)s,
            action_overrides=%(action_overrides)s,
            enabled=%(enabled)s,
            updated_at=NOW()
        WHERE id=%(id)s
        """,
        {
            "id": action_id,
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "action_type": payload.get("action_type"),
            "config": _json_dumps(payload.get("config")),
            "action_overrides": _json_dumps(payload.get("action_overrides")),
            "enabled": 1 if payload.get("enabled", True) else 0,
        },
    )
    conn.commit()
    cursor.close()
    conn.close()


def delete_automation_action(action_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_action_types WHERE id = %s", (action_id,))
    conn.commit()
    cursor.close()
    conn.close()


def get_automation_messages():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_message_templates ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["subject"] = _safe_json_loads(row.get("subject"), {})
        row["body"] = _safe_json_loads(row.get("body"), {})
    return rows


def get_automation_message(message_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_message_templates WHERE id = %s", (message_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["subject"] = _safe_json_loads(row.get("subject"), {})
        row["body"] = _safe_json_loads(row.get("body"), {})
    return row


def create_automation_message(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_message_templates (
            code, name, description, subject, body, enabled, created_at, updated_at
        ) VALUES (
            %(code)s, %(name)s, %(description)s, %(subject)s, %(body)s, %(enabled)s, NOW(), NOW()
        )
        """,
        {
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "subject": _json_dumps(payload.get("subject")),
            "body": _json_dumps(payload.get("body")),
            "enabled": 1 if payload.get("enabled", True) else 0,
        },
    )
    conn.commit()
    message_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return message_id


def update_automation_message(message_id: int, payload: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_message_templates
        SET code=%(code)s,
            name=%(name)s,
            description=%(description)s,
            subject=%(subject)s,
            body=%(body)s,
            enabled=%(enabled)s,
            updated_at=NOW()
        WHERE id=%(id)s
        """,
        {
            "id": message_id,
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "subject": _json_dumps(payload.get("subject")),
            "body": _json_dumps(payload.get("body")),
            "enabled": 1 if payload.get("enabled", True) else 0,
        },
    )
    conn.commit()
    cursor.close()
    conn.close()


def delete_automation_message(message_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_message_templates WHERE id = %s", (message_id,))
    conn.commit()
    cursor.close()
    conn.close()


def get_automation_rules():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT r.*, e.code AS event_code, e.name AS event_name,
               a.code AS action_code, a.name AS action_name,
               m.code AS message_code, m.name AS message_name
        FROM auto_rules r
        LEFT JOIN auto_event_types e ON r.event_type_id = e.id
        LEFT JOIN auto_action_types a ON r.action_id = a.id
        LEFT JOIN auto_message_templates m ON r.message_id = m.id
        ORDER BY r.id
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["run_condition"] = _safe_json_loads(row.get("run_condition"), {})
        row["channels"] = _safe_json_loads(row.get("channels"), [])
    return rows


def get_automation_rule(rule_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_rules WHERE id = %s", (rule_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["run_condition"] = _safe_json_loads(row.get("run_condition"), {})
        row["channels"] = _safe_json_loads(row.get("channels"), [])
    return row


def create_automation_rule(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_rules (
            name, description, event_type_id, run_condition, action_id,
            message_id, channels, enabled, created_at, updated_at
        ) VALUES (
            %(name)s, %(description)s, %(event_type_id)s, %(run_condition)s, %(action_id)s,
            %(message_id)s, %(channels)s,
            %(enabled)s, NOW(), NOW()
        )
        """,
        {
            "name": payload.get("name"),
            "description": payload.get("description"),
            "event_type_id": payload.get("event_type_id"),
            "run_condition": _json_dumps(payload.get("run_condition")),
            "action_id": payload.get("action_id"),
            "message_id": payload.get("message_id"),
            "channels": _json_dumps(payload.get("channels")),
            "enabled": 1 if payload.get("enabled", True) else 0,
        },
    )
    conn.commit()
    rule_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return rule_id


def update_automation_rule(rule_id: int, payload: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_rules
        SET name=%(name)s,
            description=%(description)s,
            event_type_id=%(event_type_id)s,
            run_condition=%(run_condition)s,
            action_id=%(action_id)s,
            message_id=%(message_id)s,
            channels=%(channels)s,
            enabled=%(enabled)s,
            updated_at=NOW()
        WHERE id=%(id)s
        """,
        {
            "id": rule_id,
            "name": payload.get("name"),
            "description": payload.get("description"),
            "event_type_id": payload.get("event_type_id"),
            "run_condition": _json_dumps(payload.get("run_condition")),
            "action_id": payload.get("action_id"),
            "message_id": payload.get("message_id"),
            "channels": _json_dumps(payload.get("channels")),
            "enabled": 1 if payload.get("enabled", True) else 0,
        },
    )
    conn.commit()
    cursor.close()
    conn.close()


def delete_automation_rule(rule_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_rules WHERE id = %s", (rule_id,))
    conn.commit()
    cursor.close()
    conn.close()


def create_event_inbox_entry(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_event_tampon (
            event_type_id, user_id, source_table, source_pk, source, payload,
            status, created_at, updated_at
        ) VALUES (
            %(event_type_id)s, %(user_id)s, %(source_table)s, %(source_pk)s, %(source)s,
            %(payload)s, %(status)s, NOW(), NOW()
        )
        """,
        {
            "event_type_id": payload.get("event_type_id"),
            "user_id": payload.get("user_id"),
            "source_table": payload.get("source_table"),
            "source_pk": payload.get("source_pk"),
            "source": payload.get("source"),
            "payload": _json_dumps(payload.get("payload")),
            "status": payload.get("status", "pending"),
        },
    )
    conn.commit()
    event_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return event_id


def update_event_inbox_status(event_id: int, status: str, *, error: str | None = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_event_tampon
        SET status=%s, error=%s, processed_at=IF(%s IN ('processed','failed'), NOW(), processed_at), updated_at=NOW()
        WHERE id=%s
        """,
        (status, error, status, event_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_event_inbox_entry(event_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_event_tampon WHERE id = %s", (event_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
    return row


def get_pending_event_inbox(limit: int = 100):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM auto_event_tampon WHERE status = 'pending' ORDER BY created_at LIMIT %s",
        (limit,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
    return rows


def insert_rule_execution(
    rule_id: int,
    event_id: int,
    *,
    action_queue_id: int | None = None,
    notification_queue_id: int | None = None,
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_rule_executions (
            rule_id, event_id, action_queue_id, notification_queue_id, created_at
        ) VALUES (%s, %s, %s, %s, NOW())
        """,
        (rule_id, event_id, action_queue_id, notification_queue_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def rule_execution_exists(rule_id: int, event_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM auto_rule_executions WHERE rule_id = %s AND event_id = %s LIMIT 1",
        (rule_id, event_id),
    )
    exists = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return exists


def enqueue_action(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_action_queue (
            action_id, rule_id, event_id, user_id, payload, status, attempts, created_at, updated_at
        ) VALUES (
            %(action_id)s, %(rule_id)s, %(event_id)s, %(user_id)s, %(payload)s,
            %(status)s, %(attempts)s, NOW(), NOW()
        )
        """,
        {
            "action_id": payload.get("action_id"),
            "rule_id": payload.get("rule_id"),
            "event_id": payload.get("event_id"),
            "user_id": payload.get("user_id"),
            "payload": _json_dumps(payload.get("payload")),
            "status": payload.get("status", "pending"),
            "attempts": payload.get("attempts", 0),
        },
    )
    conn.commit()
    action_queue_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return action_queue_id


def enqueue_notification(payload: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auto_notification_queue (
            message_id, rule_id, event_id, user_id, payload, channels, status,
            attempts, created_at, updated_at
        ) VALUES (
            %(message_id)s, %(rule_id)s, %(event_id)s, %(user_id)s, %(payload)s,
            %(channels)s, %(status)s, %(attempts)s, NOW(), NOW()
        )
        """,
        {
            "message_id": payload.get("message_id"),
            "rule_id": payload.get("rule_id"),
            "event_id": payload.get("event_id"),
            "user_id": payload.get("user_id"),
            "payload": _json_dumps(payload.get("payload")),
            "channels": _json_dumps(payload.get("channels")),
            "status": payload.get("status", "pending"),
            "attempts": payload.get("attempts", 0),
        },
    )
    conn.commit()
    notification_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return notification_id


def get_action_queue_entry(action_queue_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_action_queue WHERE id = %s", (action_queue_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
    return row


def get_notification_queue_entry(notification_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_notification_queue WHERE id = %s", (notification_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
        row["channels"] = _safe_json_loads(row.get("channels"), [])
    return row


def get_pending_action_queue(limit: int = 100):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM auto_action_queue WHERE status = 'pending' ORDER BY created_at LIMIT %s",
        (limit,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
    return rows


def get_pending_notification_queue(limit: int = 100):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM auto_notification_queue WHERE status = 'pending' ORDER BY created_at LIMIT %s",
        (limit,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
        row["channels"] = _safe_json_loads(row.get("channels"), [])
    return rows


def update_action_queue_status(action_queue_id: int, status: str, *, error: str | None = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_action_queue
        SET status=%s, last_error=%s,
            processed_at=IF(%s IN ('processed','failed'), NOW(), processed_at),
            attempts=attempts + IF(%s='failed', 1, 0),
            updated_at=NOW()
        WHERE id=%s
        """,
        (status, error, status, status, action_queue_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def update_notification_queue_status(notification_id: int, status: str, *, error: str | None = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE auto_notification_queue
        SET status=%s, last_error=%s,
            processed_at=IF(%s IN ('processed','failed'), NOW(), processed_at),
            attempts=attempts + IF(%s='failed', 1, 0),
            updated_at=NOW()
        WHERE id=%s
        """,
        (status, error, status, status, notification_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_automation_event_history(limit: int = 200, status: str | None = None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    if status:
        cursor.execute(
            "SELECT * FROM auto_event_tampon WHERE status = %s ORDER BY created_at DESC LIMIT %s",
            (status, limit),
        )
    else:
        cursor.execute("SELECT * FROM auto_event_tampon ORDER BY created_at DESC LIMIT %s", (limit,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
    return rows


def get_automation_action_history(limit: int = 200, status: str | None = None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    if status:
        cursor.execute(
            "SELECT * FROM auto_action_queue WHERE status = %s ORDER BY created_at DESC LIMIT %s",
            (status, limit),
        )
    else:
        cursor.execute("SELECT * FROM auto_action_queue ORDER BY created_at DESC LIMIT %s", (limit,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
    return rows


def get_automation_notification_history(limit: int = 200, status: str | None = None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    if status:
        cursor.execute(
            "SELECT * FROM auto_notification_queue WHERE status = %s ORDER BY created_at DESC LIMIT %s",
            (status, limit),
        )
    else:
        cursor.execute(
            "SELECT * FROM auto_notification_queue ORDER BY created_at DESC LIMIT %s", (limit,)
        )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        row["payload"] = _safe_json_loads(row.get("payload"), {})
        row["channels"] = _safe_json_loads(row.get("channels"), [])
    return rows


def get_automation_rule_history(limit: int = 200, status: str | None = None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM auto_rule_executions ORDER BY created_at DESC LIMIT %s", (limit,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_user_by_email(email: str):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email, pk FROM users WHERE email = %s LIMIT 1", (email,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def get_user_by_id(user_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email, pk FROM users WHERE id = %s LIMIT 1", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def create_in_app_message(user_id: int, subject: str, content: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO smails (sub, content, hid, created_at, updated_at)
        VALUES (%s, %s, 0, NOW(), NOW())
        """,
        (subject, content),
    )
    smail_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO users_mail (user, mail, sent, read, last_sent)
        VALUES (%s, %s, 1, 0, NOW())
        """,
        (user_id, smail_id),
    )
    conn.commit()
    cursor.close()
    conn.close()
    return smail_id
