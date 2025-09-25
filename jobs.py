"""Utilities for tracking long-running background jobs.

This module provides a :class:`JobStore` abstraction that can persist job state
either in memory (for development/tests) or in Redis (for production).  Each
job keeps its own log, counters and pause flag so that multiple browser tabs
can trigger independent workloads without stepping on each other.

The :class:`JobContext` exposes a tiny façade that background workers can use
to append log entries, update counters or honour pause/resume requests.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


LOGGER = logging.getLogger(__name__)

MAX_LOG_ENTRIES = 5000


class JobStoreError(RuntimeError):
    """Raised when a job operation cannot be completed."""


class BaseJobStore:
    """Interface implemented by job state backends."""

    def create_job(
        self,
        job_id: str,
        *,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        raise NotImplementedError

    def append_log(self, job_id: str, message: str) -> None:
        raise NotImplementedError

    def update_counters(self, job_id: str, values: Dict[str, Any]) -> None:
        raise NotImplementedError

    def set_status(self, job_id: str, status: str, *, error: Optional[str] = None) -> None:
        raise NotImplementedError

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def pause(self, job_id: str) -> bool:
        raise NotImplementedError

    def resume(self, job_id: str) -> bool:
        raise NotImplementedError

    def wait_if_paused(self, job_id: str, interval: float = 0.5) -> None:
        raise NotImplementedError


class InMemoryJobStore(BaseJobStore):
    """Simple process-local job store suitable for unit tests."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create_job(
        self,
        job_id: str,
        *,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = threading.Event()
        event.set()
        now = time.time()
        with self._lock:
            self._jobs[job_id] = {
                "status": "queued",
                "log": [],
                "counters": {},
                "error": None,
                "description": description,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
                "pause_event": event,
            }

    def _require_job(self, job_id: str) -> Dict[str, Any]:
        try:
            return self._jobs[job_id]
        except KeyError as exc:  # pragma: no cover - defensive
            raise JobStoreError(f"Unknown job_id {job_id}") from exc

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            data = self._require_job(job_id)
            data["log"].append(message)
            if len(data["log"]) > MAX_LOG_ENTRIES:
                del data["log"][: len(data["log"]) - MAX_LOG_ENTRIES]
            data["updated_at"] = time.time()

    def update_counters(self, job_id: str, values: Dict[str, Any]) -> None:
        with self._lock:
            data = self._require_job(job_id)
            data["counters"].update(values)
            data["updated_at"] = time.time()

    def set_status(self, job_id: str, status: str, *, error: Optional[str] = None) -> None:
        with self._lock:
            data = self._require_job(job_id)
            data["status"] = status
            if error is not None:
                data["error"] = error
            data["updated_at"] = time.time()

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._jobs.get(job_id)
            if not data:
                return None
            return {
                "job_id": job_id,
                "status": data["status"],
                "log": list(data["log"]),
                "counters": dict(data["counters"]),
                "error": data["error"],
                "description": data.get("description"),
                "metadata": dict(data.get("metadata", {})),
                "created_at": data["created_at"],
                "updated_at": data["updated_at"],
            }

    def pause(self, job_id: str) -> bool:
        with self._lock:
            data = self._jobs.get(job_id)
            if not data or data["status"] in {"completed", "failed"}:
                return False
            data["pause_event"].clear()
            data["status"] = "paused"
            data["updated_at"] = time.time()
            return True

    def resume(self, job_id: str) -> bool:
        with self._lock:
            data = self._jobs.get(job_id)
            if not data:
                return False
            data["pause_event"].set()
            if data["status"] == "paused":
                data["status"] = "running"
            data["updated_at"] = time.time()
            return True

    def wait_if_paused(self, job_id: str, interval: float = 0.5) -> None:  # pragma: no cover - blocking
        data = self._require_job(job_id)
        data["pause_event"].wait()


class RedisJobStore(BaseJobStore):
    """Persist job state inside Redis."""

    def __init__(self, url: str, *, namespace: str = "exbootgen") -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - validated in create_job_store
            raise JobStoreError("redis package is required for RedisJobStore") from exc

        self._redis_module = redis
        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._ns = namespace

        try:
            self._redis.ping()
        except redis.exceptions.AuthenticationError as exc:
            raise JobStoreError(
                "Authentification refusée par Redis. Vérifiez le mot de passe et, si "
                "nécessaire, indiquez l'utilisateur dans l'URL (ex. "
                "'redis://default:motdepasse@hote:port/0' pour Redis Cloud)."
            ) from exc
        except redis.exceptions.ResponseError as exc:
            raise JobStoreError(
                "Redis a rejeté l'index de base sélectionné. La plupart des services "
                "managés (Redis Cloud inclus) n'exposent que la base 0 ; mettez à jour "
                "JOB_STORE_URL/CELERY_* pour utiliser '/0'."
            ) from exc
        except (redis.exceptions.ConnectionError, OSError) as exc:
            raise JobStoreError(
                "Connexion impossible à Redis via "
                f"{url!s}. Vérifiez le nom d'hôte, le port, vos règles de pare-feu et "
                "l'éventuelle obligation d'ajouter votre adresse IP sur le tableau de "
                "bord Redis Cloud."
            ) from exc

    def _job_key(self, job_id: str) -> str:
        return f"{self._ns}:job:{job_id}"

    def _log_key(self, job_id: str) -> str:
        return f"{self._ns}:job:{job_id}:log"

    def _pause_key(self, job_id: str) -> str:
        return f"{self._ns}:job:{job_id}:paused"

    def create_job(
        self,
        job_id: str,
        *,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        mapping = {
            "status": "queued",
            "error": "",
            "description": description or "",
            "metadata": json.dumps(metadata or {}),
            "counters": json.dumps({}),
            "created_at": str(now),
            "updated_at": str(now),
        }
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), mapping=mapping)
        pipe.delete(self._log_key(job_id))
        pipe.set(self._pause_key(job_id), "0")
        try:
            pipe.execute()
        except self._redis_module.exceptions.ResponseError as exc:
            raise JobStoreError(
                "Impossible d'enregistrer le job dans Redis : vérifiez que l'URL utilise "
                "une base autorisée (souvent '/0' sur Redis Cloud)."
            ) from exc

    def append_log(self, job_id: str, message: str) -> None:
        if not self._redis.exists(self._job_key(job_id)):
            raise JobStoreError(f"Unknown job_id {job_id}")
        pipe = self._redis.pipeline()
        pipe.rpush(self._log_key(job_id), message)
        pipe.ltrim(self._log_key(job_id), -MAX_LOG_ENTRIES, -1)
        pipe.hset(self._job_key(job_id), "updated_at", str(time.time()))
        pipe.execute()

    def update_counters(self, job_id: str, values: Dict[str, Any]) -> None:
        key = self._job_key(job_id)
        if not self._redis.exists(key):
            raise JobStoreError(f"Unknown job_id {job_id}")
        raw = self._redis.hget(key, "counters") or "{}"
        counters = json.loads(raw)
        counters.update(values)
        pipe = self._redis.pipeline()
        pipe.hset(key, mapping={"counters": json.dumps(counters), "updated_at": str(time.time())})
        pipe.execute()

    def set_status(self, job_id: str, status: str, *, error: Optional[str] = None) -> None:
        key = self._job_key(job_id)
        if not self._redis.exists(key):
            raise JobStoreError(f"Unknown job_id {job_id}")
        mapping: Dict[str, str] = {"status": status, "updated_at": str(time.time())}
        if error is not None:
            mapping["error"] = error
        self._redis.hset(key, mapping=mapping)

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        key = self._job_key(job_id)
        if not self._redis.exists(key):
            return None
        data = self._redis.hgetall(key)
        log_entries = self._redis.lrange(self._log_key(job_id), 0, -1)
        return {
            "job_id": job_id,
            "status": data.get("status", "unknown"),
            "log": log_entries,
            "counters": json.loads(data.get("counters") or "{}"),
            "error": data.get("error") or None,
            "description": data.get("description") or None,
            "metadata": json.loads(data.get("metadata") or "{}"),
            "created_at": float(data.get("created_at", 0.0)),
            "updated_at": float(data.get("updated_at", 0.0)),
        }

    def pause(self, job_id: str) -> bool:
        key = self._job_key(job_id)
        if not self._redis.exists(key):
            return False
        status = self._redis.hget(key, "status")
        if status in {"completed", "failed"}:
            return False
        pipe = self._redis.pipeline()
        pipe.set(self._pause_key(job_id), "1")
        pipe.hset(key, mapping={"status": "paused", "updated_at": str(time.time())})
        pipe.execute()
        return True

    def resume(self, job_id: str) -> bool:
        key = self._job_key(job_id)
        if not self._redis.exists(key):
            return False
        pipe = self._redis.pipeline()
        pipe.set(self._pause_key(job_id), "0")
        current_status = self._redis.hget(key, "status")
        if current_status == "paused":
            pipe.hset(key, mapping={"status": "running", "updated_at": str(time.time())})
        else:
            pipe.hset(key, "updated_at", str(time.time()))
        pipe.execute()
        return True

    def wait_if_paused(self, job_id: str, interval: float = 0.5) -> None:  # pragma: no cover - blocking
        while self._redis.get(self._pause_key(job_id)) == "1":
            time.sleep(interval)


class SQLiteJobStore(BaseJobStore):
    """Persist job state inside a SQLite database."""

    def __init__(self, url: str) -> None:
        if url == "sqlite:///:memory:":
            self._path = ":memory:"
        elif url.startswith("sqlite:///"):
            path = url[len("sqlite:///") :]
            self._path = path
        elif url.startswith("sqlite://"):
            path = url[len("sqlite://") :]
            self._path = path
        else:
            self._path = url

        if self._path not in {":memory:", ""}:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    counters TEXT NOT NULL,
                    error TEXT,
                    description TEXT,
                    metadata TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id)"
            )

    def create_job(
        self,
        job_id: str,
        *,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (job_id, status, counters, error, description, metadata, created_at, updated_at, paused)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    job_id,
                    "queued",
                    json.dumps({}),
                    None,
                    description,
                    json.dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            conn.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))

    def append_log(self, job_id: str, message: str) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
            if cur.fetchone() is None:
                raise JobStoreError(f"Unknown job_id {job_id}")
            conn.execute(
                "INSERT INTO job_logs (job_id, message, created_at) VALUES (?, ?, ?)",
                (job_id, message, now),
            )
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            conn.execute(
                """
                DELETE FROM job_logs
                WHERE job_id = ?
                  AND id NOT IN (
                        SELECT id FROM job_logs
                        WHERE job_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                  )
                """,
                (job_id, job_id, MAX_LOG_ENTRIES),
            )

    def update_counters(self, job_id: str, values: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT counters FROM jobs WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
            if row is None:
                raise JobStoreError(f"Unknown job_id {job_id}")
            counters = json.loads(row["counters"] or "{}")
            counters.update(values)
            conn.execute(
                "UPDATE jobs SET counters = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(counters), time.time(), job_id),
            )

    def set_status(self, job_id: str, status: str, *, error: Optional[str] = None) -> None:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
            if cur.fetchone() is None:
                raise JobStoreError(f"Unknown job_id {job_id}")
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
                (status, error, time.time(), job_id),
            )

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            job = cur.fetchone()
            if job is None:
                return None
            logs = conn.execute(
                "SELECT message FROM job_logs WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
            return {
                "job_id": job_id,
                "status": job["status"],
                "log": [row["message"] for row in logs],
                "counters": json.loads(job["counters"] or "{}"),
                "error": job["error"],
                "description": job["description"],
                "metadata": json.loads(job["metadata"] or "{}"),
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
            }

    def pause(self, job_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT status FROM jobs WHERE job_id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None or row["status"] in {"completed", "failed"}:
                return False
            conn.execute(
                "UPDATE jobs SET paused = 1, status = 'paused', updated_at = ? WHERE job_id = ?",
                (time.time(), job_id),
            )
            return True

    def resume(self, job_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,))
            if cur.fetchone() is None:
                return False
            conn.execute(
                "UPDATE jobs SET paused = 0, updated_at = ?, status = CASE WHEN status = 'paused' THEN 'running' ELSE status END WHERE job_id = ?",
                (time.time(), job_id),
            )
            return True

    def wait_if_paused(self, job_id: str, interval: float = 0.5) -> None:  # pragma: no cover - blocking
        while True:
            with self._connect() as conn:
                cur = conn.execute("SELECT paused FROM jobs WHERE job_id = ?", (job_id,))
                row = cur.fetchone()
                if row is None:
                    raise JobStoreError(f"Unknown job_id {job_id}")
                if not row["paused"]:
                    return
            time.sleep(interval)


def _build_redis_url_from_env() -> Optional[str]:
    """Assemble a Redis URL from REDIS_* variables when explicit URLs are absent."""

    host = os.getenv("REDIS_HOST")
    if not host:
        return None

    username = os.getenv("REDIS_USERNAME", "")
    password = os.getenv("REDIS_PASSWORD", "")

    auth = ""
    if username and password:
        auth = f"{username}:{password}@"
    elif username:
        auth = f"{username}@"
    elif password:
        auth = f":{password}@"

    return f"redis://{auth}{host}/0"


def create_job_store() -> BaseJobStore:
    """Create the most suitable job store based on environment variables."""

    url = (
        os.getenv("JOB_STORE_URL")
        or os.getenv("REDIS_URL")
        or os.getenv("CELERY_RESULT_BACKEND")
        or os.getenv("CELERY_BROKER_URL")
        or _build_redis_url_from_env()
    )

    if url:
        if url.startswith("redis://"):
            try:
                LOGGER.info("Using RedisJobStore with url=%s", url)
                return RedisJobStore(url)
            except Exception as exc:  # pragma: no cover - fallback when Redis unavailable
                LOGGER.warning("Falling back to SQLite job store: %s", exc)
        if url.startswith("sqlite://"):
            LOGGER.info("Using SQLiteJobStore with url=%s", url)
            return SQLiteJobStore(url)

    default_sqlite = Path(os.getenv("JOB_STORE_SQLITE_PATH", "job_state.db"))
    LOGGER.info("Using default SQLiteJobStore at %s", default_sqlite)
    return SQLiteJobStore(f"sqlite:///{default_sqlite}")


@dataclass(frozen=True)
class JobContext:
    """Context object passed to background jobs."""

    store: BaseJobStore
    job_id: str

    def log(self, message: str) -> None:
        self.store.append_log(self.job_id, message)

    def update_counters(self, **values: Any) -> None:
        self.store.update_counters(self.job_id, values)

    def wait_if_paused(self) -> None:
        self.store.wait_if_paused(self.job_id)

    def set_status(self, status: str, *, error: Optional[str] = None) -> None:
        self.store.set_status(self.job_id, status, error=error)


def initialise_job(
    store: BaseJobStore,
    *,
    job_id: str,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Register a new job in ``store`` and return its identifier."""

    store.create_job(job_id, description=description, metadata=metadata)
    return job_id


__all__ = [
    "BaseJobStore",
    "InMemoryJobStore",
    "JobContext",
    "JobStoreError",
    "RedisJobStore",
    "SQLiteJobStore",
    "create_job_store",
    "initialise_job",
]

