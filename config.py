# config.py

# Database configuration
DB_CONFIG = {
    "host": "x.x.x.x",
    "user": "user",
    "password": "pass",
    "database": "db",
}

# OpenAI configuration
OPENAI_API_KEY = "sk-key"
OPENAI_MODEL   = "o4-mini"

# Distribution table: numbers of questions per difficulty, question type, and scenario style.

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
    "easy": {
        "qcm": {"no": 2, "scenario": 6, "scenario-illustrated": 6},
        "truefalse": {"no": 2, "scenario": 0, "scenario-illustrated": 0},
        "matching": {"no": 1, "scenario": 5, "scenario-illustrated": 5},
        "drag-n-drop": {"no": 1, "scenario": 5, "scenario-illustrated": 5},
    }
}
