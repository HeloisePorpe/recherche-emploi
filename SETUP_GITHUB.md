# Configuration GitHub — guide pas à pas

Ce guide explique comment mettre en route l'automatisation du scraper.
Une fois configuré :

- le scraper se lance **tout seul chaque matin** sur les serveurs de GitHub ;
- il met à jour la liste des offres ;
- le site (GitHub Pages) affiche toujours les dernières offres.

Il y a **2 choses à faire une seule fois** :

1. Enregistrer 4 « secrets » (vos clés d'accès aux sites d'offres).
2. Vérifier que tout fonctionne (GitHub Pages est déjà activé).

---

## Étape 1 — Enregistrer les 4 secrets

Les « secrets » sont des clés que GitHub garde chiffrées. Le scraper en a besoin
pour interroger France Travail et Adzuna. On ne les met **jamais** dans le code.

### Où aller

1. Ouvrez votre dépôt : `https://github.com/HeloisePorpe/recherche-emploi`
2. Onglet **Settings** (Paramètres).
3. Menu de gauche : **Secrets and variables** → **Actions**.
4. Bouton vert **New repository secret**.

### Les 4 secrets à créer

Pour **chaque** ligne : tapez le **Name** exactement comme écrit, collez la
**valeur**, puis **Add secret**.

| Name (à copier tel quel)       | Valeur                                  |
|--------------------------------|-----------------------------------------|
| `FRANCETRAVAIL_CLIENT_ID`      | Votre identifiant client France Travail |
| `FRANCETRAVAIL_CLIENT_SECRET`  | Votre clé secrète France Travail        |
| `ADZUNA_APP_ID`                | Votre « App ID » Adzuna                 |
| `ADZUNA_APP_KEY`               | Votre « App Key » Adzuna                |

---

## Étape 1 bis — Activer le filtre « trajet + télétravail » (optionnel mais recommandé)

Le dashboard propose un filtre **« 🎯 Mes critères trajet + télétravail »**.
Pour qu'il calcule les temps de trajet en transport, ajoutez **2 secrets** :

| Name (à copier tel quel) | Valeur                                                        |
|--------------------------|--------------------------------------------------------------|
| `HOME_ADDRESS`           | Votre adresse de départ, ex. `7 boulevard de la liberté, Bures-sur-Yvette` |
| `IDFM_TOKEN`             | Votre clé **Île-de-France Mobilités (PRIM)** — gratuite, voir ci-dessous |

### Obtenir une clé IDFM PRIM (gratuit, sans carte bancaire)

Île-de-France Mobilités expose une API de calcul d'itinéraire (basée sur Navitia)
gratuite, idéale ici puisque le domicile et les offres sont en Île-de-France.

1. Allez sur **https://prim.iledefrance-mobilites.fr/** → créez un compte (gratuit).
2. Confirmez votre e-mail, connectez-vous.
3. Dans **Mes jetons d'authentification** (ou « API keys »), copiez votre **clé**.
4. Collez-la dans le secret `IDFM_TOKEN`.

Le calcul se fait porte-à-porte pour une arrivée à 9h un jour de semaine.

> - **Aucune adresse personnelle n'apparaît dans le code public** : elle vit
>   uniquement dans le secret `HOME_ADDRESS`.
> - Sans ces secrets, le filtre fonctionne quand même (sur le télétravail et la
>   localisation France), mais les trajets restent affichés « à vérifier ».
> - **Note :** l'ancienne offre gratuite de Navitia.io n'existe plus. On utilise
>   donc IDFM PRIM. Alternatives possibles : `GOOGLE_MAPS_API_KEY` (Google Maps,
>   nécessite d'activer la facturation) ou `NAVITIA_TOKEN` (Navitia.io, payant).

---

## Étape 2 — Vérifier que tout fonctionne

GitHub Pages est déjà activé (branche `master`, dossier `/docs`). Votre site est à :

**https://heloiseporpe.github.io/recherche-emploi/**

Pour tester le scraper sans attendre le lendemain :

1. Onglet **Actions**.
2. À gauche, **Scraper offres d'emploi**.
3. À droite, **Run workflow** → **Run workflow**.
4. Une ligne apparaît :
   - rond **jaune** = en cours ;
   - coche **verte** = succès ;
   - croix **rouge** = erreur (voir ci-dessous).

En cas de succès, le fichier `docs/jobs_output.json` est mis à jour (commit
« MAJ offres [skip ci] ») et le site affiche les nouvelles offres après
rafraîchissement.

### En cas de croix rouge

- Cliquez sur l'étape en échec pour lire le message.
- Cause la plus fréquente : un **secret mal nommé ou manquant** (Étape 1) — les
  noms doivent être identiques, en majuscules. Corrigez puis relancez.

---

## Et après ?

Plus rien à faire : chaque matin (~5h, heure de Paris) le scraper tourne, et le
site se met à jour tout seul. Vous pouvez toujours relancer manuellement via
**Actions → Run workflow**.
