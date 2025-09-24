import time
import threading
import random
from flask import Flask, render_template, request, jsonify
from config import DISTRIBUTION, API_REQUEST_DELAY, GUI_PASSWORD
from openai_api import generate_questions, analyze_certif, correct_questions
from eraser_api import render_diagram
import db

from dom import dom_bp
from move import move_bp
from reloc import reloc_bp
from pdf_importer import pdf_bp
from quest import quest_bp

# Instanciation de l'application Flask
app = Flask(__name__, template_folder='templates')

# Enregistrement des blueprints
app.register_blueprint(dom_bp, url_prefix='/modules')
app.register_blueprint(move_bp, url_prefix='/move')
app.register_blueprint(reloc_bp, url_prefix='/reloc')
app.register_blueprint(pdf_bp, url_prefix='/pdf')
app.register_blueprint(quest_bp, url_prefix='/quest')

# Objet d'événement pour gérer la pause/reprise du processus.
pause_event = threading.Event()
pause_event.set()  # Par défaut, le processus n'est pas en pause.

# Données partagées pour suivre l'état d'avancement de la population
progress_data = {
    "status": "idle",
    "log": [],
    "counters": {
        "analysis": "",
        "domainsProcessed": 0,
        "totalDomains": 0,
        "totalQuestions": 0,
    },
}

# Définition de l'ordre des niveaux de difficulté
DIFFICULTY_LEVELS = ["easy", "medium", "hard"]

# Objectif global de questions par domaine
TARGET_PER_DOMAIN = 100

# Fonction pour tirer aléatoirement 0,1 ou 2 domaines secondaires
# Probabilités : 50% → 0, 30% → 1, 20% → 2
def pick_secondary_domains(all_domains, primary_domain):
    candidates = [d for d in all_domains if d != primary_domain]
    r = random.random()
    if r < 0.5:
        count = 0
    elif r < 0.8:
        count = 1
    else:
        count = 2
    # Assurer que count <= len(candidates)
    count = min(count, len(candidates))
    return random.sample(candidates, k=count)

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/populate", methods=["GET", "POST"])
def populate_index():
    if request.method == "POST":
        provider_id = int(request.form.get("provider_id"))
        cert_id = int(request.form.get("cert_id"))
        return render_template("populate.html", provider_id=provider_id, cert_id=cert_id, is_populating=True)
    providers = db.get_providers()
    return render_template("populate.html", providers=providers, is_populating=False)

@app.route("/populate/get_certifications", methods=["POST"])
def get_certifications():
    provider_id = int(request.form.get("provider_id"))
    certs = db.get_certifications_by_provider(provider_id)
    cert_list = [{"id": cert[0], "name": cert[1]} for cert in certs]
    return jsonify(cert_list)


def _compute_fix_progress(cert_id, action):
    if not cert_id:
        return {"total": 0, "corrected": 0, "remaining": 0}

    if action == "assign":
        total = db.count_questions_with_answers(cert_id)
        remaining = db.count_questions_missing_correct_answer(cert_id)
    elif action == "drag":
        nature_code = db.nature_mapping['drag-n-drop']
        total = db.count_questions_by_nature(cert_id, nature_code)
        remaining = db.count_questions_without_answers_by_nature(cert_id, nature_code)
    else:
        nature_code = db.nature_mapping['matching']
        total = db.count_questions_by_nature(cert_id, nature_code)
        remaining = db.count_questions_without_answers_by_nature(cert_id, nature_code)

    corrected = max(total - remaining, 0)
    return {"total": total, "corrected": corrected, "remaining": remaining}


def _resolve_provider_and_cert(provider_id, cert_id, provider_lookup=None, cert_lookup=None):
    provider_name = None
    if provider_lookup is not None:
        provider_name = provider_lookup.get(provider_id)
    if not provider_name:
        providers = db.get_providers()
        provider_name = next((p[1] for p in providers if p[0] == provider_id), None)
    if not provider_name:
        raise ValueError("Fournisseur introuvable.")

    if cert_lookup is None or cert_id not in cert_lookup:
        certs = db.get_certifications_by_provider(provider_id)
        cert_lookup = {c[0]: c[1] for c in certs}
    cert_name = cert_lookup.get(cert_id)
    if not cert_name:
        raise ValueError("Certification introuvable pour ce fournisseur.")

    return provider_name, cert_name


def _perform_fix_action(provider_id, cert_id, action, provider_lookup=None, cert_lookup=None):
    provider_name, cert_name = _resolve_provider_and_cert(
        provider_id, cert_id, provider_lookup, cert_lookup
    )

    updated_questions = 0

    if action == "assign":
        questions = db.get_questions_without_correct_answer(cert_id)
        if not questions:
            return {
                "message": "Aucune question sans réponse correcte à attribuer.",
                "updated_questions": 0,
            }
        results = correct_questions(provider_name, cert_name, questions, "assign")
        for res in results:
            question_id = res.get("question_id")
            answer_ids = res.get("answer_ids", [])
            if not question_id:
                continue
            db.mark_answers_correct(question_id, answer_ids)
            updated_questions += 1
        message = f"Attribuer Réponse juste effectué ({updated_questions} question(s) mise(s) à jour)."
    elif action == "drag":
        qlist = db.get_questions_without_answers_by_nature(
            cert_id, db.nature_mapping['drag-n-drop']
        )
        if not qlist:
            return {
                "message": "Aucune question de type drag-n-drop à compléter.",
                "updated_questions": 0,
            }
        results = correct_questions(provider_name, cert_name, qlist, "drag")
        for res in results:
            question_id = res.get("question_id")
            answers = res.get("answers", [])
            if not question_id:
                continue
            db.add_answers(question_id, answers)
            updated_questions += 1
        message = (
            f"Compléter Drag-n-drop effectué ({updated_questions} question(s) mise(s) à jour)."
        )
    else:
        qlist = db.get_questions_without_answers_by_nature(
            cert_id, db.nature_mapping['matching']
        )
        if not qlist:
            return {
                "message": "Aucune question de type matching à compléter.",
                "updated_questions": 0,
            }
        results = correct_questions(provider_name, cert_name, qlist, "matching")
        for res in results:
            question_id = res.get("question_id")
            answers = res.get("answers", [])
            if not question_id:
                continue
            db.add_answers(question_id, answers)
            updated_questions += 1
        message = (
            f"Compléter matching effectué ({updated_questions} question(s) mise(s) à jour)."
        )

    return {"message": message, "updated_questions": updated_questions}


@app.route("/fix", methods=["GET", "POST"])
def fix_index():
    providers = db.get_providers()
    provider_lookup = {p[0]: p[1] for p in providers}

    selected_provider_id = providers[0][0] if providers else None
    selected_cert_id = None
    selected_action = "assign"
    result_message = None
    initial_progress = None

    if request.method == "POST":
        provider_id = int(request.form.get("provider_id"))
        cert_id = int(request.form.get("cert_id"))
        action = request.form.get("action")

        selected_provider_id = provider_id
        selected_cert_id = cert_id
        selected_action = action

        certs = db.get_certifications_by_provider(provider_id)
        cert_lookup = {c[0]: c[1] for c in certs}

        try:
            result = _perform_fix_action(
                provider_id,
                cert_id,
                action,
                provider_lookup=provider_lookup,
                cert_lookup=cert_lookup,
            )
            result_message = result.get("message")
        except ValueError as exc:
            result_message = str(exc)
        initial_progress = _compute_fix_progress(cert_id, action)
    else:
        if not selected_provider_id:
            initial_progress = {"total": 0, "corrected": 0, "remaining": 0}

    return render_template(
        "fix.html",
        providers=providers,
        result=result_message,
        selected_provider_id=selected_provider_id,
        selected_cert_id=selected_cert_id,
        selected_action=selected_action,
        initial_progress=initial_progress,
    )


@app.route("/fix/get_certifications", methods=["POST"])
def fix_get_certifications():
    provider_id = int(request.form.get("provider_id"))
    certs = db.get_certifications_by_provider(provider_id)
    cert_list = [{"id": cert[0], "name": cert[1]} for cert in certs]
    return jsonify(cert_list)


@app.route("/fix/get_progress", methods=["POST"])
def fix_get_progress():
    cert_id = request.form.get("cert_id", type=int)
    action = request.form.get("action", type=str) or "assign"
    progress = _compute_fix_progress(cert_id, action)
    return jsonify(progress)


@app.route("/fix/run_action", methods=["POST"])
def fix_run_action():
    provider_id = request.form.get("provider_id", type=int)
    cert_id = request.form.get("cert_id", type=int)
    action = request.form.get("action", type=str) or "assign"

    if not provider_id or not cert_id:
        return jsonify({"success": False, "error": "Veuillez sélectionner un fournisseur et une certification."}), 400

    try:
        result = _perform_fix_action(provider_id, cert_id, action)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - log unexpected errors
        app.logger.exception("Unexpected error during fix action", exc_info=exc)
        return jsonify({"success": False, "error": "Une erreur inattendue est survenue."}), 500

    return jsonify({"success": True, "message": result.get("message", "")})


def run_population(provider_id, cert_id):
    """Execute the population process and update ``progress_data`` in real time."""
    global progress_data

    provider_name = next((p[1] for p in db.get_providers() if p[0] == provider_id), None)
    if not provider_name:
        progress_data["log"].append(f"Provider with id {provider_id} not found.")
        progress_data["status"] = "error"
        return
    cert_name = next((c[1] for c in db.get_certifications_by_provider(provider_id) if c[0] == cert_id), None)
    if not cert_name:
        progress_data["log"].append(f"Certification with id {cert_id} not found.")
        progress_data["status"] = "error"
        return

    # Certification analysis
    try:
        analysis_result = analyze_certif(provider_name, cert_name)
        analysis = {k: str(v).strip('"') for d in analysis_result for k, v in d.items()}
        log_analysis = f"Certification analysis: {analysis}"
    except Exception as e:
        analysis = {}
        log_analysis = f"Certification analysis unavailable: {e}"

    progress_data["log"].append(log_analysis)
    progress_data["counters"]["analysis"] = log_analysis

    # Récupérer tous les domaines et leurs noms
    domains = db.get_domains_by_certification(cert_id)
    all_domain_names = [name for (_, name) in domains]
    total_domains = len(domains)
    progress_data["counters"]["totalDomains"] = total_domains
    domains_processed = 0

    for domain_id, domain_name in domains:
        pause_event.wait()
        progress_data["log"].append(f"[{domain_name}] Domain processing started.")
        current_total = db.count_total_questions(domain_id)
        progress_data["log"].append(f"[{domain_name}] Initial question count: {current_total}")

        if current_total >= TARGET_PER_DOMAIN:
            domains_processed += 1
            progress_data["log"].append(
                f"[{domain_name}] Domain already complete (>= {TARGET_PER_DOMAIN} questions)."
            )
            progress_data["counters"]["domainsProcessed"] = domains_processed
            progress_data["counters"]["totalQuestions"] = sum(
                db.count_total_questions(d[0]) for d in domains
            )
            continue

        progress_data["log"].append(
            f"[{domain_name}] Needs {TARGET_PER_DOMAIN - current_total} additional questions to reach {TARGET_PER_DOMAIN}."
        )

        # --- Élaboration par niveau de difficulté ---
        # Traitement EASY si le domaine est vide
        if current_total == 0:
            pause_event.wait()
            progress_data["log"].append(f"[{domain_name}] Empty domain, using EASY distribution.")
            distribution = DISTRIBUTION.get("easy", {})
            progress_data["log"] = process_domain_by_difficulty(
                domain_id, domain_name, "easy", distribution,
                provider_name, cert_id, cert_name, analysis, progress_data["log"], all_domain_names
            )
            current_total = db.count_total_questions(domain_id)
            progress_data["log"].append(f"[{domain_name}] Total after EASY: {current_total}")

        # Traitement MEDIUM si nécessaire
        if current_total < TARGET_PER_DOMAIN:
            pause_event.wait()
            needed_total = TARGET_PER_DOMAIN - current_total
            progress_data["log"].append(
                f"[{domain_name}] Needs {needed_total} additional questions via MEDIUM."
            )
            distribution = DISTRIBUTION.get("medium", {})
            progress_data["log"] = process_domain_by_difficulty(
                domain_id, domain_name, "medium", distribution,
                provider_name, cert_id, cert_name, analysis, progress_data["log"], all_domain_names
            )
            current_total = db.count_total_questions(domain_id)
            progress_data["log"].append(f"[{domain_name}] Total after MEDIUM: {current_total}")

        # Traitement HARD si toujours nécessaire
        if current_total < TARGET_PER_DOMAIN:
            pause_event.wait()
            needed_total = TARGET_PER_DOMAIN - current_total
            progress_data["log"].append(
                f"[{domain_name}] Needs {needed_total} additional questions via HARD."
            )
            distribution = DISTRIBUTION.get("hard", {})
            progress_data["log"] = process_domain_by_difficulty(
                domain_id, domain_name, "hard", distribution,
                provider_name, cert_id, cert_name, analysis, progress_data["log"], all_domain_names
            )
            current_total = db.count_total_questions(domain_id)
            progress_data["log"].append(f"[{domain_name}] Total after HARD: {current_total}")

        # Fallback transverse
        if current_total < TARGET_PER_DOMAIN:
            pause_event.wait()
            needed_total = TARGET_PER_DOMAIN - current_total
            progress_data["log"].append(
                f"[{domain_name}] After HARD = {current_total}. Fallback of {needed_total} questions."
            )
            practical_val = random.choice(['no', 'scenario'])
            secondaries = pick_secondary_domains(all_domain_names, domain_name)
            progress_data["log"].append(
                f"[{domain_name} - FALLBACK] Practical: {practical_val}, Secondary domains: {secondaries}"
            )
            if secondaries:
                domain_arg = f"main domain :{domain_name}; includes context from domains: {', '.join(secondaries)}"
            else:
                domain_arg = domain_name
            if practical_val == 'scenario':
                candidates = [k for k, v in analysis.items() if v == '1']
                scenario_illu_val = random.choice(candidates) if candidates else 'none'
            else:
                scenario_illu_val = 'none'
            try:
                domain_info = db.get_domains_description_by_certif(cert_id)
                desc = next(d["descr"] for d in domain_info if d["id"] == domain_id)
                questions_data = generate_questions(
                    provider_name=provider_name,
                    certification=cert_name,
                    domain=domain_arg,
                    domain_descr=desc,
                    level="medium",
                    q_type="qcm",
                    practical=practical_val,
                    scenario_illustration_type=scenario_illu_val,
                    num_questions=needed_total
                )
                time.sleep(API_REQUEST_DELAY)
                db.insert_questions(domain_id, questions_data, practical_val)
                progress_data["log"].append(
                    f"[{domain_name}] {needed_total} fallback questions inserted."
                )
            except Exception as e:
                progress_data["log"].append(
                    f"[{domain_name}] Error during fallback generation: {e}"
                )

        domains_processed += 1
        final_total = db.count_total_questions(domain_id)
        progress_data["log"].append(
            f"[{domain_name}] Domain completed: final total = {final_total} ({domains_processed}/{total_domains})."
        )
        progress_data["counters"]["domainsProcessed"] = domains_processed
        progress_data["counters"]["totalQuestions"] = sum(
            db.count_total_questions(d[0]) for d in domains
        )

    progress_data["log"].append(
        f"Process finished: {domains_processed} domains processed out of {total_domains}."
    )
    progress_data["status"] = "completed"


@app.route("/populate/process", methods=["POST"])
def populate_process():
    provider_id = int(request.form.get("provider_id"))
    cert_id = int(request.form.get("cert_id"))
    # Réinitialiser les données de progression
    progress_data["status"] = "running"
    progress_data["log"] = []
    progress_data["counters"] = {
        "analysis": "",
        "domainsProcessed": 0,
        "totalDomains": 0,
        "totalQuestions": 0,
    }
    threading.Thread(target=run_population, args=(provider_id, cert_id), daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/populate/status", methods=["GET"])
def populate_status():
    return jsonify(progress_data)


def process_domain_by_difficulty(domain_id, domain_name, difficulty, distribution,
                                 provider_name, cert_id, cert_name, analysis, progress_log, all_domain_names):
    """Generate and insert questions for a domain according to the distribution.

    Secondary domains are randomly injected when ``practical`` is not "no".
    """
    import json  # local import to avoid dependency at module import time

    for qtype, scenarios in distribution.items():
        for scenario_type, target_count in scenarios.items():
            # Determine practical and scenario_illustration_type parameters
            if scenario_type == "no":
                practical_val = "no"
                scenario_illu_val = "none"
            elif scenario_type == "scenario":
                practical_val = "scenario"
                candidates = [k for k, v in analysis.items() if v == '1']
                scenario_illu_val = random.choice(candidates) if candidates else 'none'
            elif scenario_type == "scenario-illustrated":
                practical_val = "scenario-illustrated"
                candidates = [k for k, v in analysis.items() if v == '1' and k != 'case']
                scenario_illu_val = random.choice(candidates) if candidates else 'none'
            else:
                practical_val = "no"
                scenario_illu_val = "none"

            existing_count = db.count_questions_in_category(domain_id, difficulty, qtype, scenario_type)
            progress_log.append(
                f"[{domain_name} - {difficulty.upper()}] {qtype} with scenario '{scenario_type}' existing: "
                f"{existing_count} (target: {target_count})."
            )
            if existing_count < target_count:
                pause_event.wait()
                needed = target_count - existing_count
                progress_log.append(
                    f"[{domain_name} - {difficulty.upper()}] Needs {needed} questions for "
                    f"{qtype} with scenario '{scenario_type}'."
                )
                if practical_val != 'no':
                    secondaries = pick_secondary_domains(all_domain_names, domain_name)
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] Secondary domains: {secondaries}"
                    )
                    domain_arg = (
                        f"main domain :{domain_name}; includes context from domains: {', '.join(secondaries)}"
                        if secondaries else domain_name
                    )
                else:
                    domain_arg = domain_name

                try:
                    domain_info = db.get_domains_description_by_certif(cert_id)
                    desc = next(d["descr"] for d in domain_info if d["id"] == domain_id)
                    questions_data = generate_questions(
                        provider_name=provider_name,
                        certification=cert_name,
                        domain=domain_arg,
                        domain_descr=desc,
                        level=difficulty,
                        q_type=qtype,
                        practical=practical_val,
                        scenario_illustration_type=scenario_illu_val,
                        num_questions=needed
                    )
                    time.sleep(API_REQUEST_DELAY)
                except Exception as e:
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] Generation error for {qtype} "
                        f"with scenario '{scenario_type}': {e}"
                    )
                    continue

                for question in questions_data.get("questions", []):
                    if practical_val == "scenario-illustrated" and question.get("diagram_descr"):
                        diagram_description = question.get("diagram_descr", "").strip()
                        try:
                            diag_type = question.get("diagram_type", "")
                            # diagram_data_str = render_diagram(provider_name, diagram_description, diag_type)
                            # diag_dict = json.loads(diagram_data_str)
                            # question["image"] = (
                            #     f'<img src="{diag_dict["imageUrl"]}" alt="Generated Diagram" '
                            #     f'width="75%" height="auto"><!-- {diag_dict["createEraserFileUrl"]} -->'
                            # )
                        except Exception as e:
                            progress_log.append(
                                f"[{domain_name} - {difficulty.upper()}] Diagram error for {qtype} "
                                f"with scenario '{scenario_type}' (desc: {diagram_description}, type: {diag_type}): {e}"
                            )
                            question["image"] = ""

                try:
                    db.insert_questions(domain_id, questions_data, scenario_type)
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] {needed} questions inserted for "
                        f"{qtype} with scenario '{scenario_type}'."
                    )
                except Exception as e:
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] Insert error for {qtype} "
                        f"with scenario '{scenario_type}': {e}"
                    )
    return progress_log

@app.route("/populate/pause", methods=["POST"])
def pause_populate():
    pause_event.clear()
    return jsonify({"status": "paused"})

@app.route("/populate/resume", methods=["POST"])
def resume_populate():
    pause_event.set()
    return jsonify({"status": "resumed"})


def launch_gui():
    """Display a simple GUI to control the Flask web service.

    A password (configured via ``config.GUI_PASSWORD``) is required before
    accessing the controls.  Once authenticated, the interface offers buttons to
    start or stop the web service listening on port 5000.
    """

    import tkinter as tk
    from tkinter import messagebox
    from werkzeug.serving import make_server

    class ServerThread(threading.Thread):
        """Run the Flask application in a background thread."""

        def __init__(self):
            super().__init__(daemon=True)
            self.server = make_server("0.0.0.0", 5000, app)

        def run(self):
            self.server.serve_forever()

        def shutdown(self):
            self.server.shutdown()

    root = tk.Tk()
    root.title("ExbootGen Service")

    server_thread = None
    status_var = tk.StringVar(value="Stopped")

    def start_service():
        nonlocal server_thread
        if server_thread is None:
            server_thread = ServerThread()
            server_thread.start()
            status_var.set("Running")
        else:
            messagebox.showinfo("Info", "Service already running")

    def stop_service():
        nonlocal server_thread
        if server_thread:
            server_thread.shutdown()
            server_thread = None
            status_var.set("Stopped")
        else:
            messagebox.showinfo("Info", "Service not running")

    def authenticate():
        if password_var.get() == GUI_PASSWORD:
            login_frame.pack_forget()
            control_frame.pack(padx=10, pady=10)
        else:
            messagebox.showerror("Error", "Invalid password")

    login_frame = tk.Frame(root)
    tk.Label(login_frame, text="Password:").pack(side="left")
    password_var = tk.StringVar()
    tk.Entry(login_frame, textvariable=password_var, show="*").pack(side="left")
    tk.Button(login_frame, text="Login", command=authenticate).pack(side="left")
    login_frame.pack(padx=10, pady=10)

    control_frame = tk.Frame(root)
    tk.Label(control_frame, textvariable=status_var).pack()
    tk.Button(control_frame, text="Start Service", command=start_service).pack(fill="x")
    tk.Button(control_frame, text="Stop Service", command=stop_service).pack(fill="x")

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
