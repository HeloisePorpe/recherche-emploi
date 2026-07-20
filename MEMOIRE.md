# Mémoire du projet — Recherche emploi

> Fichier de contexte pour reprendre le projet depuis n'importe quelle session
> (PC, web, mobile). Ne contient **aucun secret** : les clés API vivent
> uniquement dans les GitHub Secrets du dépôt.

## Objectif

Veille automatique d'offres d'emploi **CRM / Campaign Manager / Chef de projet CRM**
en **Île-de-France**, avec un dashboard web consultable partout et un scoring
personnalisé.

- **Site en ligne :** https://heloiseporpe.github.io/recherche-emploi/
- **Dépôt (public) :** https://github.com/HeloisePorpe/recherche-emploi
- **Hébergement :** GitHub Pages (branche `master`, dossier `/docs`).

## Comment ça marche

1. **`job_scraper.py`** interroge les sources, score et filtre les offres, puis
   écrit **`docs/jobs_output.json`**.
2. **GitHub Actions** (`.github/workflows/scraper.yml`) relance le scraper
   **chaque matin (~5h, heure de Paris)** et pousse le JSON mis à jour.
3. Le **dashboard** (`docs/index.html` + `app.js` + `styles.css`) lit ce JSON et
   l'affiche avec filtres, recherche et tri.
4. Le **suivi de candidatures** (`docs/candidatures.html` + `candidatures.js`)
   est un Kanban personnel (état stocké dans le navigateur).

## Sources d'offres

| Source | Type | Notes |
|---|---|---|
| France Travail | API | CDI, IDF, descriptions complètes |
| Adzuna | API | Descriptions **tronquées à 500 car.** |
| **Remotive** | API | 100 % remote, catégorie marketing |
| **We Work Remotely** | RSS | 100 % remote (marketing + support) |
| **Jobicy** | API | 100 % remote, filtre géo (France/Europe/anywhere) |
| **RemoteOK** | API | 100 % remote (tech-heavy) |
| **The Muse** | API | Catégorie Marketing, France + Remote |
| **Arbeitnow** | API | Europe / remote (sans clé) |
| Indeed / WTTJ | RSS | Flux bloqués (désactivés de fait) |

France Travail et Adzuna interrogés avec une liste de mots-clés élargie
(CRM manager, responsable CRM, campaign manager, email/lifecycle marketing…) ;
Adzuna récupère 2 pages par mot-clé.

Les sources remote posent `telework_days = 5` et `in_france` selon le périmètre
(France/Europe/worldwide = True ; US-only, etc. = False). Toutes les offres
passent un **filtre de pertinence** (CRM / Campaign Manager / marketing automation)
et le **salaire plancher** (42 055 € si indiqué).

## Secrets GitHub Actions (Settings → Secrets → Actions)

Valeurs **non** stockées ici. Noms attendus :

- `FRANCETRAVAIL_CLIENT_ID`, `FRANCETRAVAIL_CLIENT_SECRET`
- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
- `HOME_ADDRESS` *(optionnel — adresse de départ pour les trajets ; jamais dans le code public)*
- `IDFM_TOKEN` *(optionnel — trajets en transport, gratuit via prim.iledefrance-mobilites.fr)*
- `NAVITIA_TOKEN` / `GOOGLE_MAPS_API_KEY` *(optionnels — alternatives payantes)*

`config.json` est **généré en CI** à partir de ces secrets et n'est jamais commité
(il est dans `.gitignore`). Voir `SETUP_GITHUB.md` pour la mise en route.

## Champs d'une offre (`jobs_output.json`)

`source`, `title`, `link`, `company`, `location`, `description`,
`salary_raw` / `salary_extracted`, `published`, `telework_days`,
`commute_minutes`, `in_france`, `contract_type`, `score`, `score_reasons`.

## Dashboard — filtres disponibles

- Recherche texte (titre + entreprise + lieu + description)
- Tri : note / date / salaire
- Ancienneté (7 / 14 / 30 / 90 jours)
- Note minimale, salaire minimum, « avec salaire affiché uniquement »
- Télétravail uniquement, **CDI uniquement**
- **🎯 Mes critères trajet + télétravail** (**activé par défaut**) :
  - Masque les offres à **plus de 75 min** de trajet…
  - …**sauf** les postes **100 % télétravail** (en France), toujours affichés
  - Trajet non calculé → affiché et marqué « à vérifier » (masqué en mode *strict*)
  - Seuil dans `docs/app.js` : `MAX_COMMUTE = 75`
- Filtre par source
- **Date de parution** affichée sur chaque carte
- **Archivage** des offres non pertinentes (bouton « ✕ Pas pertinent ») :
  masquées de la liste, consultables via « Voir les archivées », restaurables,
  et **exportables en JSON** (`offres-archivees.json`) pour analyse/affinage
  des filtres. Stockage : `localStorage` clé `recherche-emploi-archivees`.
- Filtres mémorisés (localStorage), compteur de filtres actifs
- Responsive : 1 col (mobile) / 2 col (tablette-portable) / auto (large)

## Filtrage à la source (`should_include` + `screen_offer`)

- **Salaire plancher** (42 055 € si indiqué) + pertinence CRM.
- **`screen_offer`** (règles issues de l'analyse des refus) à deux niveaux :
  - **Exclusion** (non ambigu) : titre engineer/alternance/stage, CRM médical
    (dispositifs cardiaques), CRM = caisse/magasin, secteur automobile,
    résidence/citoyenneté US requise.
  - **Alerte** (`job["flags"]`, gardée + badge ⚠ au dashboard) : Customer Success /
    Account mgmt, pertinence CRM à confirmer, contrat freelance/horaire/$, ESN,
    écart technique, séniorité/management, résidence hors France, télétravail
    non mentionné / faible, trajet long, annonce ancienne.
- Le trajet/télétravail fin reste **délégué au dashboard** (filtre « Mes critères »).
- Dashboard : badges ⚠ sur les cartes + filtre **« Masquer les offres signalées »**.

## Scoring (`compute_score` dans `job_scraper.py`)

Base 5/10, ajusté par : outils/compétences du profil, secteurs préférés/pénalisés,
« Salesforce obligatoire » (malus), salaire vs cible, trajet, jours de télétravail.

Profil candidat cible : CRM/Campaign Manager ; outils clés (emarsys, HTML/CSS,
segmentation, email/SMS…) ; salaire cible ~45–50 k€ (plancher dur 42 055 €).

## Calcul des trajets

- **Provider par défaut : IDFM PRIM** (Île-de-France Mobilités, gratuit, basé sur
  Navitia). Fallback : Navitia.io (payant) puis Google Maps (payant).
  Sélection auto selon le secret présent (`idfm` > `navitia` > `google`).
- Géocodage via la **Base Adresse Nationale** (gratuit, sans clé).
- Trajet porte-à-porte pour une arrivée à 9h un jour de semaine.
- Sans `IDFM_TOKEN` + `HOME_ADDRESS`, `commute_minutes` reste vide et le filtre
  trajet affiche « à vérifier ».
- ⚠️ L'ancienne offre gratuite **Navitia.io n'existe plus** (payante depuis 2024)
  → on utilise IDFM PRIM.

## Suivi de candidatures (Kanban)

- Page `docs/candidatures.html`, colonnes : **À postuler → Postulé → Entretien →
  Réponse**.
- Ajout depuis le dashboard (bouton « Suivre » sur chaque offre) ou manuellement.
- Glisser-déposer entre colonnes ; notes libres par candidature.
- **État stocké dans le navigateur (localStorage)** → propre à chaque appareil,
  non synchronisé PC/mobile (voir « Pistes »).

## Contraintes connues

- Site **statique** (pas de backend) → pas de synchronisation multi-appareils du Kanban.
- Adzuna tronque ses descriptions → le télétravail n'est pas toujours détectable
  (mitigé par la récupération du texte complet des annonces).

## Pistes / idées pour la suite

- Synchroniser le Kanban entre appareils (backend léger : Firebase, Supabase, ou
  un fichier commité via l'API GitHub).
- Ajouter d'autres sources d'offres.
- Ajuster le scoring / les mots-clés du profil.

## Historique des sessions

- **Session initiale (PC, Claude Code local)** : installation Python, test du
  scraper, config API France Travail + Adzuna, construction du dashboard,
  publication GitHub Pages, mise en place du scan quotidien, dépôt public créé,
  adresse perso retirée du code, secrets replacés dans le bon dépôt.
- **Session web/cloud (celle-ci)** : filtres enrichis, filtre trajet + télétravail
  personnalisé, intégration Navitia + récupération des annonces complètes, ajout
  du suivi de candidatures, ce fichier mémoire.
