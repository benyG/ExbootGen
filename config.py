"""Application configuration values.

This module centralises runtime configuration, including database access and
OpenAI settings.  Sensitive values such as the OpenAI API key are read from the
environment to avoid committing secrets to version control.
"""

import os

# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------

# Values for the database connection are sourced from environment variables to
# avoid hard-coding credentials.  Each key falls back to an empty string when
# the corresponding variable is missing so that imports succeed even if the
# database is not configured (e.g. during tests).
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", ""),
    "user": os.environ.get("DB_USER", ""),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", ""),
}

# ---------------------------------------------------------------------------
# OpenAI configuration
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = "o4-mini"
OPENAI_API_URL = os.environ.get(
    "OPENAI_API_URL", "https://api.openai.com/v1/chat/completions"
)
OPENAI_MAX_RETRIES = int(os.environ.get("OPENAI_MAX_RETRIES", "5"))

# Delay (in seconds) between two consecutive calls to the OpenAI API during the
# populate process.  This value can be tuned via the ``API_REQUEST_DELAY``
# environment variable.
API_REQUEST_DELAY = float(os.environ.get("API_REQUEST_DELAY", "1"))

# ---------------------------------------------------------------------------
# GUI authentication
# ---------------------------------------------------------------------------
# Password required by the local GUI before the web service can be started.
# It defaults to ``admin`` but can be overridden via the ``GUI_PASSWORD``
# environment variable to avoid hard-coding sensitive values in the codebase.
GUI_PASSWORD = os.environ.get("GUI_PASSWORD", "admin")

# ---------------------------------------------------------------------------
# Question distribution
# ---------------------------------------------------------------------------
# ``DISTRIBUTION`` defines how many questions must be generated for each
# difficulty level.  It is a nested mapping following the structure:
#
# ``{difficulty: {question_type: {scenario_style: target_count}}}``
#
# Example::
#
#     DISTRIBUTION = {
#         "easy": {
#             "qcm": {"no": 10, "scenario": 0, "scenario-illustrated": 0},
#             "truefalse": {"no": 5, "scenario": 0, "scenario-illustrated": 0},
#         }
#     }
#
# meaning that for the "easy" level we expect 10 multiple-choice questions
# without scenario and 5 true/false questions without scenario.

DISTRIBUTION = {
    "easy": {
        "qcm": {"no": 10, "scenario": 0, "scenario-illustrated": 0},
        "truefalse": {"no": 5, "scenario": 0, "scenario-illustrated": 0},
        "matching": {"no": 3, "scenario": 0, "scenario-illustrated": 0},
        "drag-n-drop": {"no": 4, "scenario": 0, "scenario-illustrated": 0},
    },
    "medium": {
        "qcm": {"no": 5, "scenario": 6, "scenario-illustrated": 6},
        "truefalse": {"no": 3, "scenario": 3, "scenario-illustrated": 0},
        "matching": {"no": 1, "scenario": 4, "scenario-illustrated": 4},
        "drag-n-drop": {"no": 1, "scenario": 4, "scenario-illustrated": 4},
    },
    "hard": {
        "qcm": {"no": 2, "scenario": 6, "scenario-illustrated": 6},
        "truefalse": {"no": 2, "scenario": 0, "scenario-illustrated": 0},
        "matching": {"no": 1, "scenario": 5, "scenario-illustrated": 5},
        "drag-n-drop": {"no": 1, "scenario": 5, "scenario-illustrated": 5},
    },
}

