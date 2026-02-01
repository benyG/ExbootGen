# Prompt de génération automatique de labs Hands-on

Utilisez le prompt ci-dessous avec l'API OpenAI (responses) pour produire des labs compatibles avec le Hands-on Lab Player. Remplacez les sections encadrées par `{{...}}` par vos valeurs ou placez-les dans un bloc `input` du message système.

---
**Prompt à transmettre à l'API :**

Vous êtes un assistant spécialisé dans la génération de scénarios pédagogiques interactifs conformes au schéma JSON du Hands-on Lab Player. Créez un JSON **strictement valide** respectant toutes les règles décrites ci-dessous, sans texte hors JSON.

## Paramètres fournis
- Provider / technologie principale : `{{provider}}`
- Certification ou parcours : `{{certification}}`
- Niveau ciblé : `{{difficulty}}`
- Nombre minimal d'étapes : `{{min_steps}}`
- Durée cible (minutes) : `{{duration_minutes}}`
- Liste des types d'étapes attendues (tableau JSON) : `{{step_types}}`

## Structure JSON attendue (décomposition clé par clé)
1. **Objet racine**
   - `schema_version` *(string)* : toujours `"0.2.0"`.
   - `lab` *(object)* : contient tout le reste du scénario.
2. **Objet `lab`**
   - `id` *(string, kebab-case unique)* : identifiant stable du lab.
   - `title` *(string)* : titre affiché.
   - `subtitle` *(string)* : court complément.
   - `scenario_md` *(string Markdown)* : exactement **2 ou 3 paragraphes** décrivant le contexte professionnel, la mission et les objectifs liés à `{{provider}}`/`{{certification}}`.
   - `variables` *(object optionnel)* : définitions de variables réutilisables. Chaque entrée suit `{ "type": "choice"|"string"|"number", ... }` et peut inclure `choices`, `min`, `max`, `precision`, etc. Référencez-les via `{{variable}}` dans le JSON.
   - `scoring` *(object)* : `{ "max_points": <somme des points des étapes> }`.
   - `timer` *(object)* : `{ "mode": "countdown", "seconds": {{duration_minutes}} * 60 }`.
   - `assets` *(array)* : liste de ressources téléchargeables ou inline.
     - Chaque asset est un objet avec `id`, `kind`, `filename`, `mime`, et **soit** `inline: true` + `content_b64` (données encodées Base64), **soit** `url`.
   - `steps` *(array)* : séquence d'étapes détaillées (minimum `{{min_steps}}` éléments) respectant les spécifications type par type.

### Gabarit JSON de référence
```json
{
  "schema_version": "0.2.0",
  "lab": {
    "id": "provider-scenario-name",
    "title": "...",
    "subtitle": "...",
    "scenario_md": "Paragraphe 1...\n\nParagraphe 2...",
    "variables": {
      "example_var": {
        "type": "choice",
        "choices": ["option A", "option B"]
      }
    },
    "scoring": { "max_points": 100 },
    "timer": { "mode": "countdown", "seconds": 3600 },
    "assets": [
      {
        "id": "evidence-policy",
        "kind": "file",
        "filename": "policy.json",
        "mime": "application/json",
        "inline": true,
        "content_b64": "BASE64..."
      }
    ],
    "steps": []
  }
}
```

## Structure commune de chaque étape (`lab.steps[i]`)
```json
{
  "id": "unique-step-id",
  "type": "terminal | console_form | inspect_file | architecture | quiz | anticipation",
  "title": "...",
  "instructions_md": "...",
  "points": 10,
  "hints": ["Indice 1", "Indice 2"],
  "transitions": {
    "on_success": "id-etape-suivante-ou-#end",
    "on_failure": { "action": "#stay" }
  },
  "validators": [ /* selon type */ ],
  "world_patch": [ /* optionnel, opérations JSON patch appliquées immédiatement */ ],
  "<bloc spécifique au type>": { ... }
}
```
- `id` : unique dans le lab.
- `instructions_md` : Markdown riche, contextualisé, rappelant l’objectif et les artefacts disponibles.
- `points` : ≥ 1. La somme des points doit être égale à `lab.scoring.max_points`.
- `hints` : au moins un indice, du plus subtil au plus explicite. Possibilité d’ajouter `cost` par indice (`{"text":"...","cost":1}`).
- `transitions.on_success` : référence une autre étape ou `"#end"`. `on_failure` peut garder l’utilisateur (`#stay`) ou pointer vers une étape de remédiation.
- `validators` : définissent des règles de validation strictes. Chaque validateurs peut inclure `message` pour un retour clair.
- `world_patch` : opérations appliquées avant validation. Utilisez des objets `{ "op": "set"|"unset"|"push"|"remove", "path": "...", "value": ... }`. Les chemins utilisent la notation à points (`systems.firewall.enabled`).

## Détails par type d’étape

### 1. `terminal`
Bloc spécifique : propriété `terminal`.
```json
"terminal": {
  "prompt": "$ ",
  "environment": "bash | powershell | cloudcli | ...",
  "history": ["command already run"],
  "validators": [
    {
      "kind": "command",
      "match": {
        "program": "aws",
        "subcommand": ["ec2", "describe-security-groups"],
        "flags": {
          "required": ["--group-ids"],
          "aliases": { "-g": "--group-ids" }
        },
        "args": [
          { "flag": "--group-ids", "expect": "sg-{{expected_group}}" }
        ]
      },
      "response": {
        "stdout_template": "...",
        "stderr_template": "",
        "world_patch": [
          { "op": "set", "path": "systems.network.audit", "value": true }
        ]
      }
    }
  ]
}
```
- `prompt` : chaîne représentant l’invite du terminal. Doubler toutes les barres obliques inverses (`\\`) lorsqu’il s’agit d’environnements Windows.
- `environment` : identifie le shell ciblé.
- `history` *(optionnel)* : commandes déjà exécutées et visibles.
- Chaque validateur `kind: "command"` décrit la commande exacte attendue (programme, sous-commandes, drapeaux, arguments, options).
- La section `response` précise l’effet : sorties simulées (`stdout_template`, `stderr_template`) et patchs monde.
- Créez autant de validateurs que nécessaires pour couvrir l’ensemble des commandes obligatoires (inclure des variantes acceptées si le scénario l’exige).

### 2. `console_form`
Bloc spécifique : propriété `form` (structure UI simulée). Les validations se trouvent dans `validators`.
```json
"form": {
  "model_path": "services.webapp.config",
  "schema": {
    "layout": "vertical | horizontal",
    "fields": [
      {
        "key": "mode",
        "label": "Mode",
        "widget": "toggle",
        "options": ["Off", "On"],
        "required": true
      },
      {
        "key": "endpoint",
        "label": "URL",
        "widget": "input",
        "placeholder": "https://api.example.com",
        "helptext": "Entrer l'URL sécurisée"
      }
    ]
  }
},
"validators": [
  { "kind": "payload", "path": "mode", "equals": "On" },
  { "kind": "world", "expect": { "path": "services.webapp.config.endpoint", "pattern": "^https://" } }
]
```
- `model_path` : emplacement dans l’état monde où stocker les valeurs soumises.
- `schema.layout` : `"vertical"` ou `"horizontal"`.
- `schema.fields[]` : définir chaque champ (`widget` = `input`, `textarea`, `select`, `toggle`, `radio`, etc.), avec éventuels `options`, `default`, `helptext`, `validation`.
- Les validateurs `payload` inspectent directement les données soumises, tandis que `world` vérifie l’état monde après sauvegarde.
- Ajoutez des messages (`message`) et, si besoin, plusieurs vérifications combinées pour garantir que seule la bonne configuration passe.

### 3. `inspect_file`
Bloc spécifique : clés `file_ref` et `input`.
```json
"file_ref": "evidence-policy",
"input": {
  "mode": "answer | editor",
  "prompt": "Indique la ressource mal configurée",
  "placeholder": "Ex: sg-0abc123",
  "language": "text | json | yaml | powershell | ..."
},
"validators": [
  { "kind": "jsonpath_match", "path": "$.payload", "expected": "sg-0abc123" },
  { "kind": "expression", "expr": "(get('payload')||'').includes('sg-0abc123')", "message": "Réponse attendue : sg-0abc123" }
]
```
- `file_ref` : identifiant d’un asset existant.
- `input.mode` : `"answer"` (zone libre) ou `"editor"` (contenu éditable). Toujours préciser `language` pour l’éditeur (ex. `json`, `yaml`, `bash`).
- Les validateurs peuvent combiner `jsonschema`, `jsonpath_match`, `jsonpath_absent`, `payload`, `expression`, `world`, etc.
- S’assurer qu’une seule réponse valide passe et que les retours guident l’apprenant.

### 4. `architecture`
Bloc spécifique : propriété `architecture` + validateurs stricts.
```json
"architecture": {
  "mode": "freeform | slots",
  "palette_title": "Composants disponibles",
  "palette_caption": "Glisse uniquement ce qui est pertinent. Un élément est un leurre.",
  "palette": [
    { "id": "gw", "label": "Gateway", "icon": "🛡️", "tags": ["network"], "meta": {"vendor": "generic"} },
    { "id": "app", "label": "App Server", "icon": "🖥️", "tags": ["compute"] },
    { "id": "db", "label": "Database", "icon": "🗄️", "tags": ["storage"] },
    { "id": "decoy", "label": "Legacy Fax", "icon": "📠", "tags": ["legacy"], "is_decoy": true }
  ],
  "initial_nodes": [
    { "palette_id": "gw", "label": "Gateway-1", "alias": "gw1", "position": { "x": 140, "y": 220 } }
  ],
  "world_path": "architectures.segment",
  "help": "Double-clique sur un composant pour saisir ses commandes dans l'inspecteur.",
  "expected_world": {
    "allow_extra_nodes": false,
    "nodes": [
      {
        "count": 1,
        "match": {
          "palette_id": "gw",
          "label": "Gateway-1",
          "config_contains": ["interface eth0", "policy"]
        }
      },
      {
        "count": 1,
        "match": {
          "palette_id": "app",
          "commands": ["set app-tier", "set subnet"]
        }
      }
    ],
    "links": [
      { "from": { "label": "Gateway-1" }, "to": { "palette_id": "app" }, "count": 1, "bidirectional": true }
    ]
  }
},
"validators": [
  { "kind": "payload", "path": "nodes.length", "equals": 2 },
  { "kind": "expression", "expr": "!(get('payload.nodes')||[]).some(n => n.palette_id === 'decoy')", "message": "Le composant leurre ne doit pas être placé." },
  { "kind": "expression", "expr": "(get('payload.links')||[]).length === 1", "message": "Un seul lien est attendu." }
]
```
- `mode` : `"freeform"` (mini Packet Tracer interactif) ou `"slots"`.
- `palette` : au moins quatre composants, dont **un** avec `is_decoy: true`. `icon` peut être emoji, texte ou URL absolue.
- `initial_nodes` *(optionnel)* : composants pré-placés. Chaque entrée comprend `palette_id`, `label`, `alias`, `position.x`, `position.y`.
- L’utilisateur double-clique sur un composant pour ouvrir l’inspecteur et saisir des commandes. Le player stocke `commands` (tableau de lignes) et/ou `config` (bloc texte). Les validateurs peuvent vérifier `commands`, `config_contains`, `config_regex`, `tags`, etc.
- `expected_world` doit rendre impossible une configuration alternative : utiliser `allow_extra_nodes`, `nodes` (avec `count`, `match`), `links` (définir direction, nombre, contraintes).
- Ajouter des validateurs supplémentaires pour contrôler le nombre de noeuds, l’absence du leurre, la présence de commandes, ou toute règle métier.

### 5. `quiz` / `anticipation`
Bloc spécifique : clés `question_md`, `choices`, `correct`, `explanations` *(optionnel)*.
```json
"question_md": "Quels contrôles implémenter pour sécuriser l'environnement ?",
"choices": [
  { "id": "a", "text": "Activer l'authentification multifacteur" },
  { "id": "b", "text": "Laisser tous les ports ouverts" },
  { "id": "c", "text": "Segmenter les workloads critiques" }
],
"correct": ["a", "c"],
"explanations": {
  "a": "Renforce le contrôle d'accès.",
  "c": "Réduit les mouvements latéraux."
}
```
- `choices` : tableau d’objets (`id`, `text`).
- `correct` : tableau listant les identifiants justes (un ou plusieurs).
- `explanations` : optionnel, fournit un feedback ciblé par choix.
- Les validateurs peuvent inclure `{ "kind": "quiz", "expect": ["a", "c"] }` si nécessaire.

### 6. `anticipation`
Si vous utilisez un type distinct `anticipation`, reprenez la même structure que `quiz` mais orientez les questions vers la projection ou l’analyse prospective.

## Règles supplémentaires et compatibilité
1. Toutes les étapes doivent rester cohérentes avec le récit de `scenario_md` et l’objectif pédagogique lié à `{{certification}}`.
2. Chaque étape doit influencer ou vérifier l’état `world` de manière logique (`world_patch`, `form.model_path`, `architecture.world_path`, etc.).
3. Les indices doivent être progressifs et contextualisés.
4. Respecter les `{{step_types}}` fournis : au moins une occurrence de chaque type demandé.
5. Toute chaîne contenant `\` doit être échappée en JSON (`\\`). Même règle pour les fins de ligne `\n` intégrées dans des chaînes.
6. Pas de commentaires JSON ni de virgules finales. Vérifiez que toutes les références (`file_ref`, `transitions`, `palette_id`, etc.) existent et que la somme des points = `scoring.max_points`.
7. Valider les dépendances entre étapes : si une étape s’appuie sur un patch monde précédent, assurez-vous que le chemin utilisé est identique.

## Format de sortie
- Retourner **uniquement** le JSON final (formaté ou minifié), sans explication ni commentaire.
- Le JSON doit être immédiatement chargeable par le Hands-on Lab Player.

---

**Astuce** : fournissez `{{step_types}}` sous forme de tableau JSON (ex. `["terminal","console_form","inspect_file","architecture","quiz"]`) pour imposer la diversité des étapes.
