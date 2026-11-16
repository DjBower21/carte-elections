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
    departements.json, cantons.json, circonscriptions.json   (vue d'ensemble, ~5 Mo)
    dep/<code>/communes, iris, cantons, circos, departement  (détaillés, chargés au zoom ≥ 8)
scripts/
  telecharger_wfs.py  ← télécharge communes, cantons, IRIS (Géoplateforme IGN, WFS)
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
python -I scripts/telecharger_wfs.py "ADMINEXPRESS-COG-CARTO.LATEST:commune" "code_insee_du_departement<'97'" code_insee,nom_officiel,code_insee_du_departement,code_insee_du_canton,population sources/ae_communes.geojson
python -I scripts/telecharger_wfs.py "ADMINEXPRESS-COG-CARTO.LATEST:canton" "code_insee_du_departement<'97'" code_insee,nom_officiel,code_insee_du_departement,composition_du_canton sources/ae_cantons.geojson
python -I scripts/telecharger_wfs.py "STATISTICALUNITS.IRIS:contours_iris" "type_iris<>'Z'" code_iris,code_insee,nom_commune,nom_iris,type_iris sources/iris.geojson
curl -sSfL -o sources/circonscriptions.geojson https://static.data.gouv.fr/resources/contours-geographiques-des-circonscriptions-legislatives/20240613-191506/circonscriptions-legislatives-p20.geojson
python -I scripts/build.py
```

### Comment les limites suivent les communes

`build.py` ne dessine pas les cantons et circonscriptions à partir de leurs propres contours : il les
**reconstruit en assemblant les communes** (union communes × cantons × circonscriptions, puis fusion).
Une commune entière appartient à un seul canton (champ IGN `code_insee_du_canton`) et à la circonscription
qui la couvre ; seules les communes réellement partagées (grandes villes) gardent un découpage interne.
Toutes les couches sont ensuite simplifiées ensemble : une limite commune à une commune, un canton,
une circonscription et un département est un seul et même tracé.

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
| Communes, cantons, départements | IGN Admin Express COG Carto (Géoplateforme) — même source que [france-geojson](https://github.com/gregoiredavid/france-geojson), en version récente | 2026 |
| Circonscriptions | [data.gouv.fr](https://www.data.gouv.fr/datasets/contours-geographiques-des-circonscriptions-legislatives) (agrégation des bureaux de vote) | 2024 |
| Quartiers | [Contours IRIS](https://www.data.gouv.fr/datasets/contours-iris-r-2) IGN / INSEE | Géoplateforme, 2026 |
| Fond de carte | [OpenFreeMap](https://openfreemap.org) / OpenStreetMap | — |

- Cantons, circonscriptions et départements sont alignés exactement sur les communes. Seuls les
  découpages *à l'intérieur* des grandes villes viennent des contours propres des cantons / circonscriptions
  (les circonscriptions sont elles-mêmes reconstituées à partir des bureaux de vote, donc approximatives en ville).
- Les quartiers IRIS sont une couche indépendante : leurs limites peuvent différer de quelques mètres.
- Paris et la Métropole de Lyon n'ont pas de cantons (c'est normal).
- Outre-mer non couvert pour l'instant.
