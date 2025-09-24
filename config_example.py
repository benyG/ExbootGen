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
        return f"redis://:{self.password}@{self.host}/1"


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    api_url: str = "https://api.openai.com/v1/chat/completions"
    max_retries: int = 5
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
        api_url=os.getenv(
            "OPENAI_API_URL", "https://api.openai.com/v1/chat/completions"
        ),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")),
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
