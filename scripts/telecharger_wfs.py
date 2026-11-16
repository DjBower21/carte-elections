"""Télécharge une couche WFS de la Géoplateforme IGN (data.geopf.fr) en GeoJSON, page par page.

Usage :
  python -I scripts/telecharger_wfs.py COUCHE "FILTRE_CQL" champ1,champ2,... sortie.geojson

Exemples (voir README) :
  ADMINEXPRESS-COG-CARTO.LATEST:commune   (communes, avec code_insee_du_canton)
  ADMINEXPRESS-COG-CARTO.LATEST:canton
  STATISTICALUNITS.IRIS:contours_iris     (quartiers IRIS)
"""
import json
import sys
import time
import urllib.parse
import urllib.request

WFS = "https://data.geopf.fr/wfs/ows"
PAGE = 5000


def page(layer, cql, props, sortby, start):
    params = {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": layer, "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326",
        "SORTBY": sortby, "COUNT": PAGE, "STARTINDEX": start,
    }
    if cql:
        params["CQL_FILTER"] = cql
    if props:
        params["PROPERTYNAME"] = props
    url = WFS + "?" + urllib.parse.urlencode(params)
    for essai in range(4):
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                return json.load(r)
        except Exception as e:  # le service renvoie parfois des 50x passagères
            if essai == 3:
                raise
            print("  nouvel essai après erreur :", e, flush=True)
            time.sleep(5 * (essai + 1))


def main(layer, cql, fields, out):
    champs = [c for c in fields.split(",") if c]
    sortby = champs[0] if champs else "cleabs"
    props = ",".join(champs + ["geometrie"]) if champs else ""
    feats, start = [], 0
    while True:
        d = page(layer, cql, props, sortby, start)
        fs = d["features"]
        feats.extend(fs)
        print(f"{layer} {start}: +{len(fs)} (total {len(feats)} / {d.get('numberMatched')})", flush=True)
        if len(fs) < PAGE:
            break
        start += PAGE
    for f in feats:
        f.pop("id", None)
        f.pop("geometry_name", None)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh, ensure_ascii=False)


if __name__ == "__main__":
    main(*sys.argv[1:5])
