# Refonte UI du lab player (sans changer le JSON de lab)

Objectif: améliorer **uniquement le rendu front** (UX/UI), en conservant le format JSON actuel et la logique métier existante.

---

## 1) Principes de refonte

- **Aucune évolution du schéma JSON**: on lit les mêmes champs (`title`, `instructions_md`, `steps`, etc.) et on retravaille seulement la présentation.
- **Priorité au rendu des étapes**: la progression doit être visible en un coup d’œil.
- **Interfaces orientées action**: l’apprenant doit toujours savoir:
  1) quoi faire,
  2) où agir,
  3) comment valider,
  4) où lire le feedback.

---

## 2) Nouveau rendu des étapes (applicable immédiatement)

## 2.1 Vue "Stepper" latérale + zone de travail

### Layout recommandé
- **Colonne gauche (28-32%)**: liste des étapes (stepper vertical).
- **Colonne droite (68-72%)**: détail de l’étape active (consigne + zone d’action + validation).

### États visuels des étapes
- `todo`: cercle gris + texte neutre.
- `active`: surbrillance + bordure accent + numéro visible.
- `done`: coche verte + étape repliée.
- `error`: icône alerte + message court sous le titre.
- `locked` (si applicable): icône cadenas léger.

### Densité d’information
- Dans la liste, n’afficher que:
  - numéro,
  - titre,
  - statut,
  - durée estimée (si déjà disponible).
- Le détail complet reste à droite.

---

## 2.2 Carte d’étape standardisée (même structure pour tous les types)

Pour chaque étape, utiliser toujours cette structure:

1. **Header**: `Étape X — {title}` + badge de type (`terminal`, `quiz`, etc.).
2. **Objectif**: 1 phrase claire (dérivée de `instructions_md`).
3. **Action attendue**: zone principale (terminal/form/drag&drop).
4. **Critères de validation**: petit encart fixe "Ce qui est vérifié".
5. **Feedback**: zone persistante sous le bouton de validation.

Effet: moins d’ambiguïté, meilleur rythme visuel, apprentissage plus fluide.

---

## 2.3 Actions primaires constantes (footer sticky)

Ajouter en bas de l’écran (ou du panneau droit) une barre fixe:

- Bouton principal: **Valider l’étape**
- Boutons secondaires: **Indice**, **Réinitialiser l’étape**, **Étape suivante**
- État de validation: loader / succès / erreur

Bénéfice: l’utilisateur ne "cherche" plus les actions.

---

## 3) Lisibilité de la consigne (sans toucher le contenu source)

## 3.1 Rendu markdown amélioré

Dans `instructions_md`, appliquer un styling typographique plus didactique:

- blocs `code` bien contrastés,
- lignes de commande copiables en 1 clic,
- éléments critiques (IP, ports, noms) en badges visuels,
- espacement vertical renforcé (lecture plus confortable).

## 3.2 Segmentation automatique légère

Sans modifier le JSON, parser l’instruction pour créer 3 sous-blocs visuels:

- **À faire**
- **À observer**
- **À valider**

Même si l’extraction est simple (heuristique), le gain UX est immédiat.

---

## 4) Immersion visuelle UI (toujours sans changer le JSON)

## 4.1 Bandeau de contexte

Afficher en haut:
- titre du lab,
- sous-titre,
- progression (`3/10`),
- timer (si présent),
- score courant (si présent).

Tout cela existe déjà dans les données, on améliore seulement la mise en scène.

## 4.2 Timeline compacte de progression

Sous le bandeau:
- mini timeline horizontale des étapes,
- point actif pulsé subtilement,
- étapes terminées en teinte validée.

Objectif: donner un sentiment d’avancement continu.

## 4.3 Micro-animations utiles

- transition douce quand on passe d’une étape à l’autre (150-220ms),
- animation de feedback succès/erreur,
- scroll automatique vers la zone d’erreur lors d’un échec.

Important: animation fonctionnelle uniquement (pas décorative).

---

## 5) Accessibilité et confort (impact fort, coût faible)

- Contraste WCAG AA minimum sur textes/boutons.
- Taille de police adaptable (S/M/L).
- Raccourcis clavier:
  - `Ctrl/Cmd + Enter` valider,
  - `[` / `]` étape précédente/suivante,
  - `H` afficher/masquer les indices.
- Focus visible sur tous les éléments interactifs.

---

## 6) Idées concrètes spécialement pour le rendu des étapes

1. **Step preview au survol**: survol d’une étape dans la sidebar => aperçu court de l’objectif.
2. **Repli auto des étapes validées**: réduit la charge cognitive.
3. **Sticky "erreur actuelle"**: si validation échoue, encart rouge persistant jusqu’à correction.
4. **Comparatif avant/après action**: pour certaines étapes, afficher un mini diff visuel de l’état.
5. **Mode "Focus étape"**: masque sidebar et éléments secondaires pendant l’exécution.
6. **Indicateur de difficulté par étape**: pastille simple (facile/moyen/avancé) calculée côté UI.
7. **Checkpoints visuels**: après X étapes, écran de respiration + résumé des acquis.

---

## 7) Plan de mise en œuvre (100% UI)

### Sprint 1 (rapide, 1-2 semaines)
- Stepper latéral avec états visuels.
- Carte d’étape standardisée.
- Footer sticky avec actions principales.
- Rendu markdown lisible (code, badges, spacing).

### Sprint 2 (2-3 semaines)
- Timeline compacte + transitions d’étapes.
- Gestion visuelle améliorée des erreurs/feedback.
- Mode focus + raccourcis clavier.

### Sprint 3 (3-4 semaines)
- Step preview, checkpoints, micro-interactions avancées.
- Ajustements accessibilité et personnalisation d’affichage.

---

## 8) KPI UI à suivre

- Temps moyen passé à trouver l’action suivante.
- Taux d’étapes validées sans seconde tentative.
- Nombre d’ouvertures d’indice par étape.
- Taux d’abandon en cours de lab.
- Score de satisfaction sur la clarté de l’interface.

---

## 9) Top 3 "applicables tout de suite"

1. **Stepper latéral + statuts visuels des étapes**.
2. **Carte d’étape fixe (Objectif / Action / Validation / Feedback)**.
3. **Barre d’actions sticky avec bouton “Valider” toujours visible**.

Ces trois changements donnent déjà un rendu plus pratique, plus lisible et plus immersif, sans aucun changement du JSON.
