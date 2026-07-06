# Recherche emploi — veille automatique

Agrégateur d'offres d'emploi CRM / Campaign Manager (Île-de-France).

- **Sources :** France Travail (API) + Adzuna (API)
- **Dashboard :** [https://heloiseporpe.github.io/recherche-emploi/](https://heloiseporpe.github.io/recherche-emploi/)
- **Automatisation :** GitHub Actions relance le scraper chaque matin et met à jour les offres.

## Utilisation en local

```bash
# 1. Configurer
cp config.example.json config.json   # puis remplir les clés API

# 2. Installer les dépendances
pip install feedparser requests

# 3. Lancer
python job_scraper.py                 # génère jobs_output.json

# 4. Voir le dashboard
cp jobs_output.json docs/
python -m http.server 8000 --directory docs
# puis ouvrir http://localhost:8000
```

## Configuration GitHub

Voir [SETUP_GITHUB.md](SETUP_GITHUB.md) pour la mise en route de l'automatisation
(GitHub Secrets + activation de GitHub Pages).
