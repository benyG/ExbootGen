import time
import threading
import random
from flask import Flask, render_template, request, jsonify
from config import DISTRIBUTION, API_REQUEST_DELAY
from openai_api import generate_questions, analyze_certif
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

if __name__ == "__main__":
    app.run(debug=True)
