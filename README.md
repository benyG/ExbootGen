Mapping → variables :

X_API_CONSUMER_KEY = API Key

X_API_CONSUMER_SECRET = API Key Secret

X_API_ACCESS_TOKEN = Access Token

X_API_ACCESS_TOKEN_SECRET = Access Token Secret

## Dépannage Celery

Pour l'erreur Redis `max number of clients reached`, voir `docs/celery_troubleshooting.md` pour les causes et correctifs (réduire le pool, limiter la concurrence ou augmenter le plan Redis).
Sur Windows, lancez `troubleshoot.bat` pour appliquer automatiquement les limites conseillées sans couper le worker.

## Démarrage rapide

- **Windows** : utilisez `start_app.bat` pour configurer les variables d'environnement, lancer le worker Celery dans une nouvelle fenêtre et démarrer le serveur WSGI (Waitress par défaut, Gunicorn via WSL en option).
- **Linux (Ubuntu)** : exécutez `./start_app.sh` après avoir configuré vos variables dans le fichier. Le script active `.venv`, démarre Celery en arrière-plan (logs dans `/tmp/celery_worker.log`) puis lance Gunicorn si disponible, sinon Waitress.
