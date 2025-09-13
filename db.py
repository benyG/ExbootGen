import mysql.connector
import logging
import json
from config import DB_CONFIG

# Valeurs de niveau : easy→0, medium→1, hard→2
level_mapping = {"easy": 0, "medium": 1, "hard": 2}

# Mapping de type de question et de scénario en codes numériques
nature_mapping = {"qcm": 1, "truefalse": 2, "short-answer": 3, "matching": 4, "drag-n-drop": 5}
ty_mapping = {"no": 1, "scenario": 2, "scenario-illustrated": 3}

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


def get_domains_by_certification(cert_id):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT id, name FROM modules WHERE course = %s"
    cursor.execute(query, (cert_id,))
    domains = cursor.fetchall()
    cursor.close()
    conn.close()
    return domains


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
            cursor.execute(query_question, (
                question_text,
                diagram_descr,
                level_num,
                domain_id,
                nature_num,
                ty_num
            ))
            question_id = cursor.lastrowid
            logging.info(f"Inserted question ID: {question_id}")

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
                    logging.info(f"  Inserted answer ID: {answer_id}")
                except mysql.connector.Error as err:
                    if err.errno == 1062:
                        query_select = "SELECT id FROM answers WHERE text = %s"
                        cursor.execute(query_select, (answer_json,))
                        result = cursor.fetchone()
                        if result:
                            answer_id = result[0]
                            logging.info(f"  Duplicate found, using existing answer ID: {answer_id}")
                        else:
                            raise
                    else:
                        raise

                query_link = "INSERT INTO quest_ans (question, answer, isok) VALUES (%s, %s, %s)"
                cursor.execute(query_link, (question_id, answer_id, isok))
        conn.commit()
        logging.info("Insertion completed")
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


def get_provider_name(provider_id):
    """Return provider name for given id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM provs WHERE id=%s", (provider_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else ""


def get_certification_name(cert_id):
    """Return certification name for given id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM courses WHERE id=%s", (cert_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else ""


def get_questions_without_correct_answer(cert_id):
    """Return questions with answers but none marked correct."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    query = (
        """
        SELECT q.id AS question_id, q.text, a.id AS answer_id, a.text AS answer_text
        FROM questions q
        JOIN quest_ans qa ON qa.question = q.id
        JOIN answers a ON a.id = qa.answer
        WHERE q.module IN (SELECT id FROM modules WHERE course = %s)
          AND NOT EXISTS (
              SELECT 1 FROM quest_ans qa2 WHERE qa2.question = q.id AND qa2.isok = 1
          )
        ORDER BY q.id
        """
    )
    cur.execute(query, (cert_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    questions = {}
    for r in rows:
        qid = r["question_id"]
        if qid not in questions:
            questions[qid] = {"question_id": qid, "text": r["text"], "answers": []}
        questions[qid]["answers"].append({"id": r["answer_id"], "text": r["answer_text"]})
    return list(questions.values())


def get_questions_without_answers(cert_id, q_type):
    """Return questions of given type with no answers."""
    nature_num = nature_mapping.get(q_type, 0)
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    query = (
        """
        SELECT q.id AS question_id, q.text
        FROM questions q
        WHERE q.module IN (SELECT id FROM modules WHERE course = %s)
          AND q.nature = %s
          AND NOT EXISTS (SELECT 1 FROM quest_ans qa WHERE qa.question = q.id)
        """
    )
    cur.execute(query, (cert_id, nature_num))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def mark_correct_answers(question_id, answer_ids):
    """Mark the provided answers as correct for the question."""
    if not answer_ids:
        return
    conn = get_connection()
    cur = conn.cursor()
    for aid in answer_ids:
        cur.execute(
            "UPDATE quest_ans SET isok=1 WHERE question=%s AND answer=%s",
            (question_id, aid),
        )
    conn.commit()
    cur.close()
    conn.close()


def add_answers(question_id, answers):
    """Insert generated answers for a question."""
    if not answers:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        for ans in answers:
            value = (ans.get("value") or "").strip()
            target = (ans.get("target") or "").strip()
            answer_data = {"value": value}
            if target:
                answer_data["target"] = target
            a_json = json.dumps(answer_data, ensure_ascii=False)[:700]
            cur.execute(
                "INSERT INTO answers (text, created_at) VALUES (%s,NOW())",
                (a_json,),
            )
            ans_id = cur.lastrowid
            isok = int(ans.get("isok", 0))
            cur.execute(
                "INSERT INTO quest_ans (question, answer, isok) VALUES (%s,%s,%s)",
                (question_id, ans_id, isok),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()
