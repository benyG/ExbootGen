Mapping → variables :

X_API_CONSUMER_KEY = API Key

X_API_CONSUMER_SECRET = API Key Secret

X_API_ACCESS_TOKEN = Access Token

X_API_ACCESS_TOKEN_SECRET = Access Token Secret

## Dépannage Celery

Pour l'erreur Redis `max number of clients reached`, voir `docs/celery_troubleshooting.md` pour les causes et correctifs (réduire le pool, limiter la concurrence ou augmenter le plan Redis).
Sur Windows, lancez `troubleshoot.bat` pour appliquer automatiquement les limites conseillées sans couper le worker.
Le script `start_app.bat` démarre désormais le worker Celery avec le pool `eventlet` (`-P eventlet -c 10 --pool-limit=20`) afin de limiter le nombre de threads et de connexions ouvertes sur Redis Cloud.

### Réglages Redis/Celery pour les connexions inactives

- `CELERY_REDIS_SOCKET_KEEPALIVE` (bool) : active le keep-alive TCP côté Celery (désactivé par défaut si non défini).
- `CELERY_REDIS_SOCKET_TIMEOUT` / `CELERY_REDIS_SOCKET_CONNECT_TIMEOUT` (secondes) : délai d'attente sur la socket/connexion initiale (optionnels).
- `CELERY_REDIS_HEALTH_CHECK_INTERVAL` (secondes) : intervalle de ping des connexions Celery (0 par défaut, configurez `30` pour éviter la coupure des connexions inactives).
- `REDIS_SOCKET_KEEPALIVE` (bool, défaut : `1`) : keep-alive pour le client Redis du magasin de jobs.
- `REDIS_HEALTH_CHECK_INTERVAL` (secondes, défaut : `30`) : ping périodique utilisé par `redis-py` pour fermer les connexions inactives.
- `REDIS_SOCKET_TIMEOUT` (secondes, défaut : `10`) et `REDIS_SOCKET_CONNECT_TIMEOUT` (secondes, défaut : `5`) : délais pour les opérations Redis du magasin de jobs.

Celery programme désormais une tâche de beat `tasks.redis_healthcheck` (toutes les minutes) qui ping le broker/backend Redis et journalise les métriques du pool. En cas d'échecs répétés, la file de tâches est désactivée automatiquement pour éviter qu'un Redis instable ne bloque les jobs en cours ; le worker bascule alors en mode `task_always_eager`.

### Garde-fous pour limiter les connexions Redis

- `CELERY_TASK_IGNORE_RESULT` (bool, défaut : `1`) : empêche le stockage du statut `SUCCESS` dans Redis pour éviter des connexions backend inutiles.
- `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP` (bool, défaut : `1`) : réessaie la connexion Redis au démarrage au lieu d'échouer immédiatement.
- `CELERY_POOL_LIMIT_CAP` (int, défaut : `20`) : plafond de sécurité appliqué aux valeurs `CELERY_POOL_LIMIT`, `CELERY_MAX_CONNECTIONS` et `CELERY_RESULT_MAX_CONNECTIONS`.
- `CELERY_REDIS_HEALTHCHECK_PERIOD` (secondes, défaut : `60`, minimum : `60`) : fréquence de la tâche `tasks.redis_healthcheck` pour éviter un ping trop fréquent du broker/backend.

## Démarrage rapide

- **Windows** : utilisez `start_app.bat` pour configurer les variables d'environnement, lancer le worker Celery dans une nouvelle fenêtre et démarrer le serveur WSGI (Waitress par défaut, Gunicorn via WSL en option).
- **Linux (Ubuntu)** : exécutez `./start_app.sh` après avoir configuré vos variables dans le fichier. Le script active `.venv`, démarre Celery en arrière-plan (logs dans `/tmp/celery_worker.log`) puis lance Gunicorn si disponible, sinon Waitress
