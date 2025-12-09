# Dépannage Celery et Redis

## Erreur « max number of clients reached »
Cette erreur provient de Redis : l'instance a atteint la limite de connexions simultanées autorisées (par exemple un Redis Cloud Free limité à ~30-40 clients). Le worker Celery ouvre plusieurs connexions (pool broker + backend résultat + supervision) et peut donc saturer rapidement si le nombre de workers est élevé.

### Symptômes
- Le worker affiche des boucles de reconnexion (`ConnectionError: max number of clients reached`).
- Les tâches ne partent plus et Celery bascule parfois en exécution locale forcée.

### Correctifs rapides
1. **Réduire les connexions ouvertes**
   - Limiter le pool Celery :
     ```bash
     set CELERY_POOL_LIMIT=4
     set CELERY_MAX_CONNECTIONS=4
     set CELERY_RESULT_MAX_CONNECTIONS=4
     ```
     (sur Linux/macOS remplacer `set` par `export`).
   - Démarrer le worker avec moins de processus : `celery -A app.celery_app worker --concurrency=4`.
   - Sous Windows, le script `troubleshoot.bat` applique automatiquement ces limites et envoie
     un ordre `autoscale` au worker en cours pour réduire la concurrence **sans l'arrêter** :
     ```bat
     troubleshoot.bat
     ```
     (assurez-vous d'avoir activé `.venv\Scripts\activate.bat` pour que `celery` soit dans le PATH).
2. **Fermer les clients inactifs**
   - Arrêter les workers/tests qui tournent encore sur la même base Redis.
3. **Augmenter la capacité Redis**
   - Passer à un plan Redis avec plus de clients autorisés.

### Contexte côté code
- Le pool Celery utilise par défaut un plafond raisonnable (_max 8_) via `CELERY_POOL_LIMIT` dans `app.py`. Sur un hébergeur limité, il peut être nécessaire de descendre cette valeur à `2` ou `1`.
- Les mêmes variables `CELERY_MAX_CONNECTIONS` et `CELERY_RESULT_MAX_CONNECTIONS` contrôlent respectivement le transport broker et le backend résultat.
