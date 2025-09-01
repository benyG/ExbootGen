# ExbootGen

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
```

Après avoir défini les variables avec `setx`, redémarrez votre terminal pour qu'elles soient prises en compte.
