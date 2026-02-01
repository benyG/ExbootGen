#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Script de démarrage pour ExbootGen (Ubuntu/Linux)
# ---------------------------------------------------------------------------
#  Ajustez les valeurs ci-dessous en fonction de votre infrastructure.
#  Les paramètres par défaut pointent vers l'instance Redis Cloud fournie.
#  Pour MySQL et OpenAI, remplacez les exemples par vos valeurs réelles.

set -euo pipefail

# === Paramètres MySQL ===
export DB_HOST="127.0.0.1"
export DB_USER="exbootgen"
export DB_PASSWORD="mot-de-passe-a-remplacer"
export DB_NAME="exbootgen"

# === Paramètres OpenAI ===
export OPENAI_API_KEY="sk-remplacez-moi"
export OPENAI_API_URL="https://api.openai.com/v1/responses"
export OPENAI_MAX_RETRIES="5"
export API_REQUEST_DELAY="1"
export OPENAI_MODEL="gpt-5-mini"

# === API Examboot ===
export API_KEY="votre-api-key-examboot"
export EXAMBOOT_CREATE_TEST_URL="https://examboot.net/create-test"

# === Paramètres X (Twitter) ===
#  Pour publier un tweet, fournissez les identifiants OAuth 1.0a ci-dessous
#  (contexte utilisateur). Ils sont obligatoires pour l'authentification.
export X_API_CONSUMER_KEY="votre-consumer-key"
export X_API_CONSUMER_SECRET="votre-consumer-secret"
export X_API_ACCESS_TOKEN="votre-access-token"
export X_API_ACCESS_TOKEN_SECRET="votre-access-token-secret"

# === Paramètres Redis / Celery ===
export REDIS_HOST="redis-xxxxxxxxxxxx"
export REDIS_PASSWORD="xxxxxxxxxxxxxxx"

export JOB_STORE_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}/0"
export CELERY_BROKER_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}/0"
export CELERY_RESULT_BACKEND="redis://:${REDIS_PASSWORD}@${REDIS_HOST}/0"

# === Mot de passe de l'interface ===
export GUI_PASSWORD="admin"

# ---------------------------------------------------------------------------
#  Activation de l'environnement virtuel
# ---------------------------------------------------------------------------
if [[ ! -f ".venv/bin/activate" ]]; then
    echo "[ERREUR] L'environnement virtuel .venv est introuvable." >&2
    echo "         Créez-le avec : python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi
source .venv/bin/activate

# ---------------------------------------------------------------------------
#  Lancement du worker Celery en arrière-plan
# ---------------------------------------------------------------------------
celery_cmd=(celery -A app.celery_app worker --loglevel=info)

if command -v setsid >/dev/null 2>&1; then
    echo "Démarrage du worker Celery (logs : /tmp/celery_worker.log)..."
    setsid "${celery_cmd[@]}" > /tmp/celery_worker.log 2>&1 &
elif command -v nohup >/dev/null 2>&1; then
    echo "Démarrage du worker Celery (logs : /tmp/celery_worker.log)..."
    nohup "${celery_cmd[@]}" > /tmp/celery_worker.log 2>&1 &
else
    echo "[AVERTISSEMENT] Impossible de lancer Celery en arrière-plan automatiquement." >&2
    echo "                Lancez-le manuellement avec : ${celery_cmd[*]}" >&2
fi

sleep 5

# ---------------------------------------------------------------------------
#  Lancement de Celery Beat pour l'exécution automatique des planifications
# ---------------------------------------------------------------------------
celery_beat_cmd=(celery -A app.celery_app beat --loglevel=info)

if command -v setsid >/dev/null 2>&1; then
    echo "Démarrage de Celery Beat (logs : /tmp/celery_beat.log)..."
    setsid "${celery_beat_cmd[@]}" > /tmp/celery_beat.log 2>&1 &
elif command -v nohup >/dev/null 2>&1; then
    echo "Démarrage de Celery Beat (logs : /tmp/celery_beat.log)..."
    nohup "${celery_beat_cmd[@]}" > /tmp/celery_beat.log 2>&1 &
else
    echo "[AVERTISSEMENT] Impossible de lancer Celery Beat en arrière-plan automatiquement." >&2
    echo "                Lancez-le manuellement avec : ${celery_beat_cmd[*]}" >&2
fi

# Laisser quelques secondes au scheduler pour démarrer
sleep 2

# ---------------------------------------------------------------------------
#  Reverse proxy HTTPS via Caddy (Let’s Encrypt)
# ---------------------------------------------------------------------------
#  Pour activer HTTPS automatiquement :
#    - Installez Caddy (https://caddyserver.com/docs/install).
#    - Adaptez le fichier Caddyfile (par défaut ./Caddyfile) avec votre domaine
#      et assurez-vous que les ports 80 et 443 sont accessibles.
#    - Décommentez l’une des deux lignes ci-dessous pour lancer Caddy en même temps
#      que l’application. Le Caddyfile doit exister avant le lancement.
# ---------------------------------------------------------------------------
# CADDYFILE_PATH="${CADDYFILE_PATH:-$(pwd)/Caddyfile}"
# if command -v caddy >/dev/null 2>&1 && [[ -f "${CADDYFILE_PATH}" ]]; then
#     echo "Lancement de Caddy avec ${CADDYFILE_PATH} (HTTPS auto via Let's Encrypt)..."
#     if command -v setsid >/dev/null 2>&1; then
#         setsid caddy run --config "${CADDYFILE_PATH}" > /tmp/caddy.log 2>&1 &
#     elif command -v nohup >/dev/null 2>&1; then
#         nohup caddy run --config "${CADDYFILE_PATH}" > /tmp/caddy.log 2>&1 &
#     else
#         echo "[AVERTISSEMENT] Impossible de démarrer Caddy en arrière-plan automatiquement." >&2
#     fi
# else
#     echo "[INFO] Caddy n'est pas lancé automatiquement (binaire ou Caddyfile manquant)." >&2
# fi

# ---------------------------------------------------------------------------
#  Lancement du serveur WSGI (Gunicorn si disponible, sinon Waitress)
# ---------------------------------------------------------------------------
if command -v gunicorn >/dev/null 2>&1; then
    echo "Lancement du serveur Gunicorn..."
    exec gunicorn -w 4 -k gthread app:app
fi

# Vérifie que Waitress est installée
python - <<'PY'
try:
    import waitress  # noqa: F401
except Exception:
    import sys
    sys.exit(1)
PY
waitress_available=$?

if [[ $waitress_available -eq 0 ]]; then
    echo "Lancement du serveur Waitress..."
    exec python -m waitress --listen=0.0.0.0:8000 app:app
else
    echo "[ERREUR] Gunicorn et Waitress sont indisponibles." >&2
    echo "         Installez l'un des deux (pip install gunicorn waitress)." >&2
    exit 1
fi
