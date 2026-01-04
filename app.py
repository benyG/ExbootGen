import os
import random
import json
import threading
import time
import uuid
from textwrap import dedent
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple
from datetime import datetime, date, time as dt_time

try:  # pragma: no cover - optional runtime dependency
    from celery import Celery  # type: ignore
    from celery.exceptions import CeleryError  # type: ignore
    from celery.schedules import crontab  # type: ignore
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

    def crontab(*_, **__):  # type: ignore
        return 60


from flask import Flask, jsonify, redirect, render_template, request, session, url_for

try:  # pragma: no cover - optional runtime dependency
    from kombu.exceptions import OperationalError  # type: ignore
except (ModuleNotFoundError, ImportError):  # pragma: no cover - fallback when kombu is absent
    class OperationalError(Exception):
        """Fallback OperationalError used when kombu is unavailable."""

try:  # pragma: no cover - optional runtime dependency
    from redis.exceptions import RedisError  # type: ignore
except (ModuleNotFoundError, ImportError):  # pragma: no cover - fallback when redis is absent
    class RedisError(Exception):
        """Fallback RedisError used when the redis dependency is unavailable."""

from config import (
    API_REQUEST_DELAY,
    DISTRIBUTION,
    GUI_PASSWORD,
    _distribution_total,
)
import db
from eraser_api import render_diagram
from jobs import (
    JobContext,
    JobStoreError,
    cache_job_snapshot,
    create_job_store,
    get_cached_status,
    initialise_job,
    mark_job_paused,
    mark_job_resumed,
    set_cached_status,
)
from openai_api import analyze_certif, correct_questions, generate_questions

from dom import dom_bp
from module_blueprints import module_blueprints_bp
from move import move_bp
from reloc import reloc_bp
from pdf_importer import pdf_bp
from quest import quest_bp
from edit_questions import edit_question_bp
from articles import articles_bp, render_x_callback, run_scheduled_publication, SocialPostResult
from handsonlab import hol_bp

# Instanciation de l'application Flask
BASE_DIR = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
# Minimal secret key required for session-based authentication protecting the UI
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "exboot-secret-key")


def _ensure_login_template() -> None:
    """Guarantee that a login template exists even in truncated deployments."""

    templates_dir = BASE_DIR / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    login_template = templates_dir / "login.html"
    if login_template.exists():
        return

    login_template.write_text(
        dedent(
            """\
            <!DOCTYPE html>
            <html lang="fr">
            <head>
              <meta charset="UTF-8">
              <meta name="viewport" content="width=device-width, initial-scale=1.0">
              <title>Connexion · ExbootGen</title>
            </head>
            <body>
              <h1>Connexion</h1>
              <p>Page de connexion générée automatiquement : ajoutez le fichier templates/login.html pour personnaliser l'interface.</p>
              <form method="post">
                <label>Nom d'utilisateur <input name="username" type="text" required></label><br>
                <label>Mot de passe <input name="password" type="password" required></label><br>
                <button type="submit">Se connecter</button>
                {% if error %}
                  <div style="color: red;">{{ error }}</div>
                {% endif %}
              </form>
            </body>
            </html>
            """
        ),
        encoding="utf-8",
    )
    app.logger.warning(
        "Le fichier templates/login.html était manquant : un modèle par défaut a été généré dans %s",
        login_template,
    )


_ensure_login_template()


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default)
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: Optional[int] = None) -> int:
    """Return an integer value from the environment bounded between ``minimum`` and ``maximum``."""

    raw_value = os.getenv(name)
    if raw_value is None:
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} doit être un entier") from exc
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _env_float(name: str) -> float | None:
    """Return a floating point value from the environment when defined."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un nombre") from exc


def _redis_socket_options_from_env() -> Dict[str, object]:
    """Build a mapping of Redis socket options sourced from environment variables."""

    options: Dict[str, object] = {}

    keepalive_env = os.getenv("CELERY_REDIS_SOCKET_KEEPALIVE")
    if keepalive_env is not None:
        options["socket_keepalive"] = _is_truthy(keepalive_env)

    socket_timeout = _env_float("CELERY_REDIS_SOCKET_TIMEOUT")
    if socket_timeout is not None:
        options["socket_timeout"] = socket_timeout

    socket_connect_timeout = _env_float("CELERY_REDIS_SOCKET_CONNECT_TIMEOUT")
    if socket_connect_timeout is not None:
        options["socket_connect_timeout"] = socket_connect_timeout

    health_check_env = os.getenv("CELERY_REDIS_HEALTH_CHECK_INTERVAL")
    if health_check_env is not None:
        options["health_check_interval"] = _env_int(
            "CELERY_REDIS_HEALTH_CHECK_INTERVAL",
            0,
            minimum=0,
        )

    return options


def _redis_pool_metrics(client: object) -> Dict[str, object]:
    """Return basic connection pool metrics for a redis-py client."""

    pool = getattr(client, "connection_pool", None)
    if pool is None:
        return {}

    in_use = getattr(pool, "_in_use_connections", None)
    created = getattr(pool, "_created_connections", None)
    available = len(getattr(pool, "_available_connections", []) or [])
    metrics: Dict[str, object] = {
        "max_connections": getattr(pool, "max_connections", None),
        "available": available,
    }
    if in_use is not None:
        metrics["in_use"] = in_use
    if created is not None:
        metrics["created"] = created
    return metrics


def _default_parallelism(maximum: int = 8) -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, maximum))


def make_celery() -> Celery:
    """Configure the Celery worker used for heavy workloads."""

    eager = _env_flag("CELERY_TASK_ALWAYS_EAGER")

    broker_url = os.getenv("CELERY_BROKER_URL")
    result_backend = os.getenv("CELERY_RESULT_BACKEND")
    pool_limit_env = os.getenv("CELERY_POOL_LIMIT")
    if pool_limit_env is not None:
        try:
            pool_limit = max(int(pool_limit_env), 1)
        except ValueError:
            raise ValueError("CELERY_POOL_LIMIT doit être un entier positif") from None
    else:
        pool_limit = _default_parallelism(maximum=8)

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

    redis_socket_options = _redis_socket_options_from_env()

    if broker_url and broker_url.startswith("redis://"):
        broker_max_connections = int(os.getenv("CELERY_MAX_CONNECTIONS", str(pool_limit)))
        broker_transport_options["max_connections"] = broker_max_connections
        broker_transport_options.update(redis_socket_options)
        if redis_max_connections is None:
            redis_max_connections = max(broker_max_connections, 0)
        else:
            redis_max_connections = max(redis_max_connections, broker_max_connections)

    if result_backend and result_backend.startswith("redis://"):
        result_max_connections = int(os.getenv("CELERY_RESULT_MAX_CONNECTIONS", str(pool_limit)))
        result_transport_options["max_connections"] = result_max_connections
        result_transport_options.update(redis_socket_options)
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
    celery_app.conf.beat_schedule = {
        "dispatch-due-schedules-every-minute": {
            "task": "schedule.dispatch_due",
            "schedule": crontab(),  # every minute
        },
        "redis-healthcheck-every-minute": {
            "task": "tasks.redis_healthcheck",
            "schedule": 60,
        },
    }

    if redis_max_connections is not None and redis_max_connections > 0:
        celery_app.conf.redis_max_connections = redis_max_connections

    if eager:
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

    return celery_app


celery_app = make_celery()
_INITIAL_TASK_ALWAYS_EAGER = bool(getattr(celery_app.conf, "task_always_eager", False))
_INITIAL_TASK_EAGER_PROPAGATES = bool(
    getattr(celery_app.conf, "task_eager_propagates", False)
)

_TASK_QUEUE_LOCK = Lock()
_TASK_QUEUE_DISABLED = False
_REDIS_HEALTH_FAILURES = 0
_REDIS_HEALTH_LOCK = Lock()


def _is_task_queue_disabled() -> bool:
    with _TASK_QUEUE_LOCK:
        return _TASK_QUEUE_DISABLED


def _disable_task_queue(reason: str | Exception) -> None:
    """Disable the distributed task queue after a fatal connection error."""

    global _TASK_QUEUE_DISABLED
    with _TASK_QUEUE_LOCK:
        if _TASK_QUEUE_DISABLED:
            return
        _TASK_QUEUE_DISABLED = True

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    app.logger.warning(
        "Task queue disabled after failure (%s). Background jobs will now run locally until the application is restarted.",
        reason,
    )


def _record_redis_health_failure(reason: str) -> int:
    """Track consecutive Redis healthcheck failures."""

    global _REDIS_HEALTH_FAILURES
    with _REDIS_HEALTH_LOCK:
        _REDIS_HEALTH_FAILURES += 1
        return _REDIS_HEALTH_FAILURES


def _reset_redis_health_failures() -> None:
    global _REDIS_HEALTH_FAILURES
    with _REDIS_HEALTH_LOCK:
        _REDIS_HEALTH_FAILURES = 0


def _reset_task_queue_state_for_testing() -> None:  # pragma: no cover - test helper
    """Re-enable the task queue; used exclusively by the unit test suite."""

    global _TASK_QUEUE_DISABLED
    with _TASK_QUEUE_LOCK:
        _TASK_QUEUE_DISABLED = False

    celery_app.conf.task_always_eager = _INITIAL_TASK_ALWAYS_EAGER
    celery_app.conf.task_eager_propagates = _INITIAL_TASK_EAGER_PROPAGATES


job_store = create_job_store()
_schedule_reports_lock = Lock()
_schedule_reports: Dict[str, Dict[str, object]] = {}
_schedule_entry_jobs: Dict[str, str] = {}

QUEUE_EXCEPTIONS = (CeleryError, OperationalError, ConnectionError, OSError, RedisError)

# Enregistrement des blueprints
app.register_blueprint(dom_bp, url_prefix="/modules")
app.register_blueprint(module_blueprints_bp, url_prefix="/blueprints")
app.register_blueprint(move_bp, url_prefix="/move")
app.register_blueprint(reloc_bp, url_prefix="/reloc")
app.register_blueprint(pdf_bp, url_prefix="/pdf")
app.register_blueprint(quest_bp, url_prefix="/quest")
app.register_blueprint(edit_question_bp, url_prefix="/edit-question")
app.register_blueprint(articles_bp, url_prefix="/articles")
app.register_blueprint(hol_bp)


@app.route("/x/callback")
def x_callback() -> str:
    """Expose the X OAuth 2.0 redirect endpoint at the application root."""

    return render_x_callback()

# Définition de l'ordre des niveaux de difficulté
DIFFICULTY_LEVELS = ["easy", "medium", "hard"]


def _is_truthy(value: Optional[object]) -> bool:
    """Interpret common truthy strings and booleans.

    Returns ``True`` for values such as ``"true"``, ``"1"``, ``"on"`` or the
    boolean ``True``.
    """

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _normalise_distribution(
    raw_distribution: Optional[object],
) -> Optional[Dict[str, Dict[str, Dict[str, int]]]]:
    """Convert an arbitrary object into a normalised distribution mapping.

    Returns ``None`` when the payload cannot be interpreted, allowing callers to
    fall back to the default configuration defined in :mod:`config`.
    """

    if raw_distribution is None:
        return None

    if isinstance(raw_distribution, str):
        try:
            raw_distribution = json.loads(raw_distribution)
        except ValueError:
            app.logger.warning(
                "Distribution fournie invalide : impossible de parser le JSON, utilisation de la configuration par défaut.",
            )
            return None

    if not isinstance(raw_distribution, dict):
        return None

    cleaned: Dict[str, Dict[str, Dict[str, int]]] = {}
    for difficulty, question_types in raw_distribution.items():
        if not isinstance(question_types, dict):
            continue
        for question_type, scenarios in question_types.items():
            if not isinstance(scenarios, dict):
                continue
            for scenario, value in scenarios.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    count = 0
                cleaned.setdefault(difficulty, {}).setdefault(question_type, {})[
                    scenario
                ] = max(count, 0)

    return cleaned or None


class DomainProgress:
    """Cache question counts for a domain during population jobs."""

    def __init__(self, domain_id: int) -> None:
        self.domain_id = domain_id
        total, categories = db.get_domain_question_snapshot(domain_id)
        self._total = total
        self._categories = categories

    def total_questions(self) -> int:
        return self._total

    def category_total(self, difficulty: str, qtype: str, scenario: str) -> int:
        return self._categories.get((difficulty, qtype, scenario), 0)

    def record_insertion(self, difficulty: str, qtype: str, scenario: str, imported: int) -> None:
        if imported <= 0:
            return
        key = (difficulty, qtype, scenario)
        self._categories[key] = self._categories.get(key, 0) + imported
        self._total += imported


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


@app.route("/schedule")
def schedule():
    """Display the planning calendar for social posts and articles."""

    return render_template("schedule.html")


def _normalise_schedule_status(status: str | None) -> str:
    if not status:
        return "queued"
    status = str(status).lower()
    if status in {"success", "succeeded", "completed"}:
        return "succeeded"
    if status in {"failed", "error"}:
        return "failed"
    if status in {"running", "in_progress"}:
        return "running"
    return status


def _summarise_schedule_entry(entry: Dict[str, object]) -> str:
    provider = entry.get("providerName") or "Provider"
    cert = entry.get("certName") or "Certification"
    subject = entry.get("subjectLabel") or entry.get("subject") or "Sujet"
    content = entry.get("contentTypeLabel") or entry.get("contentType") or "Contenu"
    time_of_day = entry.get("time") or "Heure non précisée"
    return f"{time_of_day} • {provider} · {cert} ({subject} – {content})"


def _register_schedule_job(date: str, job_id: str, entries: List[dict]) -> None:
    """Track the latest job id and queued entries for a given day."""

    with _schedule_reports_lock:
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id:
                _schedule_entry_jobs[str(entry_id)] = job_id

        _schedule_reports[date] = {
            "job_id": job_id,
            "status": "queued",
            "entries": [
                {
                    "id": entry.get("id"),
                    "status": _normalise_schedule_status(entry.get("status")),
                    "message": _summarise_schedule_entry(entry),
                    "channels": entry.get("channels") or [],
                }
                for entry in entries
            ],
            "updated_at": datetime.now().isoformat(),
        }


def _update_schedule_report(
    date: str, entry: Dict[str, object], status: str, *, message: str | None = None
) -> None:
    """Update cached report data for a specific entry within a day."""

    with _schedule_reports_lock:
        report = _schedule_reports.setdefault(date, {"entries": [], "status": "running"})
        entry_id = entry.get("id")
        message = message or _summarise_schedule_entry(entry)
        found = False
        for item in report["entries"]:
            if entry_id and item.get("id") == entry_id:
                item.update(
                    {
                        "status": _normalise_schedule_status(status),
                        "message": message,
                        "channels": entry.get("channels") or [],
                        "job_id": _schedule_entry_jobs.get(str(entry_id)),
                    }
                )
                found = True
                break
        if not found:
            report["entries"].append(
                {
                    "id": entry_id,
                    "status": _normalise_schedule_status(status),
                    "message": message,
                    "channels": entry.get("channels") or [],
                    "job_id": _schedule_entry_jobs.get(str(entry_id)),
                }
            )
        report["status"] = _normalise_schedule_status(status)
        report["updated_at"] = datetime.now().isoformat()


def _finalise_schedule_report(date: str, status: str, counters: Dict[str, int]) -> None:
    """Record the final status of a schedule job."""

    with _schedule_reports_lock:
        report = _schedule_reports.setdefault(date, {"entries": []})
        report["status"] = _normalise_schedule_status(status)
        report["summary"] = {
            "processed": counters.get("processed", 0),
            "succeeded": counters.get("succeeded", 0),
            "failed": counters.get("failed", 0),
        }
        report["updated_at"] = datetime.now().isoformat()


def _attach_job_metadata(entries: List[dict]) -> List[dict]:
    """Attach cached job information to entries."""

    with _schedule_reports_lock:
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id and str(entry_id) in _schedule_entry_jobs:
                entry["jobId"] = _schedule_entry_jobs[str(entry_id)]
    return entries


def _build_schedule_reports(entries: List[dict]) -> Dict[str, Dict[str, object]]:
    """Combine cached report data with the current state of schedule entries."""

    grouped: Dict[str, List[dict]] = {}
    for entry in entries:
        day = entry.get("day")
        if not isinstance(day, str):
            continue
        grouped.setdefault(day, []).append(entry)

    with _schedule_reports_lock:
        reports: Dict[str, Dict[str, object]] = {
            day: dict(report) for day, report in _schedule_reports.items()
        }

    for day, day_entries in grouped.items():
        current = reports.get(day, {"entries": []})
        entries_payload = []
        summary = {"succeeded": 0, "failed": 0, "pending": 0, "running": 0}

        for entry in day_entries:
            status = _normalise_schedule_status(entry.get("status"))
            job_id = entry.get("jobId") or entry.get("job_id")
            message = _summarise_schedule_entry(entry)
            existing_entry = next(
                (item for item in current.get("entries", []) if item.get("id") == entry.get("id")), None
            )
            if existing_entry:
                message = existing_entry.get("message") or message
                job_id = job_id or existing_entry.get("job_id")
            entries_payload.append(
                {
                    "id": entry.get("id"),
                    "status": status,
                    "message": message,
                    "job_id": job_id,
                    "channels": entry.get("channels") or [],
                    "last_run_at": entry.get("lastRunAt"),
                }
            )
            if status == "succeeded":
                summary["succeeded"] += 1
            elif status == "failed":
                summary["failed"] += 1
            elif status == "running":
                summary["running"] += 1
            else:
                summary["pending"] += 1

        overall_status = "succeeded"
        if summary["failed"] > 0:
            overall_status = "failed"
        elif summary["running"] > 0:
            overall_status = "running"
        elif summary["pending"] == len(entries_payload):
            overall_status = "queued"

        reports[day] = {
            **current,
            "entries": entries_payload,
            "summary": summary,
            "status": overall_status,
        }

    return reports


def _serialise_schedule_note(note_text: str, add_image: bool) -> str:
    """Serialize schedule note content along with media toggle metadata."""

    try:
        return json.dumps({"text": note_text, "addImage": add_image})
    except TypeError:
        return note_text


@app.route("/schedule/api", methods=["GET"])
def schedule_list():
    """Return all persisted schedule entries."""

    try:
        entries = db.get_schedule_entries()
    except Exception as exc:  # pragma: no cover - defensive path
        app.logger.exception("Impossible de charger les planifications")
        return jsonify({"error": str(exc)}), 500
    enriched = _attach_job_metadata(entries)
    reports = _build_schedule_reports(enriched)
    return jsonify({"entries": enriched, "reports": reports})


def _schedule_entry_datetime(entry: Dict[str, object]) -> datetime | None:
    """Return the combined datetime of a schedule entry."""

    day = entry.get("day")
    time_of_day = entry.get("time")
    if not isinstance(day, str) or not isinstance(time_of_day, str):
        return None

    try:
        planned_day = date.fromisoformat(day)
        planned_time = dt_time.fromisoformat(time_of_day)
    except ValueError:
        return None

    return datetime.combine(planned_day, planned_time)


@app.route("/schedule/api", methods=["POST"])
def schedule_save():
    """Persist a schedule entry."""

    entry = request.get_json() or {}
    entry.setdefault("id", uuid.uuid4().hex)
    entry.setdefault("channels", [])
    entry.setdefault("note", "")
    entry.setdefault("addImage", True)

    add_image = bool(entry.get("addImage", True))
    note_text = entry.get("note") or ""
    entry["addImage"] = add_image
    entry["note"] = _serialise_schedule_note(note_text, add_image)

    required_fields = [
        "day",
        "time",
        "providerId",
        "providerName",
        "certId",
        "certName",
        "subject",
        "subjectLabel",
        "contentType",
        "contentTypeLabel",
        "link",
    ]
    missing = [field for field in required_fields if not entry.get(field)]
    if missing:
        return jsonify({"error": f"Champs manquants: {', '.join(missing)}"}), 400

    try:
        db.upsert_schedule_entry(entry)
    except Exception as exc:  # pragma: no cover - defensive path
        app.logger.exception("Echec de sauvegarde d'une planification")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "saved", "id": entry["id"]})


@app.route("/schedule/api/<entry_id>", methods=["DELETE"])
def schedule_delete(entry_id: str):
    """Delete a persisted schedule entry."""

    if not entry_id:
        return jsonify({"error": "Identifiant de planification manquant."}), 400

    try:
        db.delete_schedule_entry(entry_id)
    except Exception as exc:  # pragma: no cover - defensive path
        app.logger.exception("Echec de suppression d'une planification")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "deleted", "id": entry_id})


@app.route("/schedule/retry/<entry_id>", methods=["POST"])
def schedule_retry(entry_id: str):
    """Retry execution of a scheduled entry by enqueuing its day batch."""

    if not entry_id:
        return jsonify({"error": "Identifiant de planification manquant."}), 400

    try:
        entries = db.get_schedule_entries()
    except Exception as exc:  # pragma: no cover - defensive path
        app.logger.exception("Impossible de charger les planifications pour relance")
        return jsonify({"error": str(exc)}), 500

    target = next((entry for entry in entries if str(entry.get("id")) == entry_id), None)
    if not target:
        return jsonify({"error": "Planification introuvable."}), 404

    day = target.get("day")
    if not isinstance(day, str) or not day:
        return jsonify({"error": "Date de planification invalide."}), 400

    day_entries = [entry for entry in entries if entry.get("day") == day]
    if not day_entries:
        return jsonify({"error": "Aucune action planifiée pour cette date."}), 404

    try:
        dispatch = _enqueue_schedule_job(day, day_entries)
    except Exception as exc:  # pragma: no cover - defensive path
        app.logger.exception("Echec de réenfilement de la planification %s", entry_id)
        return jsonify({"error": str(exc)}), 500

    dispatch.update({"date": day, "count": len(day_entries)})
    return jsonify(dispatch)


def _launch_execute_schedule_inline(job_id: str, date: str, entries: List[dict]) -> str:
    """Launch execution of planned actions in a background thread."""

    def _run_inline() -> None:
        context = JobContext(job_store, job_id)
        set_cached_status(job_id, "running")
        initialise_job(
            job_store,
            job_id=job_id,
            description="execute-planning",
            metadata={"date": date, "count": len(entries), "entry_ids": [entry.get("id") for entry in entries]},
        )
        job_store.set_status(job_id, "running")

        try:
            counters = _execute_planned_actions(context, date, entries)
        except Exception as exc:  # pragma: no cover - defensive logging only
            app.logger.exception("Execution planifiée échouée pour le %s", date)
            context.set_status("failed", error=str(exc))
        else:
            if counters.get("failed"):
                context.set_status(
                    "failed",
                    error="Certaines actions planifiées ont échoué.",
                )
            else:
                context.set_status("completed")

    threading.Thread(
        target=_run_inline,
        name=f"execute-planning-{job_id}",
        daemon=True,
    ).start()
    return job_id


def _enqueue_schedule_job(date: str, entries: List[dict], *, job_id: str | None = None) -> Dict[str, str]:
    """Enqueue a schedule execution job using Celery or fallback to inline."""

    job_id = job_id or uuid.uuid4().hex
    _register_schedule_job(date, job_id, entries)

    if _is_task_queue_disabled():
        _launch_execute_schedule_inline(job_id, date, entries)
        return {"status": "queued", "job_id": job_id, "mode": "inline"}

    try:
        async_result = execute_schedule_job.apply_async(args=(date, entries), task_id=job_id)
    except QUEUE_EXCEPTIONS as exc:
        _disable_task_queue(exc)
        _launch_execute_schedule_inline(job_id, date, entries)
        return {"status": "queued", "job_id": job_id, "mode": "inline"}

    if getattr(celery_app.conf, "task_always_eager", False):
        return {"status": "queued", "job_id": job_id, "mode": "eager"}

    return {"status": "queued", "job_id": async_result.id, "mode": "celery"}


def _redis_healthcheck_targets() -> Dict[str, List[str]]:
    targets: Dict[str, List[str]] = {}
    broker_url = getattr(celery_app.conf, "broker_url", None)
    result_backend = getattr(celery_app.conf, "result_backend", None)
    for label, url in (("broker", broker_url), ("result_backend", result_backend)):
        if isinstance(url, str) and url.startswith("redis://"):
            targets.setdefault(url, []).append(label)
    return targets


@celery_app.task(name="tasks.redis_healthcheck")
def redis_healthcheck() -> Dict[str, object]:
    """Ping Redis targets used by Celery and record pool metrics."""

    targets = _redis_healthcheck_targets()
    if not targets:
        return {"checked": 0, "healthy": 0, "status": "skipped"}

    try:
        import redis
    except ImportError:  # pragma: no cover - optional dependency
        app.logger.debug("Redis healthcheck skipped: redis package not installed.")
        return {"checked": 0, "healthy": 0, "status": "skipped"}

    socket_options = _redis_socket_options_from_env()
    socket_options.setdefault("socket_keepalive", True)
    socket_options.setdefault(
        "health_check_interval",
        _env_int("CELERY_REDIS_HEALTH_CHECK_INTERVAL", 30, minimum=0),
    )
    socket_timeout = _env_float("CELERY_REDIS_SOCKET_TIMEOUT")
    if socket_timeout is not None:
        socket_options.setdefault("socket_timeout", socket_timeout)
    socket_connect_timeout = _env_float("CELERY_REDIS_SOCKET_CONNECT_TIMEOUT")
    if socket_connect_timeout is not None:
        socket_options.setdefault("socket_connect_timeout", socket_connect_timeout)

    healthy = 0
    failures = 0
    for url, labels in targets.items():
        label = "/".join(sorted(labels))
        try:
            client = redis.Redis.from_url(url, decode_responses=True, **socket_options)
            client.ping()
        except redis.exceptions.RedisError as exc:  # pragma: no cover - runtime path
            failures += 1
            failure_count = _record_redis_health_failure(str(exc))
            app.logger.warning(
                "Redis healthcheck failed (%s): %s (failure #%s)",
                label,
                exc,
                failure_count,
            )
            if failure_count >= 3 and not _is_task_queue_disabled():
                _disable_task_queue(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            failures += 1
            failure_count = _record_redis_health_failure(str(exc))
            app.logger.warning(
                "Redis healthcheck unexpected error (%s): %s (failure #%s)",
                label,
                exc,
                failure_count,
            )
        else:
            healthy += 1
            _reset_redis_health_failures()
            metrics = _redis_pool_metrics(client)
            app.logger.info("Redis healthcheck OK (%s): %s", label, metrics or "{}")

    return {"checked": len(targets), "healthy": healthy, "failed": failures}


def _execute_planned_actions(context: JobContext, date: str, entries: List[dict]) -> Dict[str, int]:
    """Iterate through planned actions and execute all required publications.

    The function logs enough context to audit which pieces of content were sent
    (or failed to send) and returns the final counters so the caller can decide
    whether the overall job succeeded.
    """

    def _shorten(text: str, limit: int = 180) -> str:
        cleaned = " ".join((text or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 1] + "…"

    context.log(f"Planification du {date}: {len(entries)} action(s) détectée(s).")
    counters: Dict[str, int] = {"processed": 0, "succeeded": 0, "failed": 0}
    context.update_counters(**counters, date=date)

    def _update_entry_status(entry_id: str | None, status: str, *, stamp: bool = False) -> None:
        if not entry_id:
            return
        try:
            db.update_schedule_status(
                [entry_id],
                status,
                last_run_at=datetime.now() if stamp else None,
            )
        except Exception as exc:  # pragma: no cover - defensive logging only
            context.log(
                f"[{context.job_id}] Impossible de mettre à jour le statut '{status}' pour la planification "
                f"{entry_id}: {exc}"
            )

    for index, entry in enumerate(entries, start=1):
        context.wait_if_paused()
        provider_name = entry.get("providerName") or "Provider inconnu"
        cert_name = entry.get("certName") or "Certification inconnue"
        subject = entry.get("subjectLabel") or entry.get("subject") or "Sujet"
        content_label = entry.get("contentTypeLabel") or entry.get("contentType") or "Contenu"
        link = entry.get("link") or "Lien non fourni"
        time_of_day = entry.get("time") or "Heure non précisée"
        channels = entry.get("channels") or []
        attach_image = bool(entry.get("addImage", True))
        entry_id = entry.get("id")

        context.log(
            f"[{index}/{len(entries)}] {provider_name} · {cert_name} "
            f"({subject} – {content_label}) à {time_of_day}"
        )
        context.log(f"Canaux : {', '.join(channels) if channels else 'aucun canal spécifié'}")
        context.log(f"Lien/source : {link}")
        include_image = entry.get("addImage", True)
        context.log(f"Visuel : {'avec image' if include_image else 'sans image'}")
        note = entry.get("note")
        if note:
            context.log(f"Note interne : {note}")

        _update_schedule_report(date, entry, "running", message="Action en cours d'exécution.")
        try:
            provider_id = int(entry.get("providerId"))
            cert_id = int(entry.get("certId"))
        except (TypeError, ValueError):
            counters["failed"] += 1
            _update_entry_status(entry_id, "failed", stamp=True)
            context.log("IDs provider/certification invalides : action ignorée.")
            _update_schedule_report(
                date,
                entry,
                "failed",
                message="IDs provider/certification invalides : action ignorée.",
            )
            counters["processed"] += 1
            context.update_counters(**counters, date=date)
            continue

        _update_entry_status(entry_id, "running", stamp=True)
        try:
            result = run_scheduled_publication(
                provider_id=provider_id,
                certification_id=cert_id,
                exam_url=link,
                topic_type=str(entry.get("subject") or ""),
                channels=channels,
                attach_image=attach_image,
            )
        except Exception as exc:  # pragma: no cover - surfaced in job log
            counters["failed"] += 1
            _update_entry_status(entry_id, "failed", stamp=True)
            context.log(f"Erreur lors du déclenchement des publications : {exc}")
            _update_schedule_report(
                date,
                entry,
                "failed",
                message=f"Erreur lors du déclenchement des publications : {exc}",
            )
        else:
            channel_outcomes: List[bool] = []

            if "article" in channels:
                article_payload = result.get("article")
                article_error = result.get("article_error")
                if article_payload:
                    blog_id = result.get("blog_id")
                    context.log(
                        f"Article généré et enregistré"
                        f"{f' (blog #{blog_id})' if blog_id else ''}."
                    )
                    title = result.get("title") or "Article généré"
                    context.log(f"Titre : {_shorten(str(title), limit=120)}")
                    summary = result.get("summary") or ""
                    if summary:
                        context.log(f"Résumé : {_shorten(str(summary), limit=200)}")
                    channel_outcomes.append(True)
                    if result.get("course_art") is not None:
                        context.log("Fiche certification enregistrée.")
                    if result.get("course_art_error"):
                        context.log(f"Fiche certification non enregistrée : {result['course_art_error']}")
                else:
                    channel_outcomes.append(False)
                    context.log(
                        f"Article non généré : {article_error or 'raison inconnue'}"
                    )

            if "x" in channels:
                tweet_result: SocialPostResult | None = result.get("tweet_result")
                tweet_text = (tweet_result.text if tweet_result else None) or result.get("tweet") or ""
                if tweet_result and tweet_result.published:
                    channel_outcomes.append(True)
                    if tweet_text:
                        context.log(f"Tweet envoyé : {_shorten(str(tweet_text))}")
                    else:
                        context.log("Tweet publié avec succès.")
                else:
                    channel_outcomes.append(False)
                    error = (tweet_result and tweet_result.error) or "Tweet non publié."
                    context.log(error)
                    if tweet_text:
                        context.log(f"Contenu du tweet : {_shorten(str(tweet_text))}")

            if "linkedin" in channels:
                linkedin_result: SocialPostResult | None = result.get("linkedin_result")
                linkedin_text = (
                    linkedin_result.text if linkedin_result else None
                ) or result.get("linkedin_post") or ""
                if linkedin_result and linkedin_result.published:
                    channel_outcomes.append(True)
                    if linkedin_text:
                        context.log(f"Post LinkedIn envoyé : {_shorten(str(linkedin_text))}")
                    else:
                        context.log("Post LinkedIn publié avec succès.")
                else:
                    channel_outcomes.append(False)
                    error = (linkedin_result and linkedin_result.error) or "LinkedIn non publié."
                    context.log(error)
                    if linkedin_text:
                        context.log(f"Contenu LinkedIn : {_shorten(str(linkedin_text))}")

            if channel_outcomes and all(channel_outcomes):
                counters["succeeded"] += 1
                _update_entry_status(entry_id, "succeeded", stamp=True)
                _update_schedule_report(
                    date,
                    entry,
                    "succeeded",
                    message="Publication envoyée sur tous les canaux sélectionnés.",
                )
            else:
                counters["failed"] += 1
                _update_entry_status(entry_id, "failed", stamp=True)
                _update_schedule_report(
                    date,
                    entry,
                    "failed",
                    message="Échec sur au moins un canal, voir logs du job.",
                )

        counters["processed"] += 1
        context.update_counters(**counters, date=date)

    context.log(
        f"Planification terminée : {counters['processed']} action(s) traitée(s), "
        f"{counters['succeeded']} réussie(s), {counters['failed']} en échec."
    )
    overall_status = "failed" if counters.get("failed") else "succeeded"
    _finalise_schedule_report(date, overall_status, counters)
    return counters


@celery_app.task(bind=True, name="schedule.execute")
def execute_schedule_job(self, date: str, entries: List[dict]) -> None:
    """Celery wrapper that processes scheduled actions."""

    job_id = self.request.id
    context = JobContext(job_store, job_id)

    set_cached_status(job_id, "running")
    initialise_job(
        job_store,
        job_id=job_id,
        description="execute-planning",
        metadata={"date": date, "count": len(entries), "entry_ids": [entry.get("id") for entry in entries]},
    )
    job_store.set_status(job_id, "running")

    try:
        counters = _execute_planned_actions(context, date, entries)
    except Exception as exc:  # pragma: no cover - surfaced to caller
        context.set_status("failed", error=str(exc))
        raise
    else:
        if counters.get("failed"):
            context.set_status(
                "failed",
                error="Certaines actions planifiées ont échoué.",
            )
        else:
            context.set_status("completed")


@celery_app.task(name="schedule.dispatch_due")
def dispatch_due_schedules() -> Dict[str, object]:
    """Scan planned publications and enqueue those that are due."""

    now = datetime.now()
    try:
        entries = db.get_schedule_entries()
    except Exception as exc:  # pragma: no cover - defensive path
        app.logger.exception("Impossible de charger les planifications à exécuter automatiquement.")
        raise

    due_by_day: Dict[str, List[dict]] = {}
    skipped_invalid: List[str] = []
    for entry in entries:
        scheduled_at = _schedule_entry_datetime(entry)
        if scheduled_at is None:
            skipped_invalid.append(str(entry.get("id", "?")))
            continue
        if scheduled_at <= now:
            due_by_day.setdefault(entry["day"], []).append(entry)

    dispatched: List[Dict[str, object]] = []
    for day, day_entries in sorted(due_by_day.items()):
        dispatch = _enqueue_schedule_job(day, day_entries)
        dispatch["date"] = day
        dispatch["count"] = len(day_entries)
        dispatched.append(dispatch)
        entry_ids = [entry.get("id") for entry in day_entries if entry.get("id")]
        if entry_ids:
            try:
                db.update_schedule_status(entry_ids, "queued")
            except Exception as exc:  # pragma: no cover - defensive logging only
                app.logger.exception(
                    "Impossible de marquer les planifications %s comme en file d'attente.",
                    entry_ids,
                )

    summary: Dict[str, object] = {
        "dispatched_batches": len(dispatched),
        "dispatched_entries": sum(item["count"] for item in dispatched) if dispatched else 0,
    }
    if skipped_invalid:
        summary["skipped_invalid"] = skipped_invalid
    return summary


def _is_authenticated() -> bool:
    """Return True when the user is logged in."""

    return session.get("user") == "exboot"


@app.before_request
def require_login():
    """Protect the application with a simple session-based login check."""

    allowed_endpoints = {"login", "static"}
    if request.endpoint in allowed_endpoints or request.endpoint is None:
        return None

    if _is_authenticated():
        return None

    login_url = url_for("login", next=request.url)
    return redirect(login_url)


@app.route("/login", methods=["GET", "POST"])
def login():
    error: str | None = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == "exboot" and password == GUI_PASSWORD:
            session["user"] = "exboot"
            target = request.args.get("next") or url_for("home")
            return redirect(target)
        error = "Nom d'utilisateur ou mot de passe incorrect."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/schedule/execute", methods=["POST"])
def schedule_execute():
    """Trigger the execution of planned actions for a given day."""

    payload = request.get_json() or {}
    date = (payload.get("date") or "").strip() or "date-inconnue"
    entries = payload.get("entries") or []

    if not isinstance(entries, list) or not entries:
        return jsonify({"error": "Aucune action planifiée transmise."}), 400

    return jsonify(_enqueue_schedule_job(date, entries))


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
        distribution_override = _normalise_distribution(request.form.get("distribution"))
        apply_addition = _is_truthy(request.form.get("apply_addition"))
        return render_template(
            "populate.html",
            provider_id=provider_id,
            cert_id=cert_id,
            is_populating=True,
            selected_distribution=distribution_override,
            selected_apply_addition=apply_addition,
            distribution_defaults=DISTRIBUTION,
        )
    providers = db.get_providers()
    return render_template(
        "populate.html",
        providers=providers,
        is_populating=False,
        distribution_defaults=DISTRIBUTION,
        selected_apply_addition=False,
    )

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
    if total_to_process == 0:
        context.log(f"{task_label} terminé : 0 question traitée.")
        return

    max_workers = _env_int(
        "FIX_MAX_WORKERS",
        _default_parallelism(maximum=4),
        minimum=1,
        maximum=32,
    )
    if max_workers > 1:
        context.log(f"Exécution en parallèle avec {max_workers} worker(s).")

    counters_lock = Lock()
    state = {
        "processed": 0,
        "corrected": corrected,
        "remaining": remaining,
    }

    def _process_question(item: Tuple[int, Dict[str, object]]) -> None:
        index, question = item
        context.wait_if_paused()
        qid = question.get("id")
        context.log(f"[{index}/{total_to_process}] Traitement de la question {qid}.")
        try:
            responses = correct_questions(provider_name, cert_name, [question], action)
        except Exception as exc:  # pragma: no cover - propagated to Celery
            context.log(f"Erreur lors de l'appel OpenAI pour la question {qid}: {exc}")
            return

        if not responses:
            context.log(f"Aucune réponse obtenue pour la question {qid}.")
            return

        result = responses[0] or {}
        try:
            updated = _apply_result(result)
        except Exception as exc:  # pragma: no cover - DB errors surfaced in logs
            context.log(f"Erreur base de données pour la question {qid}: {exc}")
            return

        with counters_lock:
            state["processed"] += 1
            if updated:
                state["corrected"] = (
                    min(total, state["corrected"] + 1)
                    if total
                    else state["corrected"] + 1
                )
                state["remaining"] = max(state["remaining"] - 1, 0)
            current_processed = state["processed"]
            current_corrected = state["corrected"]
            current_remaining = state["remaining"]

        if updated:
            context.log(f"Question {qid} mise à jour.")
        else:
            context.log(f"Aucune modification enregistrée pour la question {qid}.")

        context.update_counters(
            total=total,
            corrected=current_corrected,
            remaining=current_remaining,
            processed=current_processed,
        )
        time.sleep(API_REQUEST_DELAY)

    items: Iterable[Tuple[int, Dict[str, object]]] = enumerate(questions, start=1)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_question, item) for item in items]
        for future in as_completed(futures):
            future.result()

    context.log(
        f"{task_label} terminé : {state['processed']} question(s) traitée(s)."
    )


@celery_app.task(bind=True, name="fix.run")
def run_fix_job(self, provider_id: int, cert_id: int, action: str) -> None:
    """Celery task wrapper for :func:`run_fix`."""

    job_id = self.request.id
    context = JobContext(job_store, job_id)

    set_cached_status(job_id, "running")
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


def _start_fix_job_inline(job_id: str, provider_id: int, cert_id: int, action: str):
    """Launch the fix workflow in a local background thread and return a response."""

    def _run_inline() -> None:
        context = JobContext(job_store, job_id)
        set_cached_status(job_id, "running")
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

    if _is_task_queue_disabled():
        return _start_fix_job_inline(job_id, provider_id, cert_id, action)

    try:
        run_fix_job.apply_async(args=(provider_id, cert_id, action), task_id=job_id)
    except QUEUE_EXCEPTIONS as exc:  # pragma: no cover - defensive, surfaced to client
        app.logger.exception(
            "Unable to enqueue fix job: provider_id=%s cert_id=%s; falling back to inline execution",
            provider_id,
            cert_id,
        )
        _disable_task_queue(str(exc))
        return _start_fix_job_inline(job_id, provider_id, cert_id, action)
    except Exception as exc:
        if getattr(celery_app.conf, "task_always_eager", False):
            app.logger.exception(
                "Fix job failed during eager execution: provider_id=%s cert_id=%s",
                provider_id,
                cert_id,
            )
            try:
                status = job_store.get_status(job_id) or {}
            except JobStoreError:
                status = {}
            payload = {"status": status.get("status", "failed"), "job_id": job_id}
            error = status.get("error") or str(exc)
            if error:
                payload["error"] = error
            return jsonify(payload)
        raise

    return jsonify({"status": "queued", "job_id": job_id})


def _load_job_status(job_id: str):
    """Return job state or a JSON error response when unavailable."""
    cached = get_cached_status(job_id)

    try:
        data = job_store.get_status(job_id)
    except JobStoreError as exc:
        app.logger.exception("Job %s: unable to fetch status from store", job_id)
        if cached is not None:
            app.logger.warning(
                "Job %s: returning cached status because the job store is unavailable", job_id
            )
            return cached, None, None
        return None, jsonify({"error": "job store unavailable", "details": str(exc)}), 503
    if data is None:
        if cached is not None:
            app.logger.warning(
                "Job %s: job store returned no data; using cached snapshot instead", job_id
            )
            return cached, None, None
        return None, jsonify({"error": "unknown job id"}), 404
    cache_job_snapshot(job_id, data)

    return data, None, None


@app.route("/schedule/status/<job_id>", methods=["GET"])
def schedule_status(job_id):
    data, error_response, status = _load_job_status(job_id)
    if error_response is not None:
        return error_response, status
    return jsonify(data)


@app.route("/fix/status/<job_id>", methods=["GET"])
def fix_status(job_id):
    data, error_response, status = _load_job_status(job_id)
    if error_response is not None:
        return error_response, status
    return jsonify(data)


def run_population(
    context: JobContext,
    provider_id: int,
    cert_id: int,
    distribution: Optional[Dict[str, Dict[str, Dict[str, int]]]] = None,
    *,
    apply_addition: bool = False,
) -> None:
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

    distribution_map = distribution or DISTRIBUTION
    distribution_total = _distribution_total(distribution_map)

    context.log(log_analysis)
    context.update_counters(analysis=log_analysis)

    # Récupérer tous les domaines et leurs noms
    domains = db.get_domains_by_certification(cert_id)
    all_domain_names = [name for (_, name) in domains]
    total_domains = len(domains)

    progress_map = {domain_id: DomainProgress(domain_id) for domain_id, _ in domains}
    total_questions_count = sum(progress.total_questions() for progress in progress_map.values())
    context.update_counters(totalDomains=total_domains, totalQuestions=total_questions_count)

    domain_descriptions = {
        item["id"]: item["descr"]
        for item in db.get_domains_description_by_certif(cert_id)
    }

    counters_lock = Lock()
    counters = {
        "domains_processed": 0,
        "total_questions": total_questions_count,
    }

    def _add_questions(amount: int) -> Tuple[int, int]:
        if amount <= 0:
            with counters_lock:
                return counters["domains_processed"], counters["total_questions"]
        with counters_lock:
            counters["total_questions"] += amount
            current_domains = counters["domains_processed"]
            current_total_questions = counters["total_questions"]
        context.update_counters(
            domainsProcessed=current_domains,
            totalQuestions=current_total_questions,
        )
        return current_domains, current_total_questions

    def _mark_domain_completed() -> Tuple[int, int]:
        with counters_lock:
            counters["domains_processed"] += 1
            current_domains = counters["domains_processed"]
            current_total_questions = counters["total_questions"]
        context.update_counters(
            domainsProcessed=current_domains,
            totalQuestions=current_total_questions,
        )
        return current_domains, current_total_questions

    max_workers = _env_int(
        "POPULATION_MAX_WORKERS",
        _default_parallelism(maximum=4),
        minimum=1,
        maximum=32,
    )
    if max_workers > 1:
        context.log(f"Traitement des domaines en parallèle avec {max_workers} worker(s).")

    def _process_domain(domain: Tuple[int, str]) -> None:
        domain_id, domain_name = domain
        context.wait_if_paused()
        context.log(f"[{domain_name}] Domain processing started.")
        progress = progress_map[domain_id]
        current_total = progress.total_questions()
        context.log(f"[{domain_name}] Initial question count: {current_total}")

        if apply_addition:
            context.log(
                f"[{domain_name}] Mode 'Appliquer en Addition' activé : ajout des quantités demandées."
            )
            for difficulty in DIFFICULTY_LEVELS:
                distribution = distribution_map.get(difficulty, {})
                if not distribution:
                    continue
                inserted = process_domain_by_difficulty(
                    context,
                    domain_id,
                    domain_name,
                    difficulty,
                    distribution,
                    provider_name,
                    cert_name,
                    analysis,
                    all_domain_names,
                    domain_descriptions,
                    progress=progress,
                    addition_mode=True,
                )
                if inserted:
                    _add_questions(inserted)

            final_total = progress.total_questions()
            current_domains, _ = _mark_domain_completed()
            context.log(
                f"[{domain_name}] Domain completed (mode addition): final total = {final_total} ({current_domains}/{total_domains})."
            )
            return

        if current_total >= distribution_total:
            current_domains, _ = _mark_domain_completed()
            context.log(
                f"[{domain_name}] Domain already complete (>= {distribution_total} questions). ({current_domains}/{total_domains})"
            )
            return

        context.log(
            f"[{domain_name}] Needs {distribution_total - current_total} additional questions to reach {distribution_total}."
        )

        # Traitement EASY si le domaine est vide
        if current_total == 0:
            context.wait_if_paused()
            context.log(f"[{domain_name}] Empty domain, using EASY distribution.")
            distribution = distribution_map.get("easy", {})
            inserted_easy = process_domain_by_difficulty(
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
                progress=progress,
            )
            if inserted_easy:
                _add_questions(inserted_easy)

            current_total = progress.total_questions()
            context.log(f"[{domain_name}] Total after EASY: {current_total}")

        # Traitement MEDIUM si nécessaire
        if current_total < distribution_total:
            context.wait_if_paused()
            needed_total = distribution_total - current_total
            context.log(
                f"[{domain_name}] Needs {needed_total} additional questions via MEDIUM."
            )
            distribution = distribution_map.get("medium", {})
            inserted_medium = process_domain_by_difficulty(
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
                progress=progress,
            )
            if inserted_medium:
                _add_questions(inserted_medium)
            current_total = progress.total_questions()
            context.log(f"[{domain_name}] Total after MEDIUM: {current_total}")

        # Traitement HARD si toujours nécessaire
        if current_total < distribution_total:
            context.wait_if_paused()
            needed_total = distribution_total - current_total
            context.log(
                f"[{domain_name}] Needs {needed_total} additional questions via HARD."
            )
            distribution = distribution_map.get("hard", {})
            inserted_hard = process_domain_by_difficulty(
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
                progress=progress,
            )
            if inserted_hard:
                _add_questions(inserted_hard)
            current_total = progress.total_questions()
            context.log(f"[{domain_name}] Total after HARD: {current_total}")

        # Vérification finale : si le total reste inférieur à la distribution, le domaine est laissé tel quel.
        if current_total < distribution_total:
            context.log(
                f"[{domain_name}] Distribution completed with {current_total} questions (< {distribution_total})."
            )

        final_total = progress.total_questions()
        current_domains, _ = _mark_domain_completed()
        context.log(
            f"[{domain_name}] Domain completed: final total = {final_total} ({current_domains}/{total_domains})."
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_domain, domain) for domain in domains]
        for future in as_completed(futures):
            future.result()

    context.log(
        f"Process finished: {counters['domains_processed']} domains processed out of {total_domains}."
    )


@celery_app.task(bind=True, name="population.run")
def run_population_job(
    self,
    provider_id: int,
    cert_id: int,
    distribution: Optional[Dict[str, Dict[str, Dict[str, int]]]] = None,
    apply_addition: bool = False,
) -> None:
    """Celery task wrapper for :func:`run_population`."""

    job_id = self.request.id
    metadata = {"provider_id": provider_id, "cert_id": cert_id}
    if distribution:
        metadata["distribution"] = distribution
    if apply_addition:
        metadata["apply_addition"] = True
    context = JobContext(job_store, job_id)

    _ensure_job_marked_running(job_id, metadata)

    try:
        run_population(
            context,
            provider_id,
            cert_id,
            distribution=distribution,
            apply_addition=apply_addition,
        )
    except Exception as exc:  # pragma: no cover - propagated to Celery
        context.set_status("failed", error=str(exc))
        raise
    else:
        context.set_status("completed")


def _run_population_thread(
    job_id: str,
    provider_id: int,
    cert_id: int,
    distribution: Optional[Dict[str, Dict[str, Dict[str, int]]]] = None,
    *,
    apply_addition: bool = False,
) -> threading.Thread:
    """Run the population workflow in a local background thread.

    When the Celery broker is unavailable (for instance when Redis refuses
    new clients because the configured limit is reached) we still want to
    allow the user to launch the populate process.  This helper mimics the
    behaviour of :func:`run_population_job` but executes it within a daemon
    thread of the web process so that the HTTP request can return immediately.
    """

    metadata = {"provider_id": provider_id, "cert_id": cert_id}
    if distribution:
        metadata["distribution"] = distribution

    def _target() -> None:
        context = JobContext(job_store, job_id)
        _ensure_job_marked_running(job_id, metadata)

        try:
            run_population(
                context,
                provider_id,
                cert_id,
                distribution=distribution,
                apply_addition=apply_addition,
            )
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


def _start_population_locally(
    job_id: str,
    provider_id: int,
    cert_id: int,
    distribution: Optional[Dict[str, Dict[str, Dict[str, int]]]] = None,
    *,
    error: Exception | None = None,
    apply_addition: bool = False,
):
    """Execute the population workflow locally and return an HTTP response."""

    try:
        _run_population_thread(
            job_id,
            provider_id,
            cert_id,
            distribution=distribution,
            apply_addition=apply_addition,
        )
    except Exception:  # pragma: no cover - fallback may still fail
        failure_message = (
            "Impossible de démarrer le traitement : la file d'attente des tâches est indisponible."
        )
        if error is not None:
            failure_message += f" ({error})"
        set_cached_status(job_id, "failed", error=str(error) if error else failure_message)
        job_store.set_status(job_id, "failed", error=str(error) if error else failure_message)
        return (
            jsonify({"error": failure_message}),
            500,
        )

    if error is None:
        app.logger.info(
            "Population job %s running in local thread because the task queue is disabled.",
            job_id,
        )
    else:
        app.logger.warning(
            "Population job %s running in local thread because the task queue is unavailable.",
            job_id,
        )

    payload = {"status": "queued", "job_id": job_id, "mode": "local"}
    return jsonify(payload)


def _ensure_job_marked_running(job_id: str, metadata: Dict[str, int]) -> None:
    set_cached_status(job_id, "running")
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
    distribution_override = _normalise_distribution(request.form.get("distribution"))
    apply_addition = _is_truthy(request.form.get("apply_addition"))

    metadata = {"provider_id": provider_id, "cert_id": cert_id}
    if distribution_override:
        metadata["distribution"] = distribution_override
    if apply_addition:
        metadata["apply_addition"] = True

    job_id = initialise_job(
        job_store,
        job_id=uuid.uuid4().hex,
        description="populate-certification",
        metadata=metadata,
    )

    if _is_task_queue_disabled():
        return _start_population_locally(
            job_id,
            provider_id,
            cert_id,
            distribution=distribution_override,
            apply_addition=apply_addition,
        )

    try:
        run_population_job.apply_async(
            args=(provider_id, cert_id, distribution_override, apply_addition),
            task_id=job_id,
        )
    except QUEUE_EXCEPTIONS as exc:  # pragma: no cover - defensive, surfaced to client
        app.logger.exception(
            "Unable to enqueue population job: provider_id=%s cert_id=%s", provider_id, cert_id
        )
        _disable_task_queue(str(exc))
        return _start_population_locally(
            job_id,
            provider_id,
            cert_id,
            distribution=distribution_override,
            error=exc,
            apply_addition=apply_addition,
        )
    except Exception as exc:
        if getattr(celery_app.conf, "task_always_eager", False):
            app.logger.exception(
                "Population job failed during eager execution: provider_id=%s cert_id=%s",
                provider_id,
                cert_id,
            )
            try:
                status = job_store.get_status(job_id) or {}
            except JobStoreError:
                status = {}
            payload = {"status": status.get("status", "failed"), "job_id": job_id}
            error = status.get("error") or str(exc)
            if error:
                payload["error"] = error
            return jsonify(payload)
        raise

    return jsonify({"status": "queued", "job_id": job_id})


@app.route("/populate/status/<job_id>", methods=["GET"])
def populate_status(job_id):
    data, error_response, status = _load_job_status(job_id)
    if error_response is not None:
        return error_response, status
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
    *,
    progress: Optional[DomainProgress] = None,
    addition_mode: bool = False,
) -> int:
    """Generate and insert questions for a domain according to the distribution."""

    import json  # local import to avoid dependency at module import time

    total_inserted = 0

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

            if progress is not None:
                existing_count = progress.category_total(difficulty, qtype, scenario_type)
            else:
                existing_count = db.count_questions_in_category(domain_id, difficulty, qtype, scenario_type)
            context.log(
                f"[{domain_name} - {difficulty.upper()}] {qtype} with scenario '{scenario_type}' existing: "
                f"{existing_count} (target: {target_count})."
            )
            if addition_mode:
                needed = max(target_count, 0)
                if needed <= 0:
                    continue
                context.wait_if_paused()
                context.log(
                    f"[{domain_name} - {difficulty.upper()}] Adding {needed} questions for "
                    f"{qtype} with scenario '{scenario_type}' (mode addition)."
                )
            elif existing_count < target_count:
                context.wait_if_paused()
                needed = target_count - existing_count
                context.log(
                    f"[{domain_name} - {difficulty.upper()}] Needs {needed} questions for "
                    f"{qtype} with scenario '{scenario_type}'."
                )
            else:
                continue
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
                    stats = db.insert_questions(domain_id, questions_data, scenario_type)
                    context.log(
                        f"[{domain_name} - {difficulty.upper()}] {needed} questions inserted for "
                        f"{qtype} with scenario '{scenario_type}'."
                    )
                    imported = 0
                    if isinstance(stats, dict):
                        imported = int(stats.get("imported_questions", 0) or 0)
                    if progress is not None and imported:
                        progress.record_insertion(difficulty, qtype, scenario_type, imported)
                    total_inserted += imported
                except Exception as exc:
                    context.log(
                        f"[{domain_name} - {difficulty.upper()}] Insert error for {qtype} "
                        f"with scenario '{scenario_type}': {exc}"
                    )

    return total_inserted

@app.route("/populate/pause/<job_id>", methods=["POST"])
def pause_populate(job_id):
    if job_store.pause(job_id):
        mark_job_paused(job_id)
        return jsonify({"status": "paused"})
    return jsonify({"error": "unknown job id"}), 404


@app.route("/populate/resume/<job_id>", methods=["POST"])
def resume_populate(job_id):
    if job_store.resume(job_id):
        mark_job_resumed(job_id)
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
