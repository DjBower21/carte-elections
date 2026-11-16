"""Construit les données de la carte à partir de sources/ vers site/data/.

Prérequis : `npm install` (mapshaper) et les fichiers de sources/ (voir README).
Usage (depuis la racine du projet) : python -I scripts/build.py
"""
import collections
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "sources")
TMP = os.path.join(ROOT, "build")
OUT = os.path.join(ROOT, "site", "data")
MAPSHAPER = os.path.join(ROOT, "node_modules", ".bin", "mapshaper" + (".cmd" if os.name == "nt" else ""))

# Métropole uniquement : le dépôt france-geojson n'a pas les cantons d'outre-mer.
METRO = "(d => d < '97')"
PALETTE = "values=0,1,2,3,4,5,6 save-as=ci"
PREC = "precision=0.00001"


def ms(*args):
    cmd = [MAPSHAPER, "-quiet", *args]
    print(">", " ".join(a if " " not in a else repr(a) for a in args[:6]), "...", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, shell=(os.name == "nt"))


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def bbox(geom):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for s in c:
                walk(s)

    walk(geom["coordinates"])
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


def ordinal(n):
    return "1re" if n == 1 else f"{n}e"


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)

    s = lambda n: os.path.join("sources", n)
    b = lambda n: os.path.join("build", n)

    # 1. Départements (léger, chargé au démarrage)
    ms("-i", s("departements.geojson"), "-simplify", "20%", "weighted", "keep-shapes",
       "-o", b("departements.json"), "format=geojson", PREC)

    # 2. Cantons : département déduit par jointure spatiale (robuste pour la Corse), couleurs non adjacentes
    ms("-i", s("cantons.geojson"), "-each", "nom=nom",
       "-join", b("departements.json"), "point-method", "fields=code", "prefix=dep_",
       "-rename-fields", "dep=dep_code",
       "-classify", "non-adjacent", *PALETTE.split(),
       "-o", b("cantons.json"), "format=geojson", PREC)

    # 3. Circonscriptions législatives (découpage 2010 en vigueur)
    ms("-i", s("circonscriptions.geojson"),
       "-filter", f"{METRO}(codeDepartement)",
       "-each", "dep=codeDepartement, code=codeCirconscription, num=+codeCirconscription.slice(-2), "
                "nomdep=nomDepartement, delete codeDepartement, delete codeCirconscription, "
                "delete nomDepartement, delete nomCirconscription",
       "-simplify", "35%", "weighted", "keep-shapes",
       "-classify", "non-adjacent", *PALETTE.split(),
       "-o", b("circos.json"), "format=geojson", PREC)

    # 4. Communes (tracés 2018 du dépôt france-geojson)
    ms("-i", s("communes.geojson"), "-each", "dep=code.slice(0,2)",
       "-o", b("communes.json"), "format=geojson", PREC)

    # 5. Quartiers = IRIS des communes découpées (hors type Z)
    ms("-i", s("iris.geojson"),
       "-filter", f"{METRO}(code_insee.slice(0,2))",
       "-each", "dep=code_insee.slice(0,2), code=code_iris, nom=nom_iris, com=code_insee, "
                "nomcom=nom_commune, type=type_iris, delete code_iris, delete nom_iris, "
                "delete code_insee, delete nom_commune, delete type_iris",
       "-simplify", "30%", "weighted", "keep-shapes",
       "-o", b("iris.json"), "format=geojson", PREC)

    # 6. Rattachement : point intérieur de chaque IRIS / commune -> canton + circonscription
    for layer in ("iris", "communes"):
        ms("-i", b(f"{layer}.json"), "-points", "inner",
           "-join", b("cantons.json"), "fields=code,nom", "prefix=can_",
           "-join", b("circos.json"), "fields=code,num", "prefix=cir_",
           "-o", b(f"{layer}_pts.json"), "format=geojson")

    deps = load(os.path.join(TMP, "departements.json"))["features"]
    cantons = load(os.path.join(TMP, "cantons.json"))["features"]
    circos = load(os.path.join(TMP, "circos.json"))["features"]
    communes = load(os.path.join(TMP, "communes.json"))["features"]
    iris = load(os.path.join(TMP, "iris.json"))["features"]
    iris_pts = load(os.path.join(TMP, "iris_pts.json"))["features"]
    com_pts = load(os.path.join(TMP, "communes_pts.json"))["features"]

    # Repli : un point intérieur tombé hors des contours simplifiés (îles, littoral)
    # est rattaché au canton / à la circonscription le plus proche du même département.
    # Exceptions légitimes sans canton : Paris (75) et la Métropole de Lyon.
    lyon_metro = set()
    verts = {}
    for layer, feats in (("can", cantons), ("cir", circos)):
        vs = collections.defaultdict(list)
        for f in feats:
            pts = []

            def walk(c, pts=pts):
                if isinstance(c[0], (int, float)):
                    pts.append(c)
                else:
                    for x in c:
                        walk(x)

            walk(f["geometry"]["coordinates"])
            vs[f["properties"]["dep"]].append((f["properties"], pts[::3] or pts))
        verts[layer] = vs

    def nearest(layer, dep, xy):
        best, bd = None, float("inf")
        for props, pts in verts[layer].get(dep, ()):
            for x, y in pts:
                d = (x - xy[0]) ** 2 + (y - xy[1]) ** 2
                if d < bd:
                    best, bd = props, d
        return best

    def complete(rows, dep_of):
        repl = 0
        for f in rows:
            r, xy = f["properties"], f["geometry"]["coordinates"]
            dep = dep_of(r)
            if not r.get("can_code") and dep not in ("75", "69"):
                p = nearest("can", dep, xy)
                if p:
                    r["can_code"], repl = p["code"], repl + 1
            if not r.get("cir_code"):
                p = nearest("cir", dep, xy)
                if p:
                    r["cir_code"], repl = p["code"], repl + 1
        return repl

    # Rhône : les communes de la Métropole de Lyon n'ont pas de canton (point hors des 13 cantons).
    for f in com_pts:
        r = f["properties"]
        if r["dep"] == "69" and not r.get("can_code"):
            lyon_metro.add(r["code"])
    print("replis canton/circo :",
          complete(iris_pts, lambda r: r["dep"]), "IRIS,",
          complete(com_pts, lambda r: r["dep"]), "communes;",
          len(lyon_metro), "communes de la Métropole de Lyon")
    # Points d'étiquette (un par entité, placé à l'intérieur du polygone)
    def label(xy, **props):
        return {"type": "Feature", "properties": {"lbl": 1, **props},
                "geometry": {"type": "Point", "coordinates": [round(xy[0], 5), round(xy[1], 5)]}}

    iris_lbl = collections.defaultdict(list)
    for f in iris_pts:
        p = f["properties"]
        iris_lbl[p["dep"]].append(label(f["geometry"]["coordinates"], nom=p["nom"]))
    com_lbl = collections.defaultdict(list)
    for f in com_pts:
        p = f["properties"]
        com_lbl[p["dep"]].append(label(f["geometry"]["coordinates"], nom=p["nom"], code=p["code"]))
    for name in ("cantons", "circos"):
        ms("-i", b(f"{name}.json"), "-points", "inner", "-o", b(f"{name}_lbl.json"), "format=geojson")
    cantons_lbl = [label(f["geometry"]["coordinates"], nom=f["properties"]["nom"], code=f["properties"]["code"])
                   for f in load(os.path.join(TMP, "cantons_lbl.json"))["features"]]
    circos_lbl_src = load(os.path.join(TMP, "circos_lbl.json"))["features"]

    iris_pts = [f["properties"] for f in iris_pts]
    com_pts = [f["properties"] for f in com_pts]

    dep_names = {d["properties"]["code"]: d["properties"]["nom"] for d in deps}
    canton_names = {c["properties"]["code"]: c["properties"]["nom"] for c in cantons}

    # Libellés des circonscriptions
    for c in circos:
        p = c["properties"]
        p["nom"] = f"{p['nomdep']} — {ordinal(p['num'])} circonscription"
        p["court"] = f"{p['dep']}-{p['num']:02d}"
        del p["nomdep"]
    circos_lbl = [label(f["geometry"]["coordinates"], code=f["properties"]["code"],
                        court=f"{f['properties']['dep']}-{f['properties']['num']:02d}")
                  for f in circos_lbl_src]

    # Rattachement des IRIS
    iris_by_code = {f["properties"]["code"]: f for f in iris}
    for r in iris_pts:
        f = iris_by_code.get(r["code"])
        if f:
            f["properties"]["canton"] = r.get("can_code")
            f["properties"]["circo"] = r.get("cir_code")

    # Rattachement des communes : union des IRIS si la commune est découpée, sinon point intérieur
    com_cantons = collections.defaultdict(set)
    com_circos = collections.defaultdict(set)
    for f in iris:
        p = f["properties"]
        com = p["com"]
        # arrondissements municipaux de Paris / Lyon / Marseille -> commune entière
        if com.startswith("751"):
            com = "75056"
        elif com.startswith("6938"):
            com = "69123"
        elif com.startswith("132") and 13201 <= int(com) <= 13216:
            com = "13055"
        p["parent"] = com
        if p.get("canton"):
            com_cantons[com].add(p["canton"])
        if p.get("circo"):
            com_circos[com].add(p["circo"])
    for r in com_pts:
        if r["code"] not in com_cantons and r.get("can_code"):
            com_cantons[r["code"]].add(r["can_code"])
        if r["code"] not in com_circos and r.get("cir_code"):
            com_circos[r["code"]].add(r["cir_code"])

    nb_iris = collections.Counter(f["properties"]["parent"] for f in iris)
    index = []
    by_dep = collections.defaultdict(lambda: {"communes": [], "iris": []})
    for f in communes:
        p = f["properties"]
        p["cantons"] = sorted(com_cantons.get(p["code"], ()))
        p["circos"] = sorted(com_circos.get(p["code"], ()))
        p["quartiers"] = nb_iris.get(p["code"], 0)
        if p["dep"] == "75":
            p["sanscanton"] = "Ville de Paris : pas de canton (conseil de Paris)"
        elif p["code"] in lyon_metro:
            p["sanscanton"] = "Métropole de Lyon : pas de canton (conseil de la Métropole)"
        by_dep[p["dep"]]["communes"].append(f)
        index.append([p["code"], p["nom"], p["dep"], bbox(f["geometry"]), p["cantons"], p["circos"]])
    for f in iris:
        by_dep[f["properties"]["dep"]]["iris"].append(f)

    # Écriture
    for dep, layers in sorted(by_dep.items()):
        layers["communes"] += com_lbl.get(dep, [])
        layers["iris"] += iris_lbl.get(dep, [])
        for name, feats in layers.items():
            dump({"type": "FeatureCollection", "features": feats}, os.path.join(OUT, "dep", dep, f"{name}.json"))

    dep_index = {}
    for d in deps:
        code = d["properties"]["code"]
        dep_index[code] = {"nom": d["properties"]["nom"], "bbox": bbox(d["geometry"])}
    dump({"type": "FeatureCollection", "features": deps}, os.path.join(OUT, "departements.json"))
    dump({"type": "FeatureCollection", "features": cantons + cantons_lbl}, os.path.join(OUT, "cantons.json"))
    dump({"type": "FeatureCollection", "features": circos + circos_lbl}, os.path.join(OUT, "circonscriptions.json"))
    dump({"deps": dep_index, "cantons": canton_names,
          "circos": {c["properties"]["code"]: c["properties"]["nom"] for c in circos},
          "communes": index},
         os.path.join(OUT, "index.json"))

    # Contrôles
    sans_canton = [c for c in index if not c[4] and c[2] not in ("75",)]
    sans_circo = [c for c in index if not c[5]]
    print(f"départements={len(deps)} cantons={len(cantons)} circos={len(circos)} "
          f"communes={len(communes)} iris={len(iris)}")
    print(f"communes sans canton (hors Paris)={len(sans_canton)} ex: {[c[1] for c in sans_canton[:8]]}")
    print(f"communes sans circo={len(sans_circo)} ex: {[c[1] for c in sans_circo[:8]]}")
    multi = sorted(index, key=lambda c: -len(c[4]))[:5]
    print("plus découpées (cantons):", [(c[1], len(c[4]), len(c[5])) for c in multi])
    print("cantons par dep 2A/2B/69:", collections.Counter(c["properties"]["dep"] for c in cantons
                                                            if c["properties"]["dep"] in ("2A", "2B", "69")))


if __name__ == "__main__":
    sys.exit(main())
