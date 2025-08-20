import time
import threading
import random
from flask import Flask, render_template, request, jsonify
from config import DISTRIBUTION
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

@app.route("/populate/process", methods=["POST"])
def populate_process():
    provider_id = int(request.form.get("provider_id"))
    cert_id = int(request.form.get("cert_id"))
    
    # Récupération des noms provider et certification
    provider_name = next((p[1] for p in db.get_providers() if p[0] == provider_id), None)
    if not provider_name:
        return jsonify({"status": "error", "log": [f"Provider with id {provider_id} not found."]})
    cert_name = next((c[1] for c in db.get_certifications_by_provider(provider_id) if c[0] == cert_id), None)
    if not cert_name:
        return jsonify({"status": "error", "log": [f"Certification with id {cert_id} not found."]})

    # Analyse de la certification
    try:
        analysis_result = analyze_certif(provider_name, cert_name)
        analysis = {k: str(v).strip('"') for d in analysis_result for k, v in d.items()}
        log_analysis = f"Analyse de certification: {analysis}"
    except Exception as e:
        analysis = {}
        log_analysis = f"Analyse de certification non disponible: {e}"
    
    progress_log = [log_analysis]

    # Récupérer tous les domaines et leurs noms
    domains = db.get_domains_by_certification(cert_id)
    all_domain_names = [name for (_, name) in domains]
    total_domains = len(domains)
    domains_processed = 0

    for domain_id, domain_name in domains:
        progress_log.append(f"[{domain_name}] Début du traitement du domaine.")
        current_total = db.count_total_questions(domain_id)
        progress_log.append(f"[{domain_name}] Total questions initiales: {current_total}")
        
        if current_total >= TARGET_PER_DOMAIN:
            domains_processed += 1
            progress_log.append(f"[{domain_name}] Domaine déjà traité (>= {TARGET_PER_DOMAIN} questions).")
            continue
        
        progress_log.append(f"[{domain_name}] Besoin de {TARGET_PER_DOMAIN - current_total} questions supplémentaires pour atteindre {TARGET_PER_DOMAIN}.")
        
        # --- Élaboration par niveau de difficulté ---
        # Traitement EASY si le domaine est vide
        if current_total == 0:
            progress_log.append(f"[{domain_name}] Domaine vide, utilisation de la distribution EASY.")
            distribution = DISTRIBUTION.get("easy", {})
            progress_log = process_domain_by_difficulty(
                domain_id, domain_name, "easy", distribution,
                provider_name, cert_name, analysis, progress_log, all_domain_names
            )
            current_total = db.count_total_questions(domain_id)
            progress_log.append(f"[{domain_name}] Total après EASY: {current_total}")
        
        # Traitement MEDIUM si nécessaire
        if current_total < TARGET_PER_DOMAIN:
            needed_total = TARGET_PER_DOMAIN - current_total
            progress_log.append(f"[{domain_name}] Besoin de {needed_total} questions supplémentaires via MEDIUM.")
            distribution = DISTRIBUTION.get("medium", {})
            progress_log = process_domain_by_difficulty(
                domain_id, domain_name, "medium", distribution,
                provider_name, cert_name, analysis, progress_log, all_domain_names
            )
            current_total = db.count_total_questions(domain_id)
            progress_log.append(f"[{domain_name}] Total après MEDIUM: {current_total}")
        
        # Traitement HARD si toujours nécessaire
        if current_total < TARGET_PER_DOMAIN:
            needed_total = TARGET_PER_DOMAIN - current_total
            progress_log.append(f"[{domain_name}] Besoin de {needed_total} questions supplémentaires via HARD.")
            distribution = DISTRIBUTION.get("hard", {})
            progress_log = process_domain_by_difficulty(
                domain_id, domain_name, "hard", distribution,
                provider_name, cert_name, analysis, progress_log, all_domain_names
            )
            current_total = db.count_total_questions(domain_id)
            progress_log.append(f"[{domain_name}] Total après HARD: {current_total}")
        
        # Fallback transverse
        if current_total < TARGET_PER_DOMAIN:
            needed_total = TARGET_PER_DOMAIN - current_total
            progress_log.append(f"[{domain_name}] Total après HARD = {current_total}. Fallback transverse de {needed_total} questions.")
            # Choix aléatoire du practical entre 'no' et 'scenario'
            practical_val = random.choice(['no', 'scenario'])
            # Choix des domaines secondaires même en fallback
            secondaries = pick_secondary_domains(all_domain_names, domain_name)
            progress_log.append(f"[{domain_name} - FALLBACK] Practical: {practical_val}, Secondary domains: {secondaries}")
            # Construction de l'argument Domain
            if secondaries:
                domain_arg = f"main domain :{domain_name}; includes context from domains: {', '.join(secondaries)}"
            else:
                domain_arg = domain_name
            # Détermination du scenario_illustration_type pour fallback scenario
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
                
                db.insert_questions(domain_id, questions_data, practical_val)
                progress_log.append(f"[{domain_name}] {needed_total} questions fallback insérées.")
            except Exception as e:
                progress_log.append(f"[{domain_name}] Erreur lors de la génération fallback: {e}")
        
        domains_processed += 1
        final_total = db.count_total_questions(domain_id)
        progress_log.append(f"[{domain_name}] Domaine traité: total final = {final_total} ({domains_processed}/{total_domains}).")

    progress_log.append(f"Processus terminé: {domains_processed} domaines traités sur {total_domains}.")
    counters = {
        "analysis": log_analysis,
        "domainsProcessed": domains_processed,
        "totalDomains": total_domains,
        "totalQuestions": sum(db.count_total_questions(d[0]) for d in domains)
    }
    return jsonify({"status": "completed", "log": progress_log, "counters": counters})


def process_domain_by_difficulty(domain_id, domain_name, difficulty, distribution,
                                 provider_name, certification_name, analysis, progress_log, all_domain_names):
    """
    Génère et insère les questions pour un domaine donné selon la distribution fournie,
    en injectant aléatoirement des domaines secondaires lorsque pratique != 'no'.
    """
    import json  # on peut le déclarer en haut du fichier aussi

    for qtype, scenarios in distribution.items():
        for scenario_type, target_count in scenarios.items():
            # Détermination des paramètres practical et scenario_illustration_type
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
                f"[{domain_name} - {difficulty.upper()}] {qtype} avec scenario '{scenario_type}' existant: "
                f"{existing_count} (target: {target_count})."
            )
            if existing_count < target_count:
                needed = target_count - existing_count
                progress_log.append(
                    f"[{domain_name} - {difficulty.upper()}] Besoin de {needed} questions pour "
                    f"{qtype} avec scenario '{scenario_type}'."
                )
                # Injection des domaines secondaires si scenario != 'no'
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
                        domain=domain_name,
                        domain_descr=desc,
                        level=difficulty,
                        q_type=qtype,
                        practical=practical_val,
                        scenario_illustration_type=scenario_illu_val,
                        num_questions=needed
                    )
                except Exception as e:
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] Erreur génération pour {qtype} "
                        f"avec scenario '{scenario_type}': {e}"
                    )
                    continue

                # Génération et insertion des diagrammes éventuels
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
                                f"[{domain_name} - {difficulty.upper()}] Erreur diagramme pour {qtype} "
                                f"avec scenario '{scenario_type}': {e}"
                            )
                            question["image"] = f""

                try:
                    db.insert_questions(domain_id, questions_data, scenario_type)
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] {needed} questions insérées pour "
                        f"{qtype} avec scenario '{scenario_type}'."
                    )
                except Exception as e:
                    progress_log.append(
                        f"[{domain_name} - {difficulty.upper()}] Erreur insertion pour {qtype} "
                        f"avec scenario '{scenario_type}': {e}"
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
