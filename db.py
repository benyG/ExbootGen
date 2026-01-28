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
_ALLOWED_SCORE_COLUMNS = ("score", "result", "note")


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


def _resolve_score_column(columns: set[str]) -> str | None:
    """Pick a safe, known score column if it exists in the table."""
    for col in _ALLOWED_SCORE_COLUMNS:
        if col in columns:
            return col
    return None


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


def get_certifications_by_provider_with_code(provider_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name, code_cert_key FROM courses WHERE prov = %s"
    cursor.execute(query, (provider_id,))
    certifications = cursor.fetchall()
    cursor.close()
    conn.close()
    return certifications


def update_certification_pub(cert_id: int, pub_status: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    query = "UPDATE courses SET pub = %s WHERE id = %s"
    cursor.execute(query, (pub_status, cert_id))
    conn.commit()
    cursor.close()
    conn.close()


def get_certifications_by_provider_with_code(provider_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name, code_cert_key FROM courses WHERE prov = %s"
    cursor.execute(query, (provider_id,))
    certifications = cursor.fetchall()
    cursor.close()
    conn.close()
    return certifications


def get_certifications_by_provider_with_code(provider_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name, code_cert_key FROM courses WHERE prov = %s"
    cursor.execute(query, (provider_id,))
    certifications = cursor.fetchall()
    cursor.close()
    conn.close()
    return certifications


def get_certifications_by_provider_with_code(provider_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name, code_cert_key FROM courses WHERE prov = %s"
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


def get_unpublished_certifications_report(include_all_unpublished: bool = False):
    """Return unpublished certifications with default domain metrics.

    When ``include_all_unpublished`` is False, only certifications eligible for
    automation (pub = 2) are returned. When True, every certification that is
    not online (pub != 1 or NULL) is returned.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if include_all_unpublished:
        where_clause = "WHERE c.pub IS NULL OR c.pub <> 1"
    else:
        where_clause = "WHERE c.pub = 2"
    query = """
        SELECT
            p.id AS provider_id,
            p.name AS provider_name,
            c.id AS cert_id,
            c.name AS cert_name,
            c.code_cert_key AS code_cert,
            c.pub AS pub_status,
            (
                SELECT COUNT(q_all.id)
                FROM questions q_all
                JOIN modules m_all ON m_all.id = q_all.module
                WHERE m_all.course = c.id
            ) AS total_questions,
            (
                SELECT COUNT(q_def.id)
                FROM questions q_def
                JOIN modules m_def ON m_def.id = q_def.module
                WHERE m_def.course = 23
                  AND (
                    TRIM(m_def.code_cert) = TRIM(c.code_cert_key)
                    OR m_def.name = LEFT(CONCAT(c.name, '-default'), 255)
                  )
            ) AS default_questions,
            (
                SELECT m_def.id
                FROM modules m_def
                WHERE m_def.course = 23
                  AND (
                    TRIM(m_def.code_cert) = TRIM(c.code_cert_key)
                    OR m_def.name = LEFT(CONCAT(c.name, '-default'), 255)
                  )
                ORDER BY m_def.id DESC
                LIMIT 1
            ) AS default_module_id,
            (
                SELECT c_def.id
                FROM modules m_def
                JOIN courses c_def ON c_def.id = m_def.course
                WHERE m_def.course = 23
                  AND (
                    TRIM(m_def.code_cert) = TRIM(c.code_cert_key)
                    OR m_def.name = LEFT(CONCAT(c.name, '-default'), 255)
                  )
                ORDER BY m_def.id DESC
                LIMIT 1
            ) AS default_cert_id,
            (
                SELECT c_def.prov
                FROM modules m_def
                JOIN courses c_def ON c_def.id = m_def.course
                WHERE m_def.course = 23
                  AND (
                    TRIM(m_def.code_cert) = TRIM(c.code_cert_key)
                    OR m_def.name = LEFT(CONCAT(c.name, '-default'), 255)
                  )
                ORDER BY m_def.id DESC
                LIMIT 1
            ) AS default_provider_id
        FROM courses c
        JOIN provs p ON p.id = c.prov
        {where_clause}
        ORDER BY p.name, c.name
    """
    cursor.execute(query.format(where_clause=where_clause))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    results = []
    for row in rows:
        results.append(
            {
                "provider_id": row[0],
                "provider_name": row[1],
                "cert_id": row[2],
                "cert_name": row[3],
                "pub_status": row[5],
                "code_cert": row[4] or "",
                "total_questions": int(row[6] or 0),
                "default_questions": int(row[7] or 0),
                "default_module_id": row[8],
                "default_cert_id": row[9],
                "default_provider_id": row[10],
                "automation_eligible": bool(row[5] == 2),
            }
        )
    return results


def get_question_activity_by_day(days: int = 30):
    """Return question activity totals per day and certification over recent days."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT activity_day,
               cert_id,
               cert_name,
               COUNT(*) AS total_questions
        FROM (
            SELECT q.id AS question_id,
                   c.id AS cert_id,
                   c.name AS cert_name,
                   DATE(
                       GREATEST(
                           q.created_at,
                           COALESCE(q.updated_at, q.created_at),
                           COALESCE(MAX(a.updated_at), MAX(a.created_at), q.created_at)
                       )
                   ) AS activity_day
            FROM questions q
            JOIN modules m ON q.module = m.id
            JOIN courses c ON m.course = c.id
            LEFT JOIN quest_ans qa ON qa.question = q.id
            LEFT JOIN answers a ON a.id = qa.answer
            GROUP BY q.id, c.id, c.name, q.created_at, q.updated_at
        ) AS activity
        WHERE activity_day >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        GROUP BY activity_day, cert_id, cert_name
        ORDER BY activity_day DESC, cert_name
    """
    cursor.execute(query, (max(days, 1),))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {
            "day": row[0],
            "certification_id": row[1],
            "certification_name": row[2],
            "total": int(row[3] or 0),
        }
        for row in rows
    ]


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

                    # Always update existing question metadata (src_file + module).
                    try:
                        cursor.execute(
                            "SELECT id, module, src_file FROM questions WHERE text = %s LIMIT 1",
                            (question_text,),
                        )
                        row = cursor.fetchone()
                        if row:
                            existing_id, existing_module, existing_src_file = row
                            if (existing_module != domain_id) or (existing_src_file != src_file):
                                cursor.execute(
                                    "UPDATE questions SET module = %s, src_file = %s WHERE id = %s",
                                    (domain_id, src_file, existing_id),
                                )
                                logging.info(
                                    "Updated duplicate question ID %s with module=%s src_file=%s",
                                    existing_id,
                                    domain_id,
                                    src_file,
                                )
                    except Exception as update_err:
                        logging.warning(
                            "Failed to update duplicate question metadata: %s", update_err
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


_COLUMN_CACHE = {}


def _get_table_columns(table_name):
    if table_name in _COLUMN_CACHE:
        return _COLUMN_CACHE[table_name]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        columns = {row[0] for row in cursor.fetchall()}
        _COLUMN_CACHE[table_name] = columns
        return columns
    finally:
        cursor.close()
        conn.close()


def _build_user_filter_clause(alias, plan, cert_id, user_query, exclude_guest=True):
    conditions = []
    params = {}
    if exclude_guest and "type" in _get_table_columns("users"):
        conditions.append(f"COALESCE({alias}.`type`, '') <> 'Guest'")
    elif exclude_guest:
        conditions.append(f"{alias}.name <> 'Guest'")
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


def search_users(user_query, limit=8):
    if not user_query:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        user_columns = _get_table_columns("users")
        type_select = (
            ", COALESCE(`type`, '') AS account_type" if "type" in user_columns else ""
        )
        cursor.execute(
            f"""
            SELECT id, name, email, ex{type_select}
            FROM users
            WHERE name LIKE %(query)s OR email LIKE %(query)s OR usn LIKE %(query)s
            ORDER BY name
            LIMIT %(limit)s
            """,
            {"query": f"%{user_query}%", "limit": limit},
        )
        return [
            {
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "plan": row[3],
                "account_type": row[4] if len(row) > 4 else None,
            }
            for row in cursor.fetchall()
        ]
    finally:
        cursor.close()
        conn.close()


def get_user_dashboard_snapshot(user_id, start_dt, end_dt, cert_id=None):
    conn = get_connection()
    cursor = conn.cursor()

    user_columns = _get_table_columns("users")
    account_type_select = (
        ", COALESCE(`type`, '') AS account_type" if "type" in user_columns else ""
    )
    cursor.execute(
        f"""
        SELECT id, name, email, ex{account_type_select}, created_at
        FROM users
        WHERE id = %(user_id)s
        """,
        {"user_id": user_id},
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        return None

    user_profile = {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "plan": row[3],
        "account_type": row[4] if "type" in user_columns else None,
        "created_at": row[5] if "type" in user_columns else row[4],
    }

    base_params = {
        "user_id": user_id,
        "start": start_dt,
        "end": end_dt,
        "now": datetime.utcnow(),
        "cert_id": cert_id,
    }

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM journs j
        WHERE j.user = %(user_id)s
          AND j.created_at BETWEEN %(start)s AND %(end)s
          AND j.fen = 'login'
        """,
        base_params,
    )
    total_sessions = cursor.fetchone()[0] or 0

    cursor.execute(
        """
        SELECT DATE(j.created_at) AS day, COUNT(*) AS total
        FROM journs j
        WHERE j.user = %(user_id)s
          AND j.created_at BETWEEN %(start)s AND %(end)s
          AND j.fen = 'login'
        GROUP BY day
        ORDER BY day
        """,
        base_params,
    )
    session_timeline = [{"day": row[0], "total": row[1]} for row in cursor.fetchall()]

    exam_filter = "AND e.certi = %(cert_id)s" if cert_id is not None else ""

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        WHERE eu.user = %(user_id)s
          AND eu.added BETWEEN %(start)s AND %(end)s
          {exam_filter}
        """,
        base_params,
    )
    assigned_exams = cursor.fetchone()[0] or 0

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        WHERE eu.user = %(user_id)s
          AND eu.comp_at BETWEEN %(start)s AND %(end)s
          {exam_filter}
        """,
        base_params,
    )
    completed_exams = cursor.fetchone()[0] or 0

    cursor.execute(
        f"""
        SELECT AVG(TIMESTAMPDIFF(MINUTE, eu.start_at, eu.comp_at))
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        WHERE eu.user = %(user_id)s
          AND eu.start_at IS NOT NULL
          AND eu.comp_at IS NOT NULL
          AND eu.comp_at BETWEEN %(start)s AND %(end)s
          {exam_filter}
        """,
        base_params,
    )
    avg_exam_duration = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM orders o
        WHERE o.user = %(user_id)s
          AND o.type = 0
          AND o.exp > %(now)s
        """,
        base_params,
    )
    active_subscription = (cursor.fetchone()[0] or 0) > 0

    exam_user_columns = _get_table_columns("exam_users")
    score_column = _resolve_score_column(exam_user_columns)

    score_select = f", AVG(eu.{score_column}) AS avg_score" if score_column else ""
    cursor.execute(
        f"""
        SELECT c.id, c.name, COUNT(eu.id) AS completions{score_select}
        FROM exam_users eu
        JOIN exams e ON e.id = eu.exam
        JOIN courses c ON c.id = e.certi
        WHERE eu.user = %(user_id)s
          AND eu.comp_at BETWEEN %(start)s AND %(end)s
          {exam_filter}
        GROUP BY c.id, c.name
        ORDER BY completions DESC
        LIMIT 6
        """,
        base_params,
    )
    completions_by_cert = []
    for cert_row in cursor.fetchall():
        completions_by_cert.append(
            {
                "id": cert_row[0],
                "name": cert_row[1],
                "completions": cert_row[2],
                "avg_score": cert_row[3] if score_column else None,
            }
        )

    score_breakdown = []
    avg_score = None
    if score_column:
        cursor.execute(
            f"""
            SELECT AVG(eu.{score_column}) AS avg_score,
                   SUM(CASE WHEN eu.{score_column} >= 80 THEN 1 ELSE 0 END) AS high_scores,
                   SUM(
                     CASE
                       WHEN eu.{score_column} >= 60 AND eu.{score_column} < 80
                       THEN 1 ELSE 0
                     END
                   ) AS mid_scores,
                   SUM(CASE WHEN eu.{score_column} < 60 THEN 1 ELSE 0 END) AS low_scores
            FROM exam_users eu
            JOIN exams e ON e.id = eu.exam
            WHERE eu.user = %(user_id)s
              AND eu.comp_at BETWEEN %(start)s AND %(end)s
              AND eu.{score_column} IS NOT NULL
              {exam_filter}
            """,
            base_params,
        )
        score_row = cursor.fetchone()
        avg_score = score_row[0] if score_row else None
        if score_row:
            score_breakdown = [
                {"label": "Excellent (≥ 80%)", "total": score_row[1] or 0},
                {"label": "Correct (60–79%)", "total": score_row[2] or 0},
                {"label": "À renforcer (< 60%)", "total": score_row[3] or 0},
            ]

    exam_type_breakdown = []
    exams_columns = _get_table_columns("exams")
    if "type" in exams_columns:
        cursor.execute(
            f"""
            SELECT e.type, COUNT(*) AS total
            FROM exam_users eu
            JOIN exams e ON e.id = eu.exam
            WHERE eu.user = %(user_id)s
              AND eu.added BETWEEN %(start)s AND %(end)s
              {exam_filter}
            GROUP BY e.type
            ORDER BY total DESC
            """,
            base_params,
        )
        type_map = {0: "Test", 1: "Exam", 2: "Share"}
        exam_type_breakdown = [
            {"type": type_map.get(row[0], str(row[0])), "total": row[1]}
            for row in cursor.fetchall()
        ]

    cursor.close()
    conn.close()

    completion_rate = (completed_exams / assigned_exams * 100) if assigned_exams else 0

    return {
        "profile": user_profile,
        "kpis": {
            "sessions": total_sessions,
            "assigned_exams": assigned_exams,
            "completed_exams": completed_exams,
            "completion_rate": completion_rate,
            "avg_exam_duration": avg_exam_duration,
            "active_subscription": active_subscription,
        },
        "session_timeline": session_timeline,
        "exam_types": exam_type_breakdown,
        "completions_by_cert": completions_by_cert,
        "score_breakdown": score_breakdown,
        "avg_score": avg_score,
    }


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

    user_columns = _get_table_columns("users")
    if "type" in user_columns:
        guest_condition = "(COALESCE(u.`type`, '') = 'Guest' OR u.name = 'Guest')"
        non_guest_condition = "COALESCE(u.`type`, '') <> 'Guest'"
    else:
        guest_condition = "u.name = 'Guest'"
        non_guest_condition = "u.name <> 'Guest'"
    guest_filters, guest_params = _build_user_filter_clause(
        "u", plan, cert_id, user_query, exclude_guest=False
    )
    if guest_condition:
        guest_filters = (
            f"{guest_filters} AND {guest_condition}" if guest_filters else guest_condition
        )

    def _apply_filters(base_query, filters):
        if not filters:
            return base_query
        insertion = f" AND {filters}\n"
        if "GROUP BY" in base_query:
            return base_query.replace("GROUP BY", f"{insertion}GROUP BY")
        if "ORDER BY" in base_query:
            return base_query.replace("ORDER BY", f"{insertion}ORDER BY")
        if "LIMIT" in base_query:
            return base_query.replace("LIMIT", f"{insertion}LIMIT")
        return f"{base_query} AND {filters}"

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM users u
            WHERE u.created_at BETWEEN %(start)s AND %(end)s
            """,
            user_filters,
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
            """,
            user_filters,
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
            """,
            user_filters,
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
            """,
            user_filters,
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
            """,
            user_filters,
        ),
        base_params,
    )
    revenue = cursor.fetchone()[0] or 0

    exam_params = {
        "start": start_dt,
        "end": end_dt,
    }
    exam_conditions = ["eu.comp_at BETWEEN %(start)s AND %(end)s"]
    if non_guest_condition:
        exam_conditions.append(non_guest_condition)
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
              AND j.fen = 'login'
            """,
            user_filters,
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
            """,
            user_filters,
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
            """,
            user_filters,
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
        {f"AND {non_guest_condition}" if non_guest_condition else ""}
        {"AND u.ex = %(plan)s" if plan is not None else ""}
        {"AND e.certi = %(cert_id)s" if cert_id is not None else ""}
        {"AND (u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)" if user_query else ""}
        """,
        exam_params,
    )
    total_exam_assignments = cursor.fetchone()[0] or 0

    cert_activity_conditions = ["eu.comp_at BETWEEN %(start)s AND %(end)s"]
    if non_guest_condition:
        cert_activity_conditions.append(non_guest_condition)
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
               COUNT(DISTINCT CASE WHEN j.fen = 'login' THEN j.id END) AS sessions,
               COUNT(DISTINCT eu.id) AS exams_completed
        FROM users u
        LEFT JOIN journs j
          ON j.user = u.id AND j.created_at BETWEEN %(start)s AND %(end)s
        LEFT JOIN exam_users eu
          ON eu.user = u.id AND eu.comp_at BETWEEN %(start)s AND %(end)s
        {"LEFT JOIN users_course uc ON uc.user = u.id" if cert_id is not None else ""}
        WHERE 1=1
        {f"AND {non_guest_condition}" if non_guest_condition else ""}
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

    cert_popularity_conditions = ["uc.created_at BETWEEN %(start)s AND %(end)s"]
    if non_guest_condition:
        cert_popularity_conditions.append(non_guest_condition)
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

    guest_metrics_params = {
        "start": start_dt,
        "end": end_dt,
        **guest_params,
    }
    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM users u
            WHERE u.created_at BETWEEN %(start)s AND %(end)s
            """,
            guest_filters,
        ),
        guest_metrics_params,
    )
    guest_new_users = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(DISTINCT j.user)
            FROM journs j
            JOIN users u ON u.id = j.user
            WHERE j.created_at BETWEEN %(start)s AND %(end)s
            """,
            guest_filters,
        ),
        guest_metrics_params,
    )
    guest_active_users = cursor.fetchone()[0] or 0

    cursor.execute(
        _apply_filters(
            """
            SELECT COUNT(*)
            FROM journs j
            JOIN users u ON u.id = j.user
            WHERE j.created_at BETWEEN %(start)s AND %(end)s
              AND j.fen = 'login'
            """,
            guest_filters,
        ),
        guest_metrics_params,
    )
    guest_sessions = cursor.fetchone()[0] or 0

    guest_exam_params = {
        "start": start_dt,
        "end": end_dt,
    }
    guest_exam_conditions = ["eu.comp_at BETWEEN %(start)s AND %(end)s"]
    if guest_condition:
        guest_exam_conditions.append(guest_condition)
    if plan is not None:
        guest_exam_params["plan"] = plan
        guest_exam_conditions.append("u.ex = %(plan)s")
    if cert_id is not None:
        guest_exam_params["cert_id"] = cert_id
        guest_exam_conditions.append("e.certi = %(cert_id)s")
    if user_query:
        guest_exam_params["user_query"] = f"%{user_query}%"
        guest_exam_conditions.append(
            "(u.name LIKE %(user_query)s OR u.email LIKE %(user_query)s OR u.usn LIKE %(user_query)s)"
        )
    guest_exam_where = " AND ".join(guest_exam_conditions)
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM exam_users eu
        JOIN users u ON u.id = eu.user
        JOIN exams e ON e.id = eu.exam
        WHERE {guest_exam_where}
        """,
        guest_exam_params,
    )
    guest_completed_exams = cursor.fetchone()[0] or 0

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
        "guests": {
            "new_users": guest_new_users,
            "active_users": guest_active_users,
            "sessions": guest_sessions,
            "completed_exams": guest_completed_exams,
        },
    }
