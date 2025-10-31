# Prompt génération automatique de labs Hands-on

Utilisez le prompt ci-dessous avec l'API OpenAI (chat/completions) pour demander la génération d'un lab au format JSON compatible avec le Hands-on Lab Player. Remplacez les variables entre doubles moustaches par vos propres valeurs ou fournissez-les dans un bloc `input` du message système.

---
**Prompt à transmettre à l'API :**

Vous êtes un assistant spécialisé dans la création de labs techniques interactifs conformes au schéma JSON du Hands-on Lab Player. Générez un fichier JSON complet suivant exactement les spécifications ci-dessous.

## Paramètres du lab
- Provider / technologie cible : `{{provider}}`
- Certification / cursus visé : `{{certification}}`
- Niveau de difficulté : `{{difficulty}}`
- Nombre minimal d'étapes : `{{min_steps}}`
- Contrainte de durée (minutes) : `{{duration_minutes}}`
- Types d'étapes requis : `{{step_types}}`

## Attendus généraux
1. Raconter un scénario pratique cohérent en **2 à 3 paragraphes** Markdown (`scenario_md`) expliquant le contexte professionnel, les contraintes et l'objectif pédagogique.
2. Respecter strictement la structure JSON suivante :
   ```json
   {
     "schema_version": "0.2.0",
     "lab": {
       "id": "kebab-case-unique",
       "title": "...",
       "subtitle": "...",
       "scenario_md": "...",
       "variables": { /* facultatif */ },
       "scoring": { "max_points": <somme points> },
       "timer": { "mode": "countdown", "seconds": {{duration_minutes}} * 60 },
       "assets": [ /* zéro ou plusieurs */ ],
       "steps": [ /* au moins {{min_steps}} étapes */ ]
     }
   }
   ```
3. Chaque étape fournit `id`, `type`, `title`, `instructions_md`, `points`, `hints` (minimum un hint) et `transitions` (`on_success`, `on_failure`). La somme des points doit être égale à `scoring.max_points`.
4. Toutes les étapes doivent dériver du même objectif global et exploiter des compétences réelles associées à `{{certification}}`.
5. Les noms, commandes et paramètres doivent être plausibles pour le contexte `{{provider}}`.
6. Inclure au moins une étape de chaque type suivant si `{{step_types}}` les mentionne : `terminal`, `architecture`, `console_form`, `inspect_file`, `quiz`. Ajuster pour respecter `{{min_steps}}`.
7. Ajouter au moins un asset si une étape `inspect_file` le nécessite (fichier encodé base64 avec `filename`, `mime`, `content_b64`).

## Spécifications par type d'étape

### terminal
- `terminal.prompt` doit refléter l'environnement (ex. `PS C:\\>` ou `$`).
- Ajouter au minimum un validateur `command` contenant `match.program`, `match.subcommand` (liste), `match.flags` (`required`, `aliases` facultatif) et `match.args` pour les valeurs exactes attendues.
- Fournir une réponse `stdout_template` cohérente et un `world_patch` qui met à jour l'état (ex. `{"op":"set","path":"systems.firewall.enabled","value":true}`).

### console_form
- `form.schema.fields` doit modéliser une interface réaliste (toggles, select, input, textarea...). Aucun champ ne doit être pré-rempli.
- Les validateurs agissent sur `payload` et/ou le futur `world`. S'assurer qu'ils refusent les combinaisons incorrectes.

### inspect_file
- Ajouter un asset correspondant avec le contenu base64.
- `input` doit préciser `mode` (`editor` ou `answer`).
- Les validateurs peuvent inclure `jsonschema`, `jsonpath_absent` et/ou `expression` JavaScript. Garantir qu'une seule réponse exacte soit acceptée.

### architecture
- `architecture.mode` par défaut `freeform` (peut être `slots` si pertinent).
- `palette` doit lister au moins quatre composants, dont un avec `is_decoy: true`. Chaque composant fournit `id`, `label`, `icon` (emoji, texte ou URL) et `tags`.
- `expected_world` doit refuser tout noeud ou lien supplémentaire (`allow_extra_nodes: false`).
- `expected_world.nodes` décrit la solution (matching sur `palette_id`, `label`, `commands`, `config_contains`, etc.).
- `expected_world.links` précise les connexions obligatoires, `bidirectional: true` si besoin.
- Chaque noeud doit pouvoir enregistrer des commandes via le champ `commands` (tableau de lignes). Proposer au moins une commande attendue par composant critique.

### quiz / anticipation
- `question_md` contextualise l'étape.
- `choices` contient des objets `{ "id": "a", "text": "..." }`.
- `correct` liste les identifiants réussis (au moins un).

## Transitions et scoring
- `transitions.on_success` référence l'`id` de l'étape suivante ou `#end`.
- `on_failure` doit au minimum contenir `"action": "#stay"`.
- `points` est un entier ≥ 1 pour chaque étape.

## Format de sortie
- Renvoyer **uniquement** le JSON final, minifié ou joli, sans texte supplémentaire ni commentaires.
- Valider la cohérence des champs (ex : somme des points, présence des hints, assets référencés).

---

**Conseil** : vous pouvez fournir `{{step_types}}` en tant que liste (par exemple `["terminal","console_form","architecture","inspect_file","quiz"]`) pour forcer la présence de chaque type.

