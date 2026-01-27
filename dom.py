from flask import Blueprint, render_template, request, jsonify
import mysql.connector
from config import DB_CONFIG
from openai_api import generate_domains_outline

dom_bp = Blueprint('dom', __name__)


def _clean_generated_modules(response) -> list[dict]:
    if isinstance(response, list):
        modules = response
    elif isinstance(response, dict):
        modules = response.get('modules')
    else:
        modules = None

    if not isinstance(modules, list):
        raise ValueError("Réponse invalide du modèle.")

    cleaned = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        name = module.get('name') or module.get('module_name')
        descr = (
            module.get('descr')
            or module.get('module_descr')
            or module.get('description')
        )
        if not name:
            continue
        cleaned.append({'module_name': name, 'module_descr': (descr or '').strip()})

    if not cleaned:
        raise ValueError("Aucun domaine valide n'a été généré.")

    failure_keywords = (
        'unable to access',
        'cannot retrieve',
        'pas en mesure',
        'not available',
        'no puedo',
        'non posso',
        'nicht in der lage',
        "impossible d'accéder",
        'error',
    )
    for module in cleaned:
        name_lower = module['module_name'].lower()
        descr_lower = module['module_descr'].lower()
        if any(keyword in name_lower for keyword in ('error', 'n/a', 'not available')):
            raise ValueError(
                "Le modèle n'a pas fourni les domaines officiels. "
                "Merci de saisir manuellement les informations issues du site du fournisseur "
                "ou de réessayer avec un autre prompt."
            )
        if any(keyword in descr_lower for keyword in failure_keywords):
            raise ValueError(
                "Le modèle n'a pas pu récupérer les domaines officiels. "
                "Veuillez fournir manuellement l'outline officiel ou réessayer."
            )

    return cleaned

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
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, code_cert_key AS code_cert, pub FROM courses WHERE prov = %s",
        (prov_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)


@dom_bp.route('/api/certifications/<int:cert_id>/modules')
def api_modules_for_cert(cert_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, descr, code_cert FROM modules WHERE course = %s ORDER BY name",
        (cert_id,),
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)


@dom_bp.route('/api/default-module')
def api_default_module():
    code_cert = (request.args.get('code_cert') or '').strip()
    if not code_cert:
        return jsonify({"error": "code_cert requis"}), 400

    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT m.id AS module_id, m.course AS cert_id, c.prov AS provider_id
        FROM modules m
        JOIN courses c ON c.id = m.course
        WHERE m.code_cert = %s
        ORDER BY m.id DESC
        LIMIT 1
        """,
        (code_cert,),
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return jsonify({"module_id": None, "cert_id": None, "provider_id": None})
    return jsonify(row)

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
        cleaned = _clean_generated_modules(response)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502

    return jsonify({'modules': cleaned, 'certification': cert_name})


@dom_bp.route('/api/mcp/certifications/<int:cert_id>/sync-domains', methods=['POST'])
def api_sync_domains(cert_id):
    """Generate domains via IA and insert missing modules for MCP."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM courses WHERE id = %s", (cert_id,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        return jsonify({'error': "Certification introuvable."}), 404

    cert_name = row["name"]
    try:
        response = generate_domains_outline(cert_name)
        cleaned = _clean_generated_modules(response)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502

    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    created = 0
    updated = 0
    results = []
    try:
        cur.execute(
            "SELECT id, name, descr FROM modules WHERE course = %s",
            (cert_id,),
        )
        existing = {row["name"].strip().lower(): row for row in cur.fetchall()}

        for module in cleaned:
            name = module["module_name"].strip()
            descr = module["module_descr"].strip()
            key = name.lower()
            existing_row = existing.get(key)
            if existing_row:
                if descr and not (existing_row.get("descr") or "").strip():
                    cur.execute(
                        "UPDATE modules SET descr = %s WHERE id = %s",
                        (descr, existing_row["id"]),
                    )
                    updated += cur.rowcount
                    results.append(
                        {
                            "module_id": existing_row["id"],
                            "module_name": name,
                            "module_descr": descr or (existing_row.get("descr") or "").strip(),
                            "status": "updated",
                        }
                    )
                else:
                    results.append(
                        {
                            "module_id": existing_row["id"],
                            "module_name": name,
                            "module_descr": (existing_row.get("descr") or "").strip() or descr,
                            "status": "unchanged",
                        }
                    )
                continue

            cur.execute(
                "INSERT INTO modules (name, descr, course) VALUES (%s, %s, %s)",
                (name, descr or None, cert_id),
            )
            created += 1
            results.append(
                {
                    "module_id": cur.lastrowid,
                    "module_name": name,
                    "module_descr": descr,
                    "status": "created",
                }
            )
        conn.commit()
    except mysql.connector.Error as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify(
        {
            "certification": cert_name,
            "created": created,
            "updated": updated,
            "processed": len(results),
            "results": results,
        }
    )
