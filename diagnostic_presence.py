#!/usr/bin/env python3
"""Diagnostic ponctuel : mesure la PRÉSENCE de certains employeurs sur
France Travail et Adzuna (Île-de-France), pour vérifier que leurs offres — et
en particulier leurs offres CRM/marketing — y sont bien récupérables.

Ne modifie rien : lit config.json (créé en CI depuis les secrets), interroge les
deux API par NOM d'entreprise, et imprime un tableau. Lancé via le workflow
`diagnostic.yml` (workflow_dispatch)."""

import json
import re
import time
import requests

CONFIG = json.load(open("config.json", encoding="utf-8"))

# Employeurs proches à vérifier (ceux dont l'ATS n'est pas interrogeable en direct).
COMPANIES = [
    "Carrefour", "CEA", "McDonald's", "Bruneau", "Horiba", "Servier", "EDF",
    "Nokia", "Mondelez", "Lidl", "Ericsson", "Bouygues Construction",
    "Dassault Systèmes", "Eiffage", "MBDA", "Colas",
]

CRM_RE = re.compile(
    r"crm|campaign|marketing automation|email marketing|lifecycle|"
    r"fid[ée]lisation|r[ée]tention|parcours client|donn[ée]es client|"
    r"gestionnaire de campagne|owned media|braze|emarsys|salesforce marketing",
    re.I,
)


def _crm(txt):
    return bool(CRM_RE.search(txt or ""))


# ── France Travail ──────────────────────────────────────────────────────────
def ft_token():
    cid = CONFIG.get("francetravail_client_id", "")
    csec = CONFIG.get("francetravail_client_secret", "")
    if not cid or "VOTRE" in cid:
        return None
    r = requests.post(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token",
        params={"realm": "/partenaire"},
        data={"grant_type": "client_credentials", "client_id": cid,
              "client_secret": csec, "scope": "api_offresdemploiv2 o2dsoffre"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("access_token")


def ft_presence(token, name):
    """(total_IDF, crm_dans_l'échantillon) pour une entreprise sur France Travail."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = requests.get(
        "https://api.francetravail.io/partenaire/offresdemploiv2/offres/search",
        headers=headers,
        params={"motsCles": name, "region": "11", "range": "0-149"},
        timeout=20,
    )
    if r.status_code not in (200, 206):
        return None, 0
    # Total réel dans l'en-tête Content-Range : "offres 0-149/532"
    total = None
    cr = r.headers.get("Content-Range", "")
    if "/" in cr:
        try:
            total = int(cr.rsplit("/", 1)[1])
        except ValueError:
            total = None
    res = (r.json() or {}).get("resultats", []) if r.content else []
    if total is None:
        total = len(res)
    crm = sum(1 for o in res
              if _crm(o.get("intitule", "") + " " + o.get("description", "")))
    return total, crm


# ── Adzuna ──────────────────────────────────────────────────────────────────
def adzuna_presence(name):
    """(total_IDF, crm_dans_l'échantillon) pour une entreprise sur Adzuna."""
    app_id = CONFIG.get("adzuna_app_id", "")
    app_key = CONFIG.get("adzuna_app_key", "")
    if not app_id or "VOTRE" in app_id:
        return None, 0
    r = requests.get(
        "https://api.adzuna.com/v1/api/jobs/fr/search/1",
        params={"app_id": app_id, "app_key": app_key, "what_phrase": name,
                "where": "Île-de-France", "results_per_page": 50, "max_days_old": 90},
        timeout=20,
    )
    if r.status_code != 200:
        return None, 0
    d = r.json() or {}
    total = int(d.get("count", 0))
    crm = sum(1 for o in d.get("results", [])
              if _crm(o.get("title", "") + " " + o.get("description", "")))
    return total, crm


def main():
    print("=" * 72)
    print("DIAGNOSTIC PRÉSENCE — France Travail + Adzuna (Île-de-France)")
    print("=" * 72)
    token = None
    try:
        token = ft_token()
    except Exception as ex:
        print(f"France Travail token : ERREUR {ex}")
    print(f"{'Entreprise':<24} | {'FT total':>8} {'FT CRM*':>7} | {'Adz total':>9} {'Adz CRM*':>8}")
    print("-" * 72)
    for name in COMPANIES:
        ft_t, ft_c = (None, 0)
        if token:
            try:
                ft_t, ft_c = ft_presence(token, name)
            except Exception as ex:
                print(f"{name:<24} | FT ERREUR {ex}")
        try:
            az_t, az_c = adzuna_presence(name)
        except Exception as ex:
            az_t, az_c = None, 0
            print(f"{name:<24} | Adzuna ERREUR {ex}")
        f_t = "n/a" if ft_t is None else str(ft_t)
        a_t = "n/a" if az_t is None else str(az_t)
        print(f"{name:<24} | {f_t:>8} {ft_c:>7} | {a_t:>9} {az_c:>8}")
        time.sleep(0.6)
    print("-" * 72)
    print("* CRM = offres CRM/marketing détectées dans l'échantillon récupéré")
    print("  (FT : sur les 150 premières ; Adz : sur les 50 premières).")
    print("  « total » = nombre total d'offres de l'entreprise en IDF sur la plateforme.")
    print("  Un total > 0 prouve que l'employeur publie sur la plateforme → ses")
    print("  offres CRM y passeront quand il en ouvrira.")


if __name__ == "__main__":
    main()
