from flask import Flask, render_template, jsonify, request, g
import mysql.connector
from config import DB_CONFIG

app = Flask(__name__)

def get_db():
    """Ouvre une connexion MySQL si nécessaire et la stocke dans g."""
    if 'db' not in g:
        g.db = mysql.connector.connect(
            host     = DB_CONFIG['host'],
            user     = DB_CONFIG['user'],
            password = DB_CONFIG['password'],
            database = DB_CONFIG['database'],
            charset  = 'utf8mb4'
        )
    return g.db

@app.teardown_appcontext
def close_db(exc):
    """Ferme la connexion à la fin de la requête."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.route('/')
def index():
    return render_template('move_questions.html')

@app.route('/api/providers')
def api_providers():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@app.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name FROM courses WHERE prov = %s",
        (prov_id,)
    )
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@app.route('/api/domains/<int:cert_id>')
def api_domains(cert_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name FROM modules WHERE course = %s",
        (cert_id,)
    )
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@app.route('/api/move', methods=['POST'])
def api_move():
    data = request.get_json()
    src_modules = data.get('source_module_ids', [])
    dst_module  = data.get('destination_module_id')

    if not src_modules or dst_module is None:
        return jsonify({'error': 'Paramètres manquants'}), 400

    db = get_db()
    cur = db.cursor()
    placeholders = ','.join(['%s'] * len(src_modules))
    sql = f"""
        UPDATE questions
           SET module = %s
         WHERE module IN ({placeholders})
    """
    params = [dst_module] + src_modules
    cur.execute(sql, params)
    moved = cur.rowcount
    db.commit()
    cur.close()

    return jsonify({'moved': moved})

if __name__ == '__main__':
    app.run(debug=True, port=8000)  # <-- ajuste le port si besoin