"""Télécharge les contours IRIS (IGN, Géoplateforme WFS) des communes découpées en quartiers.

Les IRIS de type 'Z' (commune entière non découpée) sont exclus : ils n'apportent rien
par rapport au contour de la commune.

Usage : python -I scripts/telecharger_iris.py sources/iris.geojson
"""
import json
import sys
import urllib.parse
import urllib.request

WFS = "https://data.geopf.fr/wfs/ows"
LAYER = "STATISTICALUNITS.IRIS:contours_iris"
PAGE = 5000


def page(start):
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": LAYER,
        "OUTPUTFORMAT": "application/json",
        "SRSNAME": "EPSG:4326",
        "CQL_FILTER": "type_iris<>'Z'",
        "SORTBY": "code_iris",
        "COUNT": PAGE,
        "STARTINDEX": start,
        "PROPERTYNAME": "code_insee,nom_commune,code_iris,nom_iris,type_iris,geometrie",
    }
    url = WFS + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.load(r)


def main(out):
    feats, start = [], 0
    while True:
        d = page(start)
        fs = d["features"]
        feats.extend(fs)
        print(f"{start}: +{len(fs)} (total {len(feats)} / {d.get('numberMatched')})", flush=True)
        if len(fs) < PAGE:
            break
        start += PAGE
    for f in feats:
        f.pop("id", None)
        f.pop("geometry_name", None)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1])
