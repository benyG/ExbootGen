import mysql.connector
import logging
import json
from concurrent.futures import ThreadPoolExecutor
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
                INSERT INTO questions (text, descr, level, module, nature, ty, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """
            try:
                cursor.execute(query_question, (
                    question_text,
                    diagram_descr,
                    level_num,
                    domain_id,
                    nature_num,
                    ty_num
                ))
                question_id = cursor.lastrowid
                q_imported += 1
                logging.info(f"Inserted question ID: {question_id}")
            except mysql.connector.Error as err:
                if err.errno == 1062:
                    logging.info("Duplicate question skipped")
                    q_skipped += 1
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
    avec leur id, nom et description textuelle.
    """
    conn = get_connection()
    cursor = conn.cursor()
    # On suppose que la table `modules` a une colonne `descr` qui contient la description
    query = "SELECT id, name, descr FROM modules WHERE course = %s"
    cursor.execute(query, (cert_id,))
    domains = cursor.fetchall()  # liste de tuples (id, name, descr)
    cursor.close()
    conn.close()
    # On renvoie sous forme de liste de dicts pour plus de clarté
    return [{"id": d[0], "name": d[1], "descr": d[2]} for d in domains]


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
