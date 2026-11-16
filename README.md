# Carte électorale — cantons, circonscriptions, communes, quartiers

Carte interactive (France métropolitaine) qui superpose :

- les **circonscriptions législatives** (découpage 2010, toujours en vigueur) ;
- les **cantons** (découpage 2015) ;
- les **communes** ;
- les **quartiers IRIS** dans les villes découpées (≈ 15 600 quartiers) ;
- les **départements**.

Un clic sur la carte indique le quartier, la commune, le canton et la circonscription du point cliqué.
La recherche trouve une commune, un canton (« Toulouse-4 ») ou une circonscription (« 31-01 »).
Pour une commune partagée (Toulouse, Marseille, Nice…), la fiche liste tous ses cantons et circonscriptions.

C'est un **site 100 % statique** (HTML + JS + GeoJSON) : aucun serveur à faire tourner.

## Structure

```
site/                 ← ce qui est publié
  index.html, style.css, app.js
  CNAME               ← domaine personnalisé (carte.cavalink-app.fr)
  data/
    index.json        ← recherche + rattachements commune → cantons / circonscriptions
    departements.json, cantons.json, circonscriptions.json   (chargés au démarrage, ~6 Mo)
    dep/<code>/communes.json, iris.json                      (chargés au zoom ≥ 8)
scripts/
  telecharger_iris.py ← récupère les IRIS (Géoplateforme IGN, WFS)
  build.py            ← sources/ → site/data/ (mapshaper)
sources/              ← données brutes (non versionnées)
```

## Lancer en local

```bash
python -m http.server 8765 -d site
```

Puis ouvrir http://127.0.0.1:8765.

## Reconstruire les données

```bash
npm install
mkdir -p sources
for f in cantons communes departements; do curl -sSfL -o sources/$f.geojson https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/$f.geojson; done
curl -sSfL -o sources/circonscriptions.geojson https://static.data.gouv.fr/resources/contours-geographiques-des-circonscriptions-legislatives/20240613-191506/circonscriptions-legislatives-p20.geojson
python -I scripts/telecharger_iris.py sources/iris.geojson
python -I scripts/build.py
```

## Mise en ligne sur `carte.cavalink-app.fr` (gratuit, via GitHub Pages)

1. Créer un dépôt GitHub (ex. `carte-elections`) et y pousser ce dossier (branche `main`).
2. Sur GitHub : **Settings → Pages → Source : GitHub Actions**. Le workflow
   `.github/workflows/pages.yml` publie `site/` à chaque push.
3. Chez le registrar de `cavalink-app.fr`, ajouter l'enregistrement DNS :
   `carte  CNAME  <ton-pseudo-github>.github.io.`
4. Sur GitHub : **Settings → Pages → Custom domain** : `carte.cavalink-app.fr`, puis cocher
   **Enforce HTTPS** quand le certificat est prêt (quelques minutes à quelques heures).

Sans domaine, la carte reste accessible sur `https://<ton-pseudo>.github.io/carte-elections/`.

## Sources et limites

| Couche | Source | Millésime |
|---|---|---|
| Communes, cantons, départements | IGN Admin Express via [france-geojson](https://github.com/gregoiredavid/france-geojson) | 2018 |
| Circonscriptions | [data.gouv.fr](https://www.data.gouv.fr/datasets/contours-geographiques-des-circonscriptions-legislatives) (agrégation des bureaux de vote) | 2024 |
| Quartiers | [Contours IRIS](https://www.data.gouv.fr/datasets/contours-iris-r-2) IGN / INSEE | Géoplateforme, 2026 |
| Fond de carte | [OpenFreeMap](https://openfreemap.org) / OpenStreetMap | — |

- Les sources diffèrent et sont simplifiées : les limites ne se superposent pas au mètre près.
- Rattachement commune → cantons / circonscriptions : déduit du point intérieur de chaque IRIS
  (approximatif en limite de quartier).
- Communes 2018 : les fusions de communes depuis 2018 ne sont pas prises en compte.
- Paris et la Métropole de Lyon n'ont pas de cantons (c'est normal).
- Outre-mer non couvert (pas de cantons d'outre-mer dans france-geojson).
