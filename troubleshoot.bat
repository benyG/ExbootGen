@echo off
setlocal ENABLEEXTENSIONS
REM ---------------------------------------------------------------------------
REM  Script de maintenance Celery/Redis : limite les connexions ouvertes sans
REM  couper l'application (envoie un ordre d'autoscale au worker en cours).
REM  A utiliser lorsque l'erreur Redis "max number of clients reached" apparait.
REM ---------------------------------------------------------------------------

REM === Parametres ajustables ===
REM  Concurrence maximale cible pour le worker Celery en cours
set "TARGET_MAX_CONCURRENCY=4"
REM  Concurrence minimale conservee (garde 1 processus pour traiter les taches)
set "TARGET_MIN_CONCURRENCY=1"

REM  Parametres persistants pour les prochains demarrages (reduisent le pool)
set "CELERY_POOL_LIMIT=%TARGET_MAX_CONCURRENCY%"
set "CELERY_MAX_CONNECTIONS=%TARGET_MAX_CONCURRENCY%"
set "CELERY_RESULT_MAX_CONNECTIONS=%TARGET_MAX_CONCURRENCY%"

REM ---------------------------------------------------------------------------
REM  Verifications prealables
REM ---------------------------------------------------------------------------
where celery >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] L'executable "celery" est introuvable dans le PATH.
    echo         Activez l'environnement virtuel : .venv\Scripts\activate.bat
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM  Test de reachabilite du worker
REM ---------------------------------------------------------------------------
celery -A app.celery_app inspect ping >nul 2>nul
if errorlevel 1 (
    echo [INFO] Aucun worker joignable. Les variables CELERY_* sont posees pour
    echo        les prochains demarrages, mais aucune reduction a chaud n'a ete
    echo        appliquee (redemarrez proprement le worker si necessaire).
    exit /b 0
)

REM ---------------------------------------------------------------------------
REM  Reduction a chaud des workers (autoscale)
REM ---------------------------------------------------------------------------
celery -A app.celery_app control autoscale %TARGET_MAX_CONCURRENCY% %TARGET_MIN_CONCURRENCY%
if errorlevel 1 (
    echo [AVERTISSEMENT] L'autoscale a echoue. Verifiez les journaux Celery.
) else (
    echo [OK] Concurrence reduite a %TARGET_MAX_CONCURRENCY% (min %TARGET_MIN_CONCURRENCY%).
    echo     Les taches continuent d'etre traitees pendant la reduction.
)

echo.
echo [RAPPEL] Les variables CELERY_POOL_LIMIT / CELERY_MAX_CONNECTIONS /
echo          CELERY_RESULT_MAX_CONNECTIONS sont definies pour cette session.
echo          Relancez le worker avec ces valeurs pour conserver la limite.

endlocal
