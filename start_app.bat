@echo off
setlocal ENABLEEXTENSIONS
REM ---------------------------------------------------------------------------
REM  Configuration des variables d'environnement pour ExbootGen
REM ---------------------------------------------------------------------------
REM  Ajustez les valeurs ci-dessous en fonction de votre infrastructure.
REM  Les paramètres par défaut pointent vers l'instance Redis Cloud fournie.
REM  Pour MySQL et OpenAI, remplacez les exemples par vos valeurs réelles.

REM === Paramètres MySQL ===
set "DB_HOST=127.0.0.1"
set "DB_USER=exbootgen"
set "DB_PASSWORD=mot-de-passe-a-remplacer"
set "DB_NAME=exbootgen"

REM === Paramètres OpenAI ===
set "OPENAI_API_KEY=sk-remplacez-moi"
set "OPENAI_API_URL=https://api.openai.com/v1/chat/completions"
set "OPENAI_MAX_RETRIES=5"
set "API_REQUEST_DELAY=1"
set "OPENAI_MODEL=gpt-5-mini"

REM === API Examboot ===
set "API_KEY=votre-api-key-examboot"
set "EXAMBOOT_CREATE_TEST_URL=https://examboot.net/create-test"

REM === Parametres X (Twitter) ===
REM  Pour publier un tweet, fournissez les identifiants OAuth 1.0a ci-dessous
REM  (user context). Ils sont obligatoires pour l'authentification.
set "X_API_CONSUMER_KEY=votre-consumer-key"
set "X_API_CONSUMER_SECRET=votre-consumer-secret"
set "X_API_ACCESS_TOKEN=votre-access-token"
set "X_API_ACCESS_TOKEN_SECRET=votre-access-token-secret"

REM === Paramètres Redis / Celery ===
set "REDIS_HOST=redis-xxxxxxxxxxxx"
set "REDIS_PASSWORD=xxxxxxxxxxxxxxx"

set "JOB_STORE_URL=redis://:%REDIS_PASSWORD%@%REDIS_HOST%/0"
set "CELERY_BROKER_URL=redis://:%REDIS_PASSWORD%@%REDIS_HOST%/0"
set "CELERY_RESULT_BACKEND=redis://:%REDIS_PASSWORD%@%REDIS_HOST%/0"

REM === Mot de passe de l'interface ===
set "GUI_PASSWORD=admin"

REM === Google Cloud Storage (upload images depuis l'éditeur) ===
REM  Point GOOGLE_APPLICATION_CREDENTIALS vers la clé JSON du compte de service
REM  GCS autorisé sur le bucket cible.
set "GOOGLE_APPLICATION_CREDENTIALS=C:\chemin\vers\service-account.json"
set "GCS_BUCKET_NAME=exambootstorage"
set "GCS_UPLOAD_FOLDER=img_question"

REM ---------------------------------------------------------------------------
REM  Activation de l'environnement virtuel
REM ---------------------------------------------------------------------------
if not exist ".venv\Scripts\activate.bat" (
    echo [ERREUR] L'environnement virtuel .venv est introuvable.
    echo          Creez-le avec : python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    exit /b 1
)
call .venv\Scripts\activate.bat

REM ---------------------------------------------------------------------------
REM  Lancement du worker Celery dans une nouvelle fenetre
REM ---------------------------------------------------------------------------
REM  Utilisation d'eventlet (-P) et limite de concurrence pour reduire les connexions Redis
REM  Ajustez -c (concurrency) et --pool-limit si vous disposez de plus de marge Redis
start "Celery Worker" cmd /k "celery -A app.celery_app worker --loglevel=info -P eventlet -c 10 --pool-limit=20"

REM ---------------------------------------------------------------------------
REM  Lancement de Celery Beat pour l'execution automatique des plannings
REM ---------------------------------------------------------------------------
start "Celery Beat" cmd /k "celery -A app.celery_app beat --loglevel=info"

REM Laisser quelques secondes aux services Celery pour se connecter a Redis
timeout /t 5 /nobreak >nul

REM ---------------------------------------------------------------------------
REM  Reverse proxy HTTPS via Caddy (Let's Encrypt)
REM ---------------------------------------------------------------------------
REM  Pour activer HTTPS automatiquement :
REM    - Installez Caddy (https://caddyserver.com/docs/install).
REM    - Adaptez le Caddyfile (par defaut %CD%\Caddyfile) avec votre domaine et
REM      assurez-vous que les ports 80 et 443 sont accessibles.
REM    - Decommentez le bloc ci-dessous pour lancer Caddy en meme temps
REM      que l'application. Le Caddyfile doit exister avant le lancement.
REM ---------------------------------------------------------------------------
REM set "CADDYFILE=%CD%\Caddyfile"
REM where caddy >nul 2>nul
REM if %ERRORLEVEL%==0 if exist "%CADDYFILE%" (
REM     echo Lancement de Caddy avec %CADDYFILE% (HTTPS auto via Let's Encrypt)...
REM     start "Caddy HTTPS Proxy" cmd /k "caddy run --config \"%CADDYFILE%\""
REM ) else (
REM     echo [INFO] Caddy n'est pas lance automatiquement (binaire ou Caddyfile manquant).
REM )

REM ---------------------------------------------------------------------------
REM  Choix du serveur WSGI multi-workers (Gunicorn via WSL ou Waitress natif)
REM ---------------------------------------------------------------------------
set "SERVER_CHOICE="
echo.
echo Choisissez le mode de demarrage :
echo   [1] Gunicorn via WSL (requis : wsl.exe installe)
echo   [2] Waitress natif Windows
set /p "SERVER_CHOICE=Votre choix [1/2] (defaut 2) : "

if "%SERVER_CHOICE%"=="1" goto RUN_WSL
if "%SERVER_CHOICE%"=="2" goto RUN_WAITRESS
if "%SERVER_CHOICE%"=="" goto RUN_WAITRESS

echo Option non reconnue. Lancement de Waitress par defaut.
goto RUN_WAITRESS

:RUN_WSL
if not exist "%SystemRoot%\system32\wsl.exe" (
    echo [ERREUR] WSL n'est pas disponible sur cette machine.
    echo          Choisissez l'option Waitress ou installez WSL.
    goto RUN_WAITRESS
)
echo Lancement de Gunicorn via WSL...
wsl -e gunicorn -w 4 -k gthread app:app
goto END

:RUN_WAITRESS
echo Lancement du serveur Waitress natif...
echo Pour l'installer : pip install waitress (deja inclus dans requirements.txt).
python -m waitress --listen=0.0.0.0:8000 app:app

:END

endlocal
