# ExbootGen

## Démarrage rapide avec Redis Cloud

Si vous disposez d'une instance Redis Cloud à l'adresse
`redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453` avec le mot
de passe `yACmUW5fjfEFG3MVcKrGJw0s0HNDLIt2`, suivez ces étapes pour faire
fonctionner l'application :

1. Créez un environnement virtuel puis installez les dépendances :

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Exportez les variables d'environnement attendues par `config.py` et par la
   file de tâches Celery :

   ```bash
   export REDIS_PASSWORD="yACmUW5fjfEFG3MVcKrGJw0s0HNDLIt2"
   export REDIS_USERNAME="default"
   export JOB_STORE_URL="redis://${REDIS_USERNAME}:${REDIS_PASSWORD}@redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453/0"
   export CELERY_BROKER_URL="redis://${REDIS_USERNAME}:${REDIS_PASSWORD}@redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453/0"
   export CELERY_RESULT_BACKEND="redis://${REDIS_USERNAME}:${REDIS_PASSWORD}@redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453/0"
   ```

   > ℹ️ Redis Cloud n'autorise que la base numéro `0`. L'usage est séparé du
   > broker Celery grâce aux préfixes de clés utilisés par l'application.

   Ajoutez également vos paramètres MySQL (`DB_HOST`, `DB_USER`, `DB_PASSWORD`,
   `DB_NAME`) et votre clé OpenAI (`OPENAI_API_KEY`). Le module `config.py`
   lit automatiquement ces variables.

3. Lancez le worker Celery dans un premier terminal :

   ```bash
   celery -A app.celery_app worker --loglevel=info
   ```

4. Démarrez l'application avec Gunicorn dans un second terminal :

   ```bash
   gunicorn -w 4 -k gthread app:app
   ```

5. Ouvrez `http://127.0.0.1:8000` dans le navigateur. Chaque onglet déclenche
   désormais sa propre tâche en parallèle en s'appuyant sur Redis Cloud pour
   stocker l'état.

### Lancement rapide sous Windows

Un script `start_app.bat` est fourni à la racine du projet. Il définit les
variables d'environnement nécessaires, active l'environnement virtuel `.venv`,
ouvre un terminal pour le worker Celery puis lance l'application.

1. Créez l'environnement virtuel et installez les dépendances une fois :

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Ajustez au besoin les valeurs définies dans `start_app.bat` (identifiants
   MySQL, clé OpenAI…).

3. Double-cliquez sur `start_app.bat` ou exécutez `start_app.bat` depuis une
   invite de commandes. Le script vous propose alors deux scénarios :

   - **Option 1 : Gunicorn via WSL** — si `wsl.exe` est disponible, vous pouvez
     lancer `gunicorn -w 4 -k gthread app:app` depuis l'environnement Linux pour
     profiter d'un modèle multi-workers classique.
   - **Option 2 : Waitress natif Windows** — recommandé si vous exécutez tout
     sous Windows pur. Waitress tourne en multi-threads et reste compatible avec
     Celery/Redis.

   Sélectionnez l'option adaptée à votre poste. Si vous choisissez Gunicorn
   alors que WSL n'est pas installé, le script vous suggère de basculer sur
   Waitress.

> ℹ️  Le script suppose que Redis Cloud est accessible avec l'URL
> `redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453`, l'utilisateur
> `default` et le mot de passe `yACmUW5fjfEFG3MVcKrGJw0s0HNDLIt2`. Modifiez les
> lignes correspondantes si vous disposez d'autres identifiants.

### Dépannage Redis Cloud

Si la fenêtre Celery (ou les logs Python) affichent `getaddrinfo failed`,
`invalid username-password pair` ou une "connexion impossible" vers Redis :

1. Vérifiez que le nom d'hôte correspond exactement à celui indiqué dans la
   console Redis Cloud. Le préfixe numérique (`redis-15453`, `redis-25453`, …)
   dépend de votre instance ; la moindre faute de frappe empêche la résolution
   DNS.
2. Vérifiez l'utilisateur et le mot de passe. Sur Redis Cloud, l'utilisateur
   actif est généralement `default` ; assurez-vous qu'il figure bien dans l'URL
   (`redis://default:motdepasse@…`).
3. Confirmez que le port est correct et que votre réseau autorise les connexions
   sortantes vers ce port.
4. Sur Redis Cloud, ajoutez votre adresse IP publique dans l'onglet **Allowed
   IPs** si l'instance n'est pas ouverte à toutes les adresses.
5. Relancez ensuite `start_app.bat` ou vos processus manuels. L'application
   retombe automatiquement sur SQLite si Redis reste inaccessible, mais aucun
   traitement distribué ne fonctionnera tant que la connexion n'est pas
   rétablie.

## Choisir son scénario d'exécution (Windows natif ou WSL/Linux)

Selon l'environnement disponible sur votre machine Windows, deux approches
s'offrent à vous :

1. **Windows natif (Waitress)**

   - Avantage : aucune dépendance supplémentaire, tout se lance depuis Windows.
   - Commande manuelle :

     ```powershell
     .venv\Scripts\activate
     celery -A app.celery_app worker --loglevel=info
     # Dans un second terminal
     .venv\Scripts\activate
     python -m waitress --listen=0.0.0.0:8000 app:app
     ```

   - Waitress accepte le paramètre `--threads` si vous souhaitez ajuster le
     parallélisme (`python -m waitress --listen=0.0.0.0:8000 --threads=8 app:app`).

2. **WSL ou Linux natif (Gunicorn)**

   - Avantage : workers séparés capables d'encaisser plus de trafic simultané.
   - Depuis Windows, lancez `start_app.bat` et choisissez l'option Gunicorn ou
     passez directement sous WSL :

     ```bash
     source .venv/bin/activate
     celery -A app.celery_app worker --loglevel=info
     # Dans un autre terminal WSL
     source .venv/bin/activate
     gunicorn -w 4 -k gthread app:app
     ```

Dans les deux cas, chaque requête HTTP délègue les traitements longs (OpenAI,
BD) à Celery. Vous pouvez donc ouvrir plusieurs onglets ou sessions et lancer
des populations en parallèle sans bloquer l'interface.

## Configuration des variables d'environnement


L'application lit plusieurs variables d'environnement pour configurer l'accès à la base de données et à l'API OpenAI ainsi que pour contrôler le taux d'envoi des requêtes. Les variables principales sont :

- `DB_HOST` : adresse du serveur MySQL
- `DB_USER` : utilisateur de la base de données
- `DB_PASSWORD` : mot de passe de l'utilisateur
- `DB_NAME` : nom de la base de données
- `OPENAI_API_KEY` : clé API OpenAI (obligatoire)
- `OPENAI_API_URL` : URL de l'endpoint Chat Completions (optionnel)
- `OPENAI_MAX_RETRIES` : nombre maximal de tentatives en cas d'échec (optionnel)
- `API_REQUEST_DELAY` : délai entre deux requêtes lors de la population (optionnel)
- `CELERY_BROKER_URL` : URL du broker de tâches Celery (par défaut `redis://localhost:6379/0`)
- `CELERY_RESULT_BACKEND` : backend de résultats Celery (par défaut identique au broker)
- `JOB_STORE_URL` : URL du stockage d'état des jobs (Redis recommandé)
- `REDIS_USERNAME` : utilisateur Redis (souvent `default` sur Redis Cloud)
- `REDIS_PASSWORD` : mot de passe Redis
-   *Exemple :* `JOB_STORE_URL=redis://localhost:6379/0` (Redis Cloud n'autorise
    que la base « 0 ») ou `JOB_STORE_URL=sqlite:///job_state.db`
- `CELERY_TASK_ALWAYS_EAGER` : définir à `1` pour exécuter les tâches localement sans worker (tests)

### Sous Windows – PowerShell
Pour définir des variables pour la session en cours :

```powershell
$env:DB_HOST = "127.0.0.1"
$env:DB_USER = "user"
$env:DB_PASSWORD = "mot-de-passe"
$env:DB_NAME = "ma-base"
$env:OPENAI_API_KEY = "sk-votre-cle"
$env:OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
$env:OPENAI_MAX_RETRIES = "5"
$env:API_REQUEST_DELAY = "1"
$env:REDIS_USERNAME = "default"
$env:REDIS_PASSWORD = "mot-de-passe-redis"
```

Pour enregistrer ces variables de manière permanente :

```powershell
setx DB_HOST "127.0.0.1"
setx DB_USER "user"
setx DB_PASSWORD "mot-de-passe"
setx DB_NAME "ma-base"
setx OPENAI_API_KEY "sk-votre-cle"
setx OPENAI_API_URL "https://api.openai.com/v1/chat/completions"
setx OPENAI_MAX_RETRIES "5"
setx API_REQUEST_DELAY "1"
setx REDIS_USERNAME "default"
setx REDIS_PASSWORD "mot-de-passe-redis"
```

### Sous Windows – Invite de commandes (cmd)
Pour la session uniquement :

```cmd
set DB_HOST=127.0.0.1
set DB_USER=user
set DB_PASSWORD=mot-de-passe
set DB_NAME=ma-base
set OPENAI_API_KEY=sk-votre-cle
set OPENAI_API_URL=https://api.openai.com/v1/chat/completions
set OPENAI_MAX_RETRIES=5
set API_REQUEST_DELAY=1
set REDIS_USERNAME=default
set REDIS_PASSWORD=mot-de-passe-redis
```

Pour une configuration persistante :

```cmd
setx DB_HOST "127.0.0.1"
setx DB_USER "user"
setx DB_PASSWORD "mot-de-passe"
setx DB_NAME "ma-base"
setx OPENAI_API_KEY "sk-votre-cle"
setx OPENAI_API_URL "https://api.openai.com/v1/chat/completions"
setx OPENAI_MAX_RETRIES "5"
setx API_REQUEST_DELAY "1"
setx REDIS_USERNAME "default"
setx REDIS_PASSWORD "mot-de-passe-redis"
```

Après avoir défini les variables avec `setx`, redémarrez votre terminal pour qu'elles soient prises en compte.

## Configuration de Redis

Les tâches longues et l'état des jobs sont persistés dans Redis lorsque la
variable `JOB_STORE_URL` pointe vers une instance Redis (ex. `redis://localhost:6379/0`).
Vous pouvez utiliser une instance locale ou un service managé.

### Démarrer Redis rapidement

*Via Docker :*

```bash
docker run --name exbootgen-redis -p 6379:6379 -d redis:7
```

*Sur macOS (Homebrew) :*

```bash
brew install redis
brew services start redis
```

*Sous Linux Debian/Ubuntu :*

```bash
sudo apt-get install redis-server
sudo service redis-server start
```

### Variables d'environnement à définir

```
JOB_STORE_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

En production, ajustez les numéros de base selon votre configuration Redis.
Certaines offres managées (dont Redis Cloud) n'autorisent **que** la base `0` :
utilisez simplement des préfixes de clés différents si vous devez partager
la même base entre plusieurs usages. Si vous ne définissez pas explicitement
ces variables, l'application tente d'utiliser `redis://localhost:6379/0` pour
le broker, le backend **et** le job store.

### Exemple avec Redis Cloud

Pour une instance gérée, les URLs doivent inclure l'hôte, le port et le mot de
passe fournis par le service. Par exemple, avec une instance Redis Cloud :

```bash
export REDIS_PASSWORD="yACmUW5fjfEFG3MVcKrGJw0s0HNDLIt2"
export REDIS_USERNAME="default"
export JOB_STORE_URL="redis://${REDIS_USERNAME}:${REDIS_PASSWORD}@redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453/0"
export CELERY_BROKER_URL="redis://${REDIS_USERNAME}:${REDIS_PASSWORD}@redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453/0"
export CELERY_RESULT_BACKEND="redis://${REDIS_USERNAME}:${REDIS_PASSWORD}@redis-15453.crce197.us-east-2-1.ec2.redns.redis-cloud.com:15453/0"
```

Veillez à stocker le mot de passe dans un gestionnaire de secrets ou une
variable d'environnement protégée. Chaque URL utilise le schéma `redis://` avec
la syntaxe `redis://utilisateur:motdepasse@hote:port/base`. Sur Redis Cloud,
l'utilisateur par défaut est `default`. Pour une instance locale sans ACL,
laissez simplement l'utilisateur vide (`redis://:motdepasse@hote:port/base`).

### Exemple de fichier `config.py`

Le fichier `config.py` du dépôt lit ses valeurs depuis les variables
d'environnement. Si vous préférez fournir une configuration complète via un
fichier, copiez `config_example.py` en `config_local.py` (ou remplacez
directement `config.py`) puis adaptez les valeurs :

```python
from config_example import CONFIG

DB_CONFIG = CONFIG.db_config
OPENAI_API_KEY = CONFIG.openai.api_key
OPENAI_API_URL = CONFIG.openai.api_url
OPENAI_MAX_RETRIES = CONFIG.openai.max_retries
API_REQUEST_DELAY = CONFIG.openai.request_delay
GUI_PASSWORD = CONFIG.gui.password
```

Les URLs Redis (`JOB_STORE_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`)
et autres paramètres sont ainsi centralisés dans `config_example.py`, qui
reprend déjà l'exemple Redis Cloud et peut être personnalisé onglet par onglet.

## Exécution recommandée (Gunicorn + Celery)

Le traitement de génération de questions est pris en charge par un worker
Celery externe. Pour éviter qu'une requête HTTP ne bloque les autres, lancez
l'application avec un serveur WSGI multi-workers puis démarrez le worker.

1. **Installer les dépendances Python**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Vérifier que Redis fonctionne**

   ```bash
   redis-cli -u redis://localhost:6379/0 ping
   # → PONG
   ```

3. **Exporter les variables d'environnement nécessaires** (`DB_*`, `OPENAI_*`,
   `JOB_STORE_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, etc.).

4. **Démarrer le worker Celery** (dans un terminal séparé) :

   ```bash
   celery -A app.celery_app worker --loglevel=info
   ```

5. **Démarrer l'application sous Gunicorn** :

   ```bash
   gunicorn -w 4 -k gthread app:app
   ```

   * `-w 4` exécute 4 workers Python capables de traiter des requêtes en
     parallèle.
   * `-k gthread` utilise un worker multi-threads compatible avec les appels
     bloquants (MySQL, OpenAI, etc.).

6. **Accéder à l'application** sur `http://127.0.0.1:8000`.

Avec cette architecture, chaque onglet du navigateur déclenche sa propre tâche
identifiée par un `job_id` sans interférer avec les autres.

## Suivi des tâches de population

* `POST /populate/process` — démarre une nouvelle tâche et retourne
  `{ "job_id": "..." }`.
* `GET /populate/status/<job_id>` — récupère les journaux et compteurs du job.
* `POST /populate/pause/<job_id>` — met en pause le job correspondant.
* `POST /populate/resume/<job_id>` — relance un job précédemment mis en pause.

Le front-end met à jour ses éléments en utilisant ces endpoints ; il est donc
possible d'ouvrir plusieurs onglets, chacun suivant son propre identifiant de
tâche, sans conflit entre les sessions. Les journaux et compteurs sont stockés
dans Redis (ou en mémoire en mode `CELERY_TASK_ALWAYS_EAGER`).
