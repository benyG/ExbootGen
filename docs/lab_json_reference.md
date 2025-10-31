# Référence JSON des labs Hands-on

Ce document décrit la structure standardisée des fichiers JSON utilisés par le player Hands-on Labs. Le schéma ci-dessous s'applique à tout lab et garantit une expérience cohérente lors de la génération automatique de scénarios.

## Structure générale

```json
{
  "schema_version": "0.2.0",
  "lab": {
    "id": "identifiant-unique",
    "title": "Titre affiché",
    "subtitle": "Sous-titre optionnel",
    "scenario_md": "Description Markdown en 2 à 3 paragraphes",
    "variables": { ... },
    "scoring": { "max_points": 100 },
    "timer": { "mode": "countdown", "seconds": 1800 },
    "assets": [ ... ],
    "steps": [ ... ]
  }
}
```

- `scenario_md` (Markdown) fournit le contexte narratif qui sera affiché dans le bandeau supérieur du player.
- `variables` permet de tirer aléatoirement des valeurs pour personnaliser le scénario (type `choice`, `number`, etc.). Les expressions `{{variable}}` peuvent être utilisées dans les instructions et validateurs.
- `assets` contient les fichiers mis à disposition des apprenants (base64 inline ou URL distante). Chaque asset expose au minimum `id`, `kind`, `mime` et, si inline, `content_b64`.
- `steps` décrit la séquence pédagogique. Chaque étape doit préciser au moins `id`, `type`, `title`, `instructions_md`, `points`, `hints` optionnels et `transitions` (`on_success` obligatoire, `on_failure` optionnel).

## Types d'étapes

### 1. `terminal`

Simule l'exécution d'une commande en CLI. La structure minimale :

```json
{
  "type": "terminal",
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
            { "flag": "--Profile", "expect": "Domain,Private,Public" }
          ]
        },
        "response": {
          "stdout_template": "...",
          "world_patch": [ { "op": "set", "path": "windows.firewall.enabled", "value": true } ]
        }
      }
    ]
  }
}
```

La commande validée peut appliquer des patchs sur l'`état monde` (structure JSON persistée entre les étapes).

### 2. `console_form`

Représente une interface graphique simulée. La propriété `form.schema.fields` décrit chaque champ :

- `widget: "toggle"` crée un bouton cyclant sur les valeurs `options`. Aucun choix n'est présélectionné tant que l'utilisateur n'interagit pas.
- Les champs simples utilisent la classe `input` et supportent `placeholder`.

Les validateurs s'exécutent après la sauvegarde et peuvent inspecter le sous-objet (`payload`) ou l'état monde complet.

### 3. `inspect_file`

Expose un fichier téléchargeable (liens générés automatiquement). L'attribut `input.mode` détermine le comportement :

- `"editor"` (défaut) pré-remplit la zone avec le contenu du fichier pour modification.
- `"answer"` affiche un champ de réponse libre accompagné d'un prompt Markdown.

Les validateurs reçoivent `payload` (string ou JSON) et peuvent utiliser `jsonschema`, `jsonpath_absent`, ou des `expression` JavaScript (sécurisées via `evalExpr`).

### 4. `architecture`

Offre un espace de travail de type "mini Packet Tracer" basé sur Konva. Deux modes :

- **Libre** (`freeform`, valeur par défaut) pour déplacer des noeuds, les relier et saisir des commandes par composant.
- **Slots** (`slots`) pour déposer des éléments sur des emplacements prédéfinis.

Champs principaux :

- `palette` liste les composants disponibles (`id`, `label`, `icon` texte ou image, `tags`). Au moins un composant doit être marqué `is_decoy: true` pour servir de leurre.
- `initial_nodes` instancie les composants présents au chargement (position, alias, etc.).
- `expected_world` décrit la solution attendue.
  - `nodes`: règles de correspondance (`match`) avec `label`, `palette_id`, `config_contains`, `commands`, `tags`, etc.
  - `links`: connexions obligatoires, avec support du flag `bidirectional`.
  - `allow_extra_nodes`: à `false` pour exiger une solution unique.
- Les validateurs peuvent accéder au `payload` (noeuds, liens, configs, résumé) et à l'état monde projeté.

L'interface propose désormais :

- Un bouton "Créer un lien" dans l'inspecteur.
- Un aperçu dynamique des liaisons en cours.
- La saisie des commandes associées à chaque composant, stockées dans `payload.nodes[].config` et `commands`.

### 5. `quiz`

Questions à choix multiples ou unique :

```json
{
  "type": "quiz",
  "question_md": "Quel outil ... ?",
  "choices": [ { "id": "a", "text": "..." } ],
  "correct": ["a"],
  "points": 10
}
```

Le player exige qu'au moins une réponse figure dans `correct`.

## Transitions

Chaque étape définit `transitions.on_success` (prochaine étape ou `#end`). `on_failure` permet de rester sur place (`#stay`) ou de bifurquer.

## Scoring et temps

- `scoring.max_points` doit correspondre à la somme des `points` de chaque étape.
- `timer.mode` accepte `countdown` (séance limitée) ou peut être omis.

## Bonnes pratiques

1. **Contexte immersif** : toujours fournir un `scenario_md` de 2-3 paragraphes.
2. **Décoy palette** : inclure un composant inutile (`is_decoy: true`) dans chaque étape `architecture`.
3. **Validations strictes** : utiliser `expected_world` ou des validateurs `expression` pour limiter les solutions acceptées.
4. **Actifs documentés** : préciser `filename` et `mime` pour faciliter le téléchargement des fichiers.
5. **Guidage** : renseigner `hints` pour toutes les étapes afin de faciliter la découverte.

Ce référentiel sert de base pour générer automatiquement de nouveaux labs compatibles avec le player Hands-on Labs.
