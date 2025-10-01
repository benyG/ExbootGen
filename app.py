import os
import random
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Dict, List

try:  # pragma: no cover - optional runtime dependency
    from celery import Celery  # type: ignore
    from celery.exceptions import CeleryError  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without Celery
    class CeleryError(Exception):
        """Fallback CeleryError used when the dependency is unavailable."""

    class _CeleryConfig(SimpleNamespace):
        def update(self, *_, **kwargs):
            self.__dict__.update(kwargs)

    class Celery:  # type: ignore
        def __init__(self, *_, **__):
            # Run tasks eagerly by default when Celery isn't installed so that
            # development environments without the dependency can still execute
            # long running jobs synchronously instead of crashing at runtime.
            self.conf = _CeleryConfig(task_always_eager=True, task_eager_propagates=True)

        def task(self, bind: bool = False, name: str | None = None):
            def decorator(func):
                def apply_async(*, args=None, kwargs=None, task_id=None):
                    if not getattr(self.conf, "task_always_eager", False):
                        raise CeleryError(
                            "Celery n'est pas installé. Veuillez ajouter la dépendance 'celery' pour l'exécution asynchrone."
                        )
                    args = args or ()
                    kwargs = kwargs or {}
                    if bind:
                        request_id = task_id or uuid.uuid4().hex
                        bound_self = SimpleNamespace(request=SimpleNamespace(id=request_id))
                        func(bound_self, *args, **kwargs)
                        return SimpleNamespace(id=request_id, get=lambda: None)
                    func(*args, **kwargs)
                    return SimpleNamespace(id=task_id, get=lambda: None)

                func.apply_async = lambda *, args=None, kwargs=None, task_id=None: apply_async(
                    args=args, kwargs=kwargs, task_id=task_id
                )
                return func

            return decorator

        def conf_update(self, **kwargs):
            self.conf.update(**kwargs)


from flask import Flask, jsonify, render_template, request

try:  # pragma: no cover - optional runtime dependency
    from kombu.exceptions import OperationalError  # type: ignore
except (ModuleNotFoundError, ImportError):  # pragma: no cover - fallback when kombu is absent
    class OperationalError(Exception):
        """Fallback OperationalError used when kombu is unavailable."""

from config import API_REQUEST_DELAY, DISTRIBUTION, GUI_PASSWORD
import db
from eraser_api import render_diagram
from jobs import JobContext, JobStoreError, create_job_store, initialise_job
from openai_api import analyze_certif, correct_questions, generate_questions

from dom import dom_bp
from move import move_bp
from reloc import reloc_bp
from pdf_importer import pdf_bp
from quest import quest_bp

# Instanciation de l'application Flask
app = Flask(__name__, template_folder="templates")


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default)
    return value.lower() in {"1", "true", "yes", "on"}


def make_celery() -> Celery:
    """Configure the Celery worker used for heavy workloads."""

    eager = _env_flag("CELERY_TASK_ALWAYS_EAGER")

    broker_url = os.getenv("CELERY_BROKER_URL")
    result_backend = os.getenv("CELERY_RESULT_BACKEND")
    pool_limit = int(os.getenv("CELERY_POOL_LIMIT", "1"))

    if eager:
        broker_url = broker_url or "memory://"
        result_backend = result_backend or "cache+memory://"
    else:
        default_redis = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        broker_url = broker_url or default_redis
        result_backend = result_backend or broker_url

    celery_app = Celery("exbootgen", broker=broker_url, backend=result_backend)

    broker_transport_options: Dict[str, object] = {}
    result_transport_options: Dict[str, object] = {}
    redis_max_connections_setting = os.getenv("CELERY_REDIS_MAX_CONNECTIONS")
    redis_max_connections: int | None = None
    if redis_max_connections_setting:
        redis_max_connections = max(int(redis_max_connections_setting), 0)

    if broker_url and broker_url.startswith("redis://"):
        broker_max_connections = int(os.getenv("CELERY_MAX_CONNECTIONS", str(pool_limit)))
        broker_transport_options["max_connections"] = broker_max_connections
        if redis_max_connections is None:
            redis_max_connections = max(broker_max_connections, 0)
        else:
            redis_max_connections = max(redis_max_connections, broker_max_connections)

    if result_backend and result_backend.startswith("redis://"):
        result_max_connections = int(os.getenv("CELERY_RESULT_MAX_CONNECTIONS", str(pool_limit)))
        result_transport_options["max_connections"] = result_max_connections
        if redis_max_connections is None:
            redis_max_connections = max(result_max_connections, 0)
        else:
            redis_max_connections = max(redis_max_connections, result_max_connections)
    celery_app.conf.update(
        task_track_started=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        broker_pool_limit=pool_limit,
        broker_transport_options=broker_transport_options,
        result_backend_transport_options=result_transport_options,
    )

    if redis_max_connections is not None and redis_max_connections > 0:
        celery_app.conf.redis_max_connections = redis_max_connections

    if eager:
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

    return celery_app


celery_app = make_celery()
job_store = create_job_store()

QUEUE_EXCEPTIONS = (CeleryError, OperationalError, ConnectionError, OSError)

# Enregistrement des blueprints
app.register_blueprint(dom_bp, url_prefix="/modules")
app.register_blueprint(move_bp, url_prefix="/move")
app.register_blueprint(reloc_bp, url_prefix="/reloc")
app.register_blueprint(pdf_bp, url_prefix="/pdf")
app.register_blueprint(quest_bp, url_prefix="/quest")

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


@app.route("/reports")
def reports():
    certification_id = 23

    futures = {
        "domain_counts": db.execute_async(db.get_domain_question_counts_for_cert, certification_id),
        "missing_correct": db.execute_async(db.get_certifications_missing_correct_answers),
        "missing_correct_domains": db.execute_async(
            db.get_domains_missing_correct_answers, certification_id
        ),
        "missing_answers": db.execute_async(db.get_domains_missing_answers_by_type),
        "missing_domains": db.execute_async(db.get_certifications_without_domains),
    }

    domain_counts = futures["domain_counts"].result()
    missing_correct = futures["missing_correct"].result()
    missing_correct_domains = futures["missing_correct_domains"].result()
    missing_answers = futures["missing_answers"].result()
    missing_domains = futures["missing_domains"].result()

    return render_template(
        "reports.html",
        certification_id=certification_id,
        domain_counts=domain_counts,
        missing_correct=missing_correct,
        missing_correct_domains=missing_correct_domains,
        missing_answers=missing_answers,
        missing_domains=missing_domains,
    )


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


@app.route("/fix", methods=["GET"])
def fix_index():
    providers = db.get_providers()

    selected_provider_id = request.args.get("provider_id", type=int)
    selected_cert_id = request.args.get("cert_id", type=int)
    selected_action = request.args.get("action", default="assign") or "assign"

    if selected_provider_id is None and providers:
        selected_provider_id = providers[0][0]

    initial_progress = None
    if selected_cert_id is not None:
        initial_progress = _compute_fix_progress(selected_cert_id, selected_action)

    return render_template(
        "fix.html",
        providers=providers,
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


def run_fix(context: JobContext, provider_id: int, cert_id: int, action: str) -> None:
    """Correct or complete questions for a certification asynchronously."""

    providers = {pid: name for pid, name in db.get_providers()}
    provider_name = providers.get(provider_id)
    if not provider_name:
        message = f"Provider with id {provider_id} not found."
        context.log(message)
        raise ValueError(message)

    certifications = {cid: name for cid, name in db.get_certifications_by_provider(provider_id)}
    cert_name = certifications.get(cert_id)
    if not cert_name:
        message = f"Certification with id {cert_id} not found."
        context.log(message)
        raise ValueError(message)

    action = action or "assign"
    context.log(
        f"Starting fix workflow for provider '{provider_name}', certification '{cert_name}' (action={action})."
    )

    progress = _compute_fix_progress(cert_id, action)
    total = progress.get("total", 0) or 0
    corrected = progress.get("corrected", 0) or 0
    remaining = progress.get("remaining", 0) or 0

    context.update_counters(
        total=total,
        corrected=corrected,
        remaining=remaining,
        processed=0,
        action=action,
    )

    if remaining <= 0:
        context.log("Nothing to process for the selected certification.")
        return

    if action == "assign":
        questions = db.get_questions_without_correct_answer(cert_id)

        def _apply_result(result):
            answer_ids = result.get("answer_ids", [])
            db.mark_answers_correct(result.get("question_id"), answer_ids)
            return bool(answer_ids)

        task_label = "Attribuer les réponses correctes"
    elif action == "drag":
        questions = db.get_questions_without_answers_by_nature(
            cert_id, db.nature_mapping['drag-n-drop']
        )

        def _apply_result(result):
            answers = result.get("answers", [])
            db.add_answers(result.get("question_id"), answers)
            return bool(answers)

        task_label = "Compléter les questions drag-n-drop"
    else:
        questions = db.get_questions_without_answers_by_nature(
            cert_id, db.nature_mapping['matching']
        )

        def _apply_result(result):
            answers = result.get("answers", [])
            db.add_answers(result.get("question_id"), answers)
            return bool(answers)

        task_label = "Compléter les questions matching"

    total_to_process = len(questions)
    context.log(f"{total_to_process} question(s) à traiter.")

    processed = 0
    for index, question in enumerate(questions, start=1):
        context.wait_if_paused()
        qid = question.get("id")
        context.log(f"[{index}/{total_to_process}] Traitement de la question {qid}.")
        try:
            responses = correct_questions(provider_name, cert_name, [question], action)
        except Exception as exc:  # pragma: no cover - propagated to Celery
            context.log(f"Erreur lors de l'appel OpenAI pour la question {qid}: {exc}")
            continue

        if not responses:
            context.log(f"Aucune réponse obtenue pour la question {qid}.")
            continue

        result = responses[0] or {}
        try:
            updated = _apply_result(result)
        except Exception as exc:  # pragma: no cover - DB errors surfaced in logs
            context.log(f"Erreur base de données pour la question {qid}: {exc}")
            continue

        processed += 1
        if updated:
            corrected = min(total, corrected + 1) if total else corrected + 1
            remaining = max(remaining - 1, 0)
            context.log(f"Question {qid} mise à jour.")
        else:
            context.log(f"Aucune modification enregistrée pour la question {qid}.")
        context.update_counters(
            total=total,
            corrected=corrected,
            remaining=remaining,
            processed=processed,
        )
        time.sleep(API_REQUEST_DELAY)

    context.log(f"{task_label} terminé : {processed} question(s) traitée(s).")


@celery_app.task(bind=True, name="fix.run")
def run_fix_job(self, provider_id: int, cert_id: int, action: str) -> None:
    """Celery task wrapper for :func:`run_fix`."""

    job_id = self.request.id
    context = JobContext(job_store, job_id)

    try:
        job_store.set_status(job_id, "running")
    except JobStoreError:
        initialise_job(
            job_store,
            job_id=job_id,
            description="fix-certification",
            metadata={
                "provider_id": provider_id,
                "cert_id": cert_id,
                "action": action,
            },
        )
        job_store.set_status(job_id, "running")

    try:
        run_fix(context, provider_id, cert_id, action)
    except Exception as exc:  # pragma: no cover - propagated to Celery
        context.set_status("failed", error=str(exc))
        raise
    else:
        context.set_status("completed")


@app.route("/fix/process", methods=["POST"])
def fix_process():
    provider_id = request.form.get("provider_id", type=int)
    cert_id = request.form.get("cert_id", type=int)
    action = request.form.get("action", type=str) or "assign"

    if provider_id is None or cert_id is None:
        return jsonify({"error": "provider_id and cert_id are required"}), 400

    job_id = initialise_job(
        job_store,
        job_id=uuid.uuid4().hex,
        description="fix-certification",
        metadata={"provider_id": provider_id, "cert_id": cert_id, "action": action},
    )

    try:
        run_fix_job.apply_async(args=(provider_id, cert_id, action), task_id=job_id)
    except QUEUE_EXCEPTIONS as exc:  # pragma: no cover - defensive, surfaced to client
        app.logger.exception(
            "Unable to enqueue fix job: provider_id=%s cert_id=%s; falling back to inline execution",
            provider_id,
            cert_id,
        )

        def _run_inline() -> None:
            context = JobContext(job_store, job_id)
            try:
                job_store.set_status(job_id, "running")
            except JobStoreError:
                initialise_job(
                    job_store,
                    job_id=job_id,
                    description="fix-certification",
                    metadata={
                        "provider_id": provider_id,
                        "cert_id": cert_id,
                        "action": action,
                    },
                )
                job_store.set_status(job_id, "running")

            try:
                run_fix(context, provider_id, cert_id, action)
            except Exception as inline_exc:  # pragma: no cover - surfaced via job status
                app.logger.exception(
                    "Inline fix job failed: provider_id=%s cert_id=%s", provider_id, cert_id
                )
                context.set_status("failed", error=str(inline_exc))
            else:
                context.set_status("completed")

        threading.Thread(
            target=_run_inline,
            name=f"fix-inline-{job_id}",
            daemon=True,
        ).start()

        return jsonify({"status": "queued", "job_id": job_id, "mode": "inline"})
    except Exception as exc:
        if getattr(celery_app.conf, "task_always_eager", False):
            app.logger.exception(
                "Fix job failed during eager execution: provider_id=%s cert_id=%s",
                provider_id,
                cert_id,
            )
            status = job_store.get_status(job_id) or {}
            payload = {"status": status.get("status", "failed"), "job_id": job_id}
            error = status.get("error") or str(exc)
            if error:
                payload["error"] = error
            return jsonify(payload)
        raise

    return jsonify({"status": "queued", "job_id": job_id})


@app.route("/fix/status/<job_id>", methods=["GET"])
def fix_status(job_id):
    data = job_store.get_status(job_id)
    if data is None:
        return jsonify({"error": "unknown job id"}), 404
    return jsonify(data)


def run_population(context: JobContext, provider_id: int, cert_id: int) -> None:
    """Execute the population process for a certification."""

    providers = {pid: name for pid, name in db.get_providers()}
    provider_name = providers.get(provider_id)
    if not provider_name:
        message = f"Provider with id {provider_id} not found."
        context.log(message)
        raise ValueError(message)

    certifications = {cid: name for cid, name in db.get_certifications_by_provider(provider_id)}
    cert_name = certifications.get(cert_id)
    if not cert_name:
        message = f"Certification with id {cert_id} not found."
        context.log(message)
        raise ValueError(message)

    context.update_counters(
        analysis="",
        domainsProcessed=0,
        totalDomains=0,
        totalQuestions=0,
    )

    # Certification analysis
    try:
        analysis_result = analyze_certif(provider_name, cert_name)
        analysis = {k: str(v).strip('"') for d in analysis_result for k, v in d.items()}
        log_analysis = f"Certification analysis: {analysis}"
    except Exception as exc:
        analysis = {}
        log_analysis = f"Certification analysis unavailable: {exc}"

    context.log(log_analysis)
    context.update_counters(analysis=log_analysis)

    # Récupérer tous les domaines et leurs noms
    domains = db.get_domains_by_certification(cert_id)
    all_domain_names = [name for (_, name) in domains]
    total_domains = len(domains)
    context.update_counters(totalDomains=total_domains)

    domain_descriptions = {
        item["id"]: item["descr"]
        for item in db.get_domains_description_by_certif(cert_id)
    }

    domains_processed = 0

    for domain_id, domain_name in domains:
        context.wait_if_paused()
        context.log(f"[{domain_name}] Domain processing started.")
        current_total = db.count_total_questions(domain_id)
        context.log(f"[{domain_name}] Initial question count: {current_total}")

        if current_total >= TARGET_PER_DOMAIN:
            domains_processed += 1
            context.log(
                f"[{domain_name}] Domain already complete (>= {TARGET_PER_DOMAIN} questions)."
            )
            context.update_counters(
                domainsProcessed=domains_processed,
                totalQuestions=sum(db.count_total_questions(d[0]) for d in domains),
            )
            continue

        context.log(
            f"[{domain_name}] Needs {TARGET_PER_DOMAIN - current_total} additional questions to reach {TARGET_PER_DOMAIN}."
        )

        # --- Élaboration par niveau de difficulté ---
        # Traitement EASY si le domaine est vide
        if current_total == 0:
            context.wait_if_paused()
            context.log(f"[{domain_name}] Empty domain, using EASY distribution.")
            distribution = DISTRIBUTION.get("easy", {})
            process_domain_by_difficulty(
                context,
                domain_id,
                domain_name,
                "easy",
                distribution,
                provider_name,
                cert_name,
                analysis,
                all_domain_names,
                domain_descriptions,
            )
            current_total = db.count_total_questions(domain_id)
            context.log(f"[{domain_name}] Total after EASY: {current_total}")

        # Traitement MEDIUM si nécessaire
        if current_total < TARGET_PER_DOMAIN:
            context.wait_if_paused()
            needed_total = TARGET_PER_DOMAIN - current_total
            context.log(
                f"[{domain_name}] Needs {needed_total} additional questions via MEDIUM."
            )
            distribution = DISTRIBUTION.get("medium", {})
            process_domain_by_difficulty(
                context,
                domain_id,
                domain_name,
                "medium",
                distribution,
                provider_name,
                cert_name,
                analysis,
                all_domain_names,
                domain_descriptions,
            )
            current_total = db.count_total_questions(domain_id)
            context.log(f"[{domain_name}] Total after MEDIUM: {current_total}")

        # Traitement HARD si toujours nécessaire
        if current_total < TARGET_PER_DOMAIN:
            context.wait_if_paused()
            needed_total = TARGET_PER_DOMAIN - current_total
            context.log(
                f"[{domain_name}] Needs {needed_total} additional questions via HARD."
            )
            distribution = DISTRIBUTION.get("hard", {})
            process_domain_by_difficulty(
                context,
                domain_id,
                domain_name,
                "hard",
                distribution,
                provider_name,
                cert_name,
                analysis,
                all_domain_names,
                domain_descriptions,
            )
            current_total = db.count_total_questions(domain_id)
            context.log(f"[{domain_name}] Total after HARD: {current_total}")

        # Fallback transverse
        if current_total < TARGET_PER_DOMAIN:
            context.wait_if_paused()
            needed_total = TARGET_PER_DOMAIN - current_total
            context.log(
                f"[{domain_name}] After HARD = {current_total}. Fallback of {needed_total} questions."
            )
            practical_val = random.choice(['no', 'scenario'])
            secondaries = pick_secondary_domains(all_domain_names, domain_name)
            context.log(
                f"[{domain_name} - FALLBACK] Practical: {practical_val}, Secondary domains: {secondaries}"
            )
            if secondaries:
                domain_arg = (
                    f"main domain :{domain_name}; includes context from domains: {', '.join(secondaries)}"
                )
            else:
                domain_arg = domain_name
            if practical_val == 'scenario':
                candidates = [k for k, v in analysis.items() if v == '1']
                scenario_illu_val = random.choice(candidates) if candidates else 'none'
            else:
                scenario_illu_val = 'none'
            try:
                desc = domain_descriptions.get(domain_id, "")
                questions_data = generate_questions(
                    provider_name=provider_name,
                    certification=cert_name,
                    domain=domain_arg,
                    domain_descr=desc,
                    level="medium",
                    q_type="qcm",
                    practical=practical_val,
                    scenario_illustration_type=scenario_illu_val,
                    num_questions=needed_total,
                )
                time.sleep(API_REQUEST_DELAY)
                db.insert_questions(domain_id, questions_data, practical_val)
                context.log(
                    f"[{domain_name}] {needed_total} fallback questions inserted."
                )
            except Exception as exc:
                context.log(
                    f"[{domain_name}] Error during fallback generation: {exc}"
                )

        domains_processed += 1
        final_total = db.count_total_questions(domain_id)
        context.log(
            f"[{domain_name}] Domain completed: final total = {final_total} ({domains_processed}/{total_domains})."
        )
        context.update_counters(
            domainsProcessed=domains_processed,
            totalQuestions=sum(db.count_total_questions(d[0]) for d in domains),
        )

    context.log(
        f"Process finished: {domains_processed} domains processed out of {total_domains}."
    )


@celery_app.task(bind=True, name="population.run")
def run_population_job(self, provider_id: int, cert_id: int) -> None:
    """Celery task wrapper for :func:`run_population`."""

    job_id = self.request.id
    metadata = {"provider_id": provider_id, "cert_id": cert_id}
    context = JobContext(job_store, job_id)

    _ensure_job_marked_running(job_id, metadata)

    try:
        run_population(context, provider_id, cert_id)
    except Exception as exc:  # pragma: no cover - propagated to Celery
        context.set_status("failed", error=str(exc))
        raise
    else:
        context.set_status("completed")


def _run_population_thread(job_id: str, provider_id: int, cert_id: int) -> threading.Thread:
    """Run the population workflow in a local background thread.

    When the Celery broker is unavailable (for instance when Redis refuses
    new clients because the configured limit is reached) we still want to
    allow the user to launch the populate process.  This helper mimics the
    behaviour of :func:`run_population_job` but executes it within a daemon
    thread of the web process so that the HTTP request can return immediately.
    """

    metadata = {"provider_id": provider_id, "cert_id": cert_id}

    def _target() -> None:
        context = JobContext(job_store, job_id)
        _ensure_job_marked_running(job_id, metadata)

        try:
            run_population(context, provider_id, cert_id)
        except Exception as exc:  # pragma: no cover - logged for diagnostics
            app.logger.exception(
                "Population job failed during local execution: job_id=%s", job_id
            )
            context.set_status("failed", error=str(exc))
        else:
            context.set_status("completed")

    thread = threading.Thread(
        target=_target, name=f"populate-{job_id}", daemon=True
    )
    thread.start()
    return thread


def _ensure_job_marked_running(job_id: str, metadata: Dict[str, int]) -> None:
    try:
        job_store.set_status(job_id, "running")
        return
    except JobStoreError as exc:
        try:
            initialise_job(
                job_store,
                job_id=job_id,
                description="populate-certification",
                metadata=metadata,
            )
            job_store.set_status(job_id, "running")
            return
        except JobStoreError as retry_exc:
            app.logger.warning(
                "Job %s: impossible de persister l'état 'running' dans le magasin de jobs (%s).",
                job_id,
                retry_exc,
            )
            app.logger.debug(
                "Job %s: échec initial lors du passage en 'running': %s",
                job_id,
                exc,
            )


@app.route("/populate/process", methods=["POST"])
def populate_process():
    provider_id = int(request.form.get("provider_id"))
    cert_id = int(request.form.get("cert_id"))

    job_id = initialise_job(
        job_store,
        job_id=uuid.uuid4().hex,
        description="populate-certification",
        metadata={"provider_id": provider_id, "cert_id": cert_id},
    )

    try:
        run_population_job.apply_async(args=(provider_id, cert_id), task_id=job_id)
    except QUEUE_EXCEPTIONS as exc:  # pragma: no cover - defensive, surfaced to client
        app.logger.exception(
            "Unable to enqueue population job: provider_id=%s cert_id=%s", provider_id, cert_id
        )
        try:
            _run_population_thread(job_id, provider_id, cert_id)
        except Exception:  # pragma: no cover - fallback may still fail
            job_store.set_status(job_id, "failed", error=str(exc))
            return (
                jsonify(
                    {
                        "error": "Impossible de démarrer le traitement : "
                        "la file d'attente des tâches est indisponible."
                    }
                ),
                500,
            )
        app.logger.warning(
            "Population job %s running in local thread because the task queue is unavailable.",
            job_id,
        )
        return jsonify({"status": "queued", "job_id": job_id, "mode": "local"})
    except Exception as exc:
        if getattr(celery_app.conf, "task_always_eager", False):
            app.logger.exception(
                "Population job failed during eager execution: provider_id=%s cert_id=%s",
                provider_id,
                cert_id,
            )
            status = job_store.get_status(job_id) or {}
            payload = {"status": status.get("status", "failed"), "job_id": job_id}
            error = status.get("error") or str(exc)
            if error:
                payload["error"] = error
            return jsonify(payload)
        raise

    return jsonify({"status": "queued", "job_id": job_id})


@app.route("/populate/status/<job_id>", methods=["GET"])
def populate_status(job_id):
    data = job_store.get_status(job_id)
    if data is None:
        return jsonify({"error": "unknown job id"}), 404
    return jsonify(data)


def process_domain_by_difficulty(
    context: JobContext,
    domain_id: int,
    domain_name: str,
    difficulty: str,
    distribution: Dict[str, Dict[str, int]],
    provider_name: str,
    cert_name: str,
    analysis: Dict[str, str],
    all_domain_names: List[str],
    domain_descriptions: Dict[int, str],
) -> None:
    """Generate and insert questions for a domain according to the distribution."""

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
            context.log(
                f"[{domain_name} - {difficulty.upper()}] {qtype} with scenario '{scenario_type}' existing: "
                f"{existing_count} (target: {target_count})."
            )
            if existing_count < target_count:
                context.wait_if_paused()
                needed = target_count - existing_count
                context.log(
                    f"[{domain_name} - {difficulty.upper()}] Needs {needed} questions for "
                    f"{qtype} with scenario '{scenario_type}'."
                )
                if practical_val != 'no':
                    secondaries = pick_secondary_domains(all_domain_names, domain_name)
                    context.log(
                        f"[{domain_name} - {difficulty.upper()}] Secondary domains: {secondaries}"
                    )
                    domain_arg = (
                        f"main domain :{domain_name}; includes context from domains: {', '.join(secondaries)}"
                        if secondaries
                        else domain_name
                    )
                else:
                    domain_arg = domain_name

                try:
                    desc = domain_descriptions.get(domain_id, "")
                    questions_data = generate_questions(
                        provider_name=provider_name,
                        certification=cert_name,
                        domain=domain_arg,
                        domain_descr=desc,
                        level=difficulty,
                        q_type=qtype,
                        practical=practical_val,
                        scenario_illustration_type=scenario_illu_val,
                        num_questions=needed,
                    )
                    time.sleep(API_REQUEST_DELAY)
                except Exception as exc:
                    context.log(
                        f"[{domain_name} - {difficulty.upper()}] Generation error for {qtype} "
                        f"with scenario '{scenario_type}': {exc}"
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
                        except Exception as exc:  # pragma: no cover - log only
                            context.log(
                                f"[{domain_name} - {difficulty.upper()}] Diagram error for {qtype} "
                                f"with scenario '{scenario_type}' (desc: {diagram_description}, type: {diag_type}): {exc}"
                            )
                            question["image"] = ""

                try:
                    db.insert_questions(domain_id, questions_data, scenario_type)
                    context.log(
                        f"[{domain_name} - {difficulty.upper()}] {needed} questions inserted for "
                        f"{qtype} with scenario '{scenario_type}'."
                    )
                except Exception as exc:
                    context.log(
                        f"[{domain_name} - {difficulty.upper()}] Insert error for {qtype} "
                        f"with scenario '{scenario_type}': {exc}"
                    )

@app.route("/populate/pause/<job_id>", methods=["POST"])
def pause_populate(job_id):
    if job_store.pause(job_id):
        return jsonify({"status": "paused"})
    return jsonify({"error": "unknown job id"}), 404


@app.route("/populate/resume/<job_id>", methods=["POST"])
def resume_populate(job_id):
    if job_store.resume(job_id):
        return jsonify({"status": "resumed"})
    return jsonify({"error": "unknown job id"}), 404


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
