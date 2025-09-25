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

REM === Paramètres Redis / Celery ===
set "REDIS_HOST=redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453"
set "REDIS_USERNAME=default"
set "REDIS_PASSWORD=yACmUW5fjfEFG3MVcKrGJw0s0HNDLIt2"

set "REDIS_AUTH="
if defined REDIS_USERNAME (
    if defined REDIS_PASSWORD (
        set "REDIS_AUTH=%REDIS_USERNAME%:%REDIS_PASSWORD%@"
    ) else (
        set "REDIS_AUTH=%REDIS_USERNAME%@"
    )
) else if defined REDIS_PASSWORD (
    set "REDIS_AUTH=:%REDIS_PASSWORD%@"
)

set "JOB_STORE_URL=redis://%REDIS_AUTH%%REDIS_HOST%/0"
set "CELERY_BROKER_URL=redis://%REDIS_AUTH%%REDIS_HOST%/0"
set "CELERY_RESULT_BACKEND=redis://%REDIS_AUTH%%REDIS_HOST%/0"

REM === Mot de passe de l'interface ===
set "GUI_PASSWORD=admin"

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
start "Celery Worker" cmd /k "celery -A app.celery_app worker --loglevel=info"

REM Laisser quelques secondes au worker pour se connecter a Redis
timeout /t 5 /nobreak >nul
echo.
echo [INFO] Si la fenetre "Celery Worker" affiche "getaddrinfo failed" ou
echo        "Error 11001", verifiez le nom d'hote et le port Redis specifies
echo        dans ce script. Comparez-les a la console Redis Cloud (champ
echo        ENDPOINT) puis relancez ce script une fois la correction appliquee.

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
