# ExbootGen

## Configuration des variables d'environnement

L'application lit plusieurs variables d'environnement pour configurer l'accès à l'API OpenAI et contrôler le taux d'envoi des requêtes. Les variables principales sont :

- `OPENAI_API_KEY` : clé API OpenAI (obligatoire)
- `OPENAI_API_URL` : URL de l'endpoint Chat Completions (optionnel)
- `OPENAI_MAX_RETRIES` : nombre maximal de tentatives en cas d'échec (optionnel)
- `API_REQUEST_DELAY` : délai entre deux requêtes lors de la population (optionnel)

### Sous Windows – PowerShell
Pour définir des variables pour la session en cours :

```powershell
$env:OPENAI_API_KEY = "sk-votre-cle"
$env:OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
$env:OPENAI_MAX_RETRIES = "5"
$env:API_REQUEST_DELAY = "1"
```

Pour enregistrer ces variables de manière permanente :

```powershell
setx OPENAI_API_KEY "sk-votre-cle"
setx OPENAI_API_URL "https://api.openai.com/v1/chat/completions"
setx OPENAI_MAX_RETRIES "5"
setx API_REQUEST_DELAY "1"
```

### Sous Windows – Invite de commandes (cmd)
Pour la session uniquement :

```cmd
set OPENAI_API_KEY=sk-votre-cle
set OPENAI_API_URL=https://api.openai.com/v1/chat/completions
set OPENAI_MAX_RETRIES=5
set API_REQUEST_DELAY=1
```

Pour une configuration persistante :

```cmd
setx OPENAI_API_KEY "sk-votre-cle"
setx OPENAI_API_URL "https://api.openai.com/v1/chat/completions"
setx OPENAI_MAX_RETRIES "5"
setx API_REQUEST_DELAY "1"
```

Après avoir défini les variables avec `setx`, redémarrez votre terminal pour qu'elles soient prises en compte.
