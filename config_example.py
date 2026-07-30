"""Exemple complet de configuration pour ExbootGen.

Copiez ce fichier sous le nom ``config_local.py`` ou importez les constantes
qu'il définit depuis ``config.py`` si vous préférez gérer la configuration par
fichier plutôt que par variables d'environnement.

Les valeurs renseignées reprennent les informations fournies pour Redis Cloud
ainsi qu'une configuration de base pour MySQL et l'API OpenAI. **Remplacez** les
identifiants sensibles (mot de passe MySQL, clé OpenAI) avant de déployer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    user: str
    password: str
    name: str


@dataclass(frozen=True)
class RedisSettings:
    host: str
    password: str

    @property
    def broker_url(self) -> str:
        return f"redis://:{self.password}@{self.host}/0"

    @property
    def result_backend(self) -> str:
        return f"redis://:{self.password}@{self.host}/0"

    @property
    def job_store_url(self) -> str:
        """URL utilisée par le stockage d'état des jobs."""

        # Redis Cloud impose l'utilisation de la base « 0 ». Si vous exploitez
        # une instance Redis auto-hébergée, vous pouvez changer ce numéro selon
        # vos besoins.
        return f"redis://:{self.password}@{self.host}/0"

@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    model: str = "gpt-5-mini"
    api_url: str = "https://api.openai.com/v1/responses"
    max_retries: int = 5
    timeout_seconds: float = 120.0
    request_delay: float = 1.0


@dataclass(frozen=True)
class GUISettings:
    password: str = "admin"


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseSettings
    redis: RedisSettings
    openai: OpenAISettings
    gui: GUISettings

    @property
    def db_config(self) -> Dict[str, str]:
        return {
            "host": self.database.host,
            "user": self.database.user,
            "password": self.database.password,
            "database": self.database.name,
        }


CONFIG = AppConfig(
    database=DatabaseSettings(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        user=os.getenv("DB_USER", "exbootgen"),
        password=os.getenv("DB_PASSWORD", "mot-de-passe-a-remplacer"),
        name=os.getenv("DB_NAME", "exbootgen"),
    ),
    redis=RedisSettings(
        host=os.getenv(
            "REDIS_HOST",
            "redis-25453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453",
        ),
        password=os.getenv("REDIS_PASSWORD", "yACmUW5fjfEFG3MVcKrGJw0s0HNDLIt2"),
    ),
    openai=OpenAISettings(
        api_key=os.getenv("OPENAI_API_KEY", "sk-remplacez-moi"),
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        api_url=os.getenv(
            "OPENAI_API_URL", "https://api.openai.com/v1/responses"
        ),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")),
        timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "120")),
        request_delay=float(os.getenv("API_REQUEST_DELAY", "1")),
    ),
    gui=GUISettings(password=os.getenv("GUI_PASSWORD", "admin")),
)

# Exemple d'utilisation -----------------------------------------------------
#
# from config_example import CONFIG
# celery.conf.broker_url = CONFIG.redis.broker_url
# db_connection = mysql.connector.connect(**CONFIG.db_config)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Growth reporting
# ---------------------------------------------------------------------------
# The monthly recurring revenue the growth dashboard measures progress against.
MONTHLY_REVENUE_GOAL = float(os.environ.get("MONTHLY_REVENUE_GOAL", "5000"))

# ---------------------------------------------------------------------------
# Lead magnets
# ---------------------------------------------------------------------------
# Where the console pushes a generated asset so the platform can gate it.
EXAMBOOT_MAGNET_URL = os.environ.get(
    "EXAMBOOT_MAGNET_URL", "https://examboot.net/lead-magnet"
)
EXAMBOOT_BASE_URL = os.environ.get("EXAMBOOT_BASE_URL", "https://examboot.net").rstrip("/")

# ---------------------------------------------------------------------------
# Video publication
# ---------------------------------------------------------------------------
# Feeds the console publishes to on its own. Everything else is downloaded and
# posted by hand — see VIDEO_MANUAL_CHANNELS in videopub.py.
VIDEO_AUTO_CHANNELS = tuple(
    channel.strip()
    for channel in os.environ.get("VIDEO_AUTO_CHANNELS", "linkedin,x").split(",")
    if channel.strip()
)
#: How many times a transient failure is retried before the admin is asked.
VIDEO_MAX_ATTEMPTS = int(os.environ.get("VIDEO_MAX_ATTEMPTS", "3"))

# ---------------------------------------------------------------------------
# Admin notifications
# ---------------------------------------------------------------------------
# Where the console writes when it needs a human: an expired token, a
# publication it could not complete. Without SMTP the console still shows the
# alert in its own interface, it just cannot reach anyone who is not looking.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") not in ("0", "false", "False")
#: Public address of this console, so a notification can link to the page that
#: fixes the problem rather than merely describing it.
CONSOLE_BASE_URL = os.environ.get("CONSOLE_BASE_URL", "").rstrip("/")
