import os
from flask import Blueprint, render_template, jsonify, request, g
import mysql.connector
from config import DB_CONFIG

move_bp = Blueprint('move', __name__)

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

@move_bp.teardown_request
def close_db(exc):
    """Ferme la connexion à la fin de la requête."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

@move_bp.route('/')
def index():
    default_pdf_dir = os.environ.get("DEFAULT_PDF_DIR", r"C:\\dumps\\dumps")
    return render_template('move_questions.html', default_pdf_dir=default_pdf_dir)

@move_bp.route('/api/providers')
def api_providers():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@move_bp.route('/api/certifications/<int:prov_id>')
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

@move_bp.route('/api/domains/<int:cert_id>')
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

@move_bp.route('/api/move', methods=['POST'])
def api_move():
    data = request.get_json() or {}
    src_modules = data.get('source_module_ids', [])
    dst_module = data.get('destination_module_id')
    source_file_mode = bool(data.get('source_file_mode'))
    source_file_name = (data.get('source_file_name') or '').strip()

    if dst_module is None:
        return jsonify({'error': 'Paramètres manquants'}), 400

    if source_file_mode:
        if not source_file_name:
            return jsonify({'error': 'Nom de fichier source manquant'}), 400
    elif not src_modules:
        return jsonify({'error': 'Paramètres manquants'}), 400

    db = get_db()
    cur = db.cursor()
    if source_file_mode:
        cur.execute(
            """
            UPDATE questions
               SET module = %s
             WHERE src_file LIKE %s
            """,
            (dst_module, f"{source_file_name}%")
        )
    else:
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
