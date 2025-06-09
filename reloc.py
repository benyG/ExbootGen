# reloc.py

from flask import Flask, render_template, request, Response
import mysql.connector
import requests
import json
import re
from time import sleep
from config import OPENAI_API_KEY, OPENAI_MODEL, DB_CONFIG

OPENAI_ENDPOINT = 'https://api.openai.com/v1/chat/completions'

app = Flask(__name__)

# -- Routes pour remplir les dropdowns --
@app.route('/')
def index():
    return render_template('reloc.html')

@app.route('/api/providers')
def api_providers():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}

@app.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}

@app.route('/api/modules/<int:cert_id>')
def api_modules(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM modules WHERE course = %s", (cert_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return json.dumps(rows, ensure_ascii=False), 200, {'Content-Type':'application/json'}

# -- SSE pour le streaming de la relocalisation --
@app.route('/api/stream_relocate', methods=['GET'])
def stream_relocate():
    src_module = request.args.get('source_module_id',    type=int)
    dst_cert    = request.args.get('destination_cert_id', type=int)
    batch_size  = request.args.get('batch_size',          default=10, type=int)

    if not src_module or not dst_cert:
        return {"error": "source_module_id et destination_cert_id sont requis"}, 400

    def generate():
        # 1) Charger la liste des modules de destination (une seule fois)
        conn0 = mysql.connector.connect(**DB_CONFIG)
        cur0  = conn0.cursor(dictionary=True)
        cur0.execute("SELECT id, name FROM modules WHERE course = %s", (dst_cert,))
        modules = cur0.fetchall()
        cur0.close(); conn0.close()

        offset = 0
        total_moved = 0

        while True:
            # 2) Récupérer un batch de questions
            conn1 = mysql.connector.connect(**DB_CONFIG)
            cur1  = conn1.cursor(dictionary=True)
            cur1.execute(
                "SELECT id, text FROM questions WHERE module = %s "
                "LIMIT %s OFFSET %s",
                (src_module, batch_size, offset)
            )
            questions = cur1.fetchall()
            cur1.close(); conn1.close()

            if not questions:
                break

            # 3) Construire le prompt pour l’IA
            modules_info   = json.dumps(modules,   ensure_ascii=False)
            questions_info = json.dumps(
                [{'question_id': q['id'], 'text': q['text']} for q in questions],
                ensure_ascii=False
            )
            prompt = (
                "TASK: As a coach proficient in the exam curriculum, assign each question to the correct domain using the module ID.\n"
                "FORMAT: JSON array of objects {question_id: X, domain_to_affect: Y} where Y is module id.\n"
                f"Exam id: {dst_cert}\n"
                f"Domains: {modules_info}\n"
                f"Questions: {questions_info}\n"
            )

            # 4) Appel à l’API OpenAI
            payload = {
                "model":    OPENAI_MODEL,
                "messages":[{"role":"user","content":prompt}]
            }
            try:
                resp = requests.post(
                    OPENAI_ENDPOINT,
                    headers={
                        'Content-Type':  'application/json',
                        'Authorization': f'Bearer {OPENAI_API_KEY}'
                    },
                    json=payload,
                    timeout=60
                )
                resp.raise_for_status()
            except Exception as e:
                yield f"data: ERREUR OpenAI: {e}\n\n"
                return

            choices = resp.json().get('choices', [])
            if not choices or 'message' not in choices[0]:
                yield "data: ERREUR réponse OpenAI invalide\n\n"
                return

            content = choices[0]['message']['content']
            # 5) Nettoyage et parsing du JSON renvoyé
            cleaned = re.sub(r"```json|```", "", content).strip()
            try:
                mapping = json.loads(cleaned)
            except json.JSONDecodeError:
                cleaned2 = cleaned.replace("\n", "").replace("\\", "")
                try:
                    mapping = json.loads(cleaned2)
                except Exception as e:
                    yield f"data: ERREUR parsing JSON IA: {e}\n\n"
                    return

            # 6) Mise à jour en base pour ce batch
            conn2 = mysql.connector.connect(**DB_CONFIG)
            cur2  = conn2.cursor()
            moved = 0
            for item in mapping:
                qid    = item.get('question_id')
                mod_id = item.get('domain_to_affect')
                if isinstance(qid, int) and isinstance(mod_id, int):
                    cur2.execute(
                        "UPDATE questions SET module=%s WHERE id=%s",
                        (mod_id, qid)
                    )
                    moved += cur2.rowcount
            conn2.commit()
            cur2.close()
            conn2.close()

            total_moved += moved
            offset += batch_size

            # 7) Envoi de l’événement SSE pour ce batch
            yield f"data: Batch offset={offset}, moved={moved}, total={total_moved}\n\n"
            sleep(0.1)

        # 8) Fin du flux
        if total_moved == 0:
            yield "data: Aucun traitement (0 questions trouvées)\n\n"
        else:
            yield f"data: DONE, total moved={total_moved}\n\n"

    return Response(generate(), mimetype='text/event-stream')


if __name__ == '__main__':
    app.run(debug=True, port=9000)
