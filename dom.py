from flask import Flask, render_template, request, jsonify
import mysql.connector
import json
from config import DB_CONFIG

app = Flask(__name__)

# --- Routes pour l’interface ---
@app.route('/')
def index():
    return render_template('import_modules.html')

# --- API pour remplir les dropdowns ---
@app.route('/api/providers')
def api_providers():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

# --- API pour créer un domaine (module) ---
@app.route('/api/modules', methods=['POST'])
def api_create_module():
    data = request.get_json() or {}
    cert_id = data.get('certification_id')
    name    = data.get('name')
    descr   = data.get('descr')  # peut être None

    if not cert_id or not name:
        return jsonify({'error': 'certification_id et name requis'}), 400

    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO modules (name, descr, course) VALUES (%s, %s, %s)",
            (name, descr, cert_id)
        )
        conn.commit()
        new_id = cur.lastrowid
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

    return jsonify({'id': new_id, 'name': name})

if __name__ == '__main__':
    app.run(debug=True, port=9001)
