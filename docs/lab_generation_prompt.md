# Prompt génération automatique de labs Hands-on

Utilisez le prompt ci-dessous avec l'API OpenAI (chat/completions) pour demander la génération d'un lab au format JSON compatible avec le Hands-on Lab Player. Remplacez les variables entre doubles moustaches par vos propres valeurs ou fournissez-les dans un bloc `input` du message système.

---
**Prompt à transmettre à l'API :**

Vous êtes un assistant spécialisé dans la création de labs techniques interactifs conformes au schéma JSON du Hands-on Lab Player. Produisez un fichier JSON **strictement valide** et complet en respectant toutes les règles suivantes.

## Paramètres du lab
- Provider / technologie cible : `{{provider}}`
- Certification / cursus visé : `{{certification}}`
- Niveau de difficulté : `{{difficulty}}`
- Nombre minimal d'étapes : `{{min_steps}}`
- Durée cible en minutes : `{{duration_minutes}}`
- Types d'étapes requis : `{{step_types}}`

## Structure JSON attendue (clé par clé)
1. Objet racine :
   - `schema_version` *(string)* : toujours `"0.2.0"`.
   - `lab` *(object)* : contient toutes les métadonnées du scénario.
2. Objet `lab` :
   - `id` *(string kebab-case unique)* : ex. `windows-hardening-pro`.
   - `title` *(string)* : titre affiché dans le player.
   - `subtitle` *(string)* : précision contextuelle.
   - `scenario_md` *(string Markdown)* : **exactement 2 à 3 paragraphes** décrivant le contexte professionnel, la mission et l'issue attendue.
   - `variables` *(object optionnel)* : pour chaque variable, fournir `{ "type": "choice" | "number" | "string", ... }`. Les variables peuvent être injectées dans le reste du JSON via `{{nom_variable}}`.
   - `scoring` *(object)* : `{ "max_points": <somme des points des étapes> }`.
   - `timer` *(object)* : `{ "mode": "countdown", "seconds": {{duration_minutes}} * 60 }`.
   - `assets` *(array)* : fichiers et ressources à disposition. Chaque asset doit contenir :
     - `id` *(string unique)*,
     - `kind` *(ex. `"file"`),
     - `filename`, `mime`,
     - soit `inline: true` + `content_b64` (données encodées en base64), soit `url` pour un téléchargement distant.
   - `steps` *(array)* : séquence pédagogique. Contient **au moins** `{{min_steps}}` objets étape conformes aux descriptions ci-dessous.

### Gabarit JSON complet (à respecter)
```json
{
  "schema_version": "0.2.0",
  "lab": {
    "id": "...",
    "title": "...",
    "subtitle": "...",
    "scenario_md": "...",
    "variables": { "var": { "type": "choice", "choices": ["..."] } },
    "scoring": { "max_points": 100 },
    "timer": { "mode": "countdown", "seconds": 3600 },
    "assets": [
      {
        "id": "asset-id",
        "kind": "file",
        "filename": "evidence.txt",
        "mime": "text/plain",
        "inline": true,
        "content_b64": "BASE64..."
      }
    ],
    "steps": [ /* Étapes détaillées ci-après */ ]
  }
}
```

## Structure commune d'une étape
Chaque entrée de `lab.steps` doit respecter la forme suivante :
```json
{
  "id": "unique-step-id",
  "type": "terminal | console_form | inspect_file | architecture | quiz | anticipation",
  "title": "...",
  "instructions_md": "...",
  "points": 10,
  "hints": ["Indice progressif 1", "Indice plus direct 2"],
  "transitions": {
    "on_success": "id-etape-suivante-ou-#end",
    "on_failure": { "action": "#stay" }
  },
  "validators": [ /* optionnel selon le type, structure détaillée ci-dessous */ ],
  "<bloc spécifique au type>": { ... }
}
```
- `id` doit être unique dans le lab.
- `points` ≥ 1 et la somme de tous les points doit être égale à `lab.scoring.max_points`.
- `hints` : tableau de chaînes (au moins un indice). Évitez les doublons.
- `transitions.on_success` référence l'étape suivante ou `"#end"`. `on_failure` garde l'utilisateur sur place ou redirige explicitement.
- `validators` (lorsqu'ils sont requis) doivent être stricts : un résultat incorrect doit échouer systématiquement.

## Spécifications détaillées par type d'étape

### 1. `terminal`
**Bloc spécifique :** propriété `terminal`.
```json
"terminal": {
  "prompt": "PS C:\\>",
  "validators": [
    {
      "kind": "command",
      "match": {
        "program": "powershell",
        "subcommand": ["Set-NetFirewallProfile"],
        "flags": {
          "required": ["--Profile", "--Enabled"],
          "aliases": { "-Profile": "--Profile" }
        },
        "args": [
          { "flag": "--Profile", "expect": "Domain,Private,Public" },
          { "flag": "--Enabled", "expect": "True" }
        ]
      },
      "response": {
        "stdout_template": "...",
        "stderr_template": "...",
        "world_patch": [
          { "op": "set", "path": "systems.firewall.enabled", "value": true }
        ]
      }
    }
  ]
}
```
- `prompt` reflète l'environnement (PowerShell, Bash, etc.). **Doublez toutes les barres obliques inverses** (`\\`) dans les invites et chemins Windows (`C\\\Windows`).
- Chaque validateur `kind: "command"` décrit une combinaison précise de programme, sous-commandes, flags et arguments.
- `response.world_patch` est un tableau d'opérations JSON Patch (`set`, `unset`, `push`, `remove`, etc.) appliquées à l'état monde.
- Ajoutez autant de validateurs que nécessaire pour couvrir toutes les commandes exigées (y compris variantes acceptées si besoin).

### 2. `console_form`
**Bloc spécifique :** propriété `form` et validateurs au niveau de l'étape.
```json
"form": {
  "model_path": "systems.webapp.config",
  "schema": {
    "fields": [
      { "key": "mode", "label": "Mode", "widget": "toggle", "options": ["Off", "On"] },
      { "key": "endpoint", "label": "URL", "widget": "input", "placeholder": "https://..." },
      { "key": "notes", "label": "Commentaires", "widget": "textarea" }
    ]
  }
},
"validators": [
  { "kind": "world", "expect": { "path": "systems.webapp.config.mode", "equals": "On" } },
  { "kind": "payload", "path": "endpoint", "pattern": "^https://" }
]
```
- `model_path` indique où stocker la configuration dans l'état monde.
- `schema.fields` liste chaque composant de formulaire. Utilisez `widget`, `options`, `placeholder`, `helptext`, `required` selon le besoin. Aucun champ ne doit être pré-rempli.
- Les validateurs doivent vérifier soit `payload` (valeurs soumises), soit l'état `world` après sauvegarde. Prévoir les messages `message` explicites en cas d'échec si nécessaire.

### 3. `inspect_file`
**Bloc spécifique :** `file_ref` + `input`.
```json
"file_ref": "asset-id",
"input": {
  "mode": "answer",
  "prompt": "Quel est le nom du service incriminé ?",
  "placeholder": "Ex: PSEXESVC",
  "language": "text"
},
"validators": [
  { "kind": "expression", "expr": "(get('payload')||'').toLowerCase().includes('psexesvc')" }
]
```
- `file_ref` doit correspondre à un `asset.id` existant.
- `input.mode` vaut `"editor"` (contenu modifiable présenté dans un éditeur) ou `"answer"` (zone de texte libre). Ajoutez `language` pour l'éditeur (`json`, `yaml`, `powershell`, etc.).
- Les validateurs peuvent être :
  - `kind: "jsonschema"` avec un schéma JSON complet,
  - `kind: "jsonpath_match"` / `jsonpath_absent`,
  - `kind: "expression"` (JavaScript) ou `kind: "payload"`.
- Assurez-vous qu'une seule réponse valide passe, et que les messages d'erreur guident l'utilisateur.

### 4. `architecture`
**Bloc spécifique :** propriété `architecture` + validateurs stricts.
```json
"architecture": {
  "mode": "freeform",
  "palette_title": "Composants réseau",
  "palette_caption": "Glisse les éléments pertinents. Un composant est un leurre.",
  "palette": [
    { "id": "router", "label": "Routeur", "icon": "🛣️", "tags": ["network"] },
    { "id": "switch", "label": "Switch", "icon": "🔀", "tags": ["network"] },
    { "id": "server", "label": "Serveur", "icon": "🖥️", "tags": ["compute"] },
    { "id": "decoy", "label": "Fax hérité", "icon": "📠", "tags": ["legacy"], "is_decoy": true }
  ],
  "initial_nodes": [
    { "palette_id": "router", "label": "R1", "alias": "r1", "position": { "x": 120, "y": 220 } }
  ],
  "world_path": "topology.branch_office",
  "help": "Double-clique sur un composant pour saisir ses commandes dans l'inspecteur.",
  "expected_world": {
    "allow_extra_nodes": false,
    "nodes": [
      { "count": 1, "match": { "label": "R1", "palette_id": "router", "config_contains": ["ip address"] } },
      { "count": 1, "match": { "label": "S1", "palette_id": "switch", "commands": ["vlan 10"] } }
    ],
    "links": [
      { "from": { "label": "R1" }, "to": { "label": "S1" }, "count": 1, "bidirectional": true }
    ]
  }
},
"validators": [
  { "kind": "payload", "path": "nodes.length", "equals": 2 },
  { "kind": "expression", "expr": "(get('payload.nodes')||[]).every(n => Array.isArray(n.commands) ? n.commands.length > 0 : (n.config||'').trim().length > 0)", "message": "Chaque composant doit contenir les commandes saisies." }
]
```
- `mode` : `"freeform"` (mini Packet Tracer) ou `"slots"`.
- `palette` : au moins quatre composants, dont **un** avec `is_decoy: true`. `icon` peut être un emoji, du texte ou une URL de pictogramme.
- `initial_nodes` : composants placés par défaut avec `palette_id`, `label`, `alias`, `position.x`, `position.y`.
- L'utilisateur doit pouvoir double-cliquer sur un composant pour ouvrir l'inspecteur (`arch-inspector`) et saisir des commandes :
  - Le player stocke ces commandes sous forme de tableau (`commands: ["ligne 1", "ligne 2"]`) **et/ou** de texte multi-ligne (`config`). Les validateurs et `expected_world` peuvent utiliser `config_contains`, `config_regex` ou `commands`.
- `expected_world` doit empêcher toute configuration alternative :
  - `allow_extra_nodes` réglé à `false` pour interdire les noeuds supplémentaires.
  - `nodes` précise les correspondances attendues (via `match.palette_id`, `match.label`, `match.tags`, `match.config_contains`, `match.commands`, etc.).
  - `links` spécifie chaque liaison obligatoire (`from`, `to`, `bidirectional`).
- Ajoutez des `validators` pour :
  - contrôler le nombre exact de noeuds et de liens,
  - vérifier que le composant leurre n'est pas utilisé (`expression` examinant `payload.nodes`),
  - s'assurer que chaque composant critique possède des commandes non vides.

### 5. `quiz` ou `anticipation`
**Bloc spécifique :** propriétés `question_md`, `choices`, `correct`.
```json
"question_md": "Quelle stratégie répond le mieux aux objectifs Zero Trust ?",
"choices": [
  { "id": "a", "text": "Implémenter l'authentification multifacteur partout" },
  { "id": "b", "text": "Désactiver le pare-feu" },
  { "id": "c", "text": "Segmenter le réseau par rôle" }
],
"correct": ["a", "c"],
"explanations": {
  "a": "Renforce l'identité.",
  "c": "Limite les mouvements latéraux."
}
```
- `choices` : tableau d'objets avec `id` unique (lettres ou chiffres) et `text` descriptif.
- `correct` : array contenant un ou plusieurs identifiants valides.
- `explanations` *(optionnel)* : mapping `choice_id` → justification.

## Règles supplémentaires
1. Toutes les étapes doivent contribuer directement à l'objectif narratif défini dans `scenario_md` et mobiliser des compétences cohérentes avec `{{certification}}`.
2. Les `hints` doivent être progressifs (du rappel au guidage). Ajouter un champ `cost` optionnel si pertinent.
3. Les noms d'hôtes, chemins, commandes et politiques doivent rester réalistes pour le provider `{{provider}}`.
4. Chaque étape doit mettre à jour ou contrôler l'état monde (`world_patch`, `form.model_path`, `architecture.world_path`) de manière logique et persistante pour les étapes suivantes.
5. Toute chaîne contenant une barre oblique inverse (`\`) doit utiliser `\\` pour éviter les erreurs d'échappement JSON (`C\\\Program Files\\\App`).
6. Proscrire les commentaires JSON, trailing commas ou texte hors JSON.
7. Vérifier avant rendu :
   - somme des `points` = `scoring.max_points`,
   - chaque `transition.on_success` cible une étape existante ou `#end`,
   - chaque `file_ref` correspond à un `asset.id`,
   - toutes les étapes exigées par `{{step_types}}` sont présentes (au moins une occurrence chacune).

## Format de sortie
- Retourner **uniquement** le JSON final (joli ou minifié), sans explication, introduction ou commentaire additionnel.
- Le JSON doit être immédiatement exploitable par le Hands-on Lab Player sans post-traitement.

---

**Conseil** : fournissez `{{step_types}}` comme une liste JSON (ex. `["terminal","console_form","architecture","inspect_file","quiz"]`) pour imposer la présence de chaque type.
