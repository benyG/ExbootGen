from flask import Blueprint, render_template, request, jsonify
import mysql.connector
from config import DB_CONFIG
from openai_api import generate_domains_outline

dom_bp = Blueprint('dom', __name__)

# --- Routes pour l’interface ---
@dom_bp.route('/')
def index():
    return render_template('import_modules.html')

# --- API pour remplir les dropdowns ---
@dom_bp.route('/api/providers')
def api_providers():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM provs")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@dom_bp.route('/api/certifications/<int:prov_id>')
def api_certs(prov_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM courses WHERE prov = %s", (prov_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)


@dom_bp.route('/api/certifications/<int:cert_id>/modules')
def api_modules_for_cert(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, descr FROM modules WHERE course = %s ORDER BY name",
        (cert_id,),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

# --- API pour créer un domaine (module) ---
@dom_bp.route('/api/modules', methods=['POST'])
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


@dom_bp.route('/api/certifications/<int:cert_id>/generate-domains', methods=['POST'])
def api_generate_domains(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT name FROM courses WHERE id = %s", (cert_id,))
    row = cur.fetchone()
    cur.close(); conn.close()

    if not row:
        return jsonify({'error': "Certification introuvable."}), 404

    cert_name = row[0]

    try:
        response = generate_domains_outline(cert_name)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    modules = response.get('modules') if isinstance(response, dict) else None
    if not isinstance(modules, list):
        return jsonify({'error': "Réponse invalide du modèle."}), 502

    cleaned = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        name = module.get('module_name') or module.get('name')
        descr = module.get('module_descr') or module.get('descr')
        if not name:
            continue
        cleaned.append({'module_name': name, 'module_descr': descr or ''})

    if not cleaned:
        return jsonify({'error': "Aucun domaine valide n'a été généré."}), 502

    return jsonify({'modules': cleaned, 'certification': cert_name})

