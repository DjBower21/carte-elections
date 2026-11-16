"""Construit les données de la carte à partir de sources/ vers site/data/.

Principe : cantons, circonscriptions et départements sont RECONSTRUITS à partir des communes,
pour que leurs limites suivent exactement les limites communales :

1. union communes × cantons × circonscriptions -> « morceaux » (chaque morceau = une commune,
   ou la partie d'une commune située dans un canton / une circonscription) ;
2. chaque morceau reçoit son canton et sa circonscription :
   - commune entière dans un seul canton : canton indiqué par l'IGN (champ code_insee_du_canton) ;
   - commune découpée (grandes villes) : canton du morceau, d'après le contour IGN du canton ;
   - circonscription : celle qui couvre la commune ; si la commune est partagée (≥ 5 % de sa
     surface dans une autre circonscription), on garde le découpage interne ;
3. fusion des morceaux par canton / circonscription / commune / département, puis simplification
   commune à toutes les couches (les limites partagées restent superposées).

Prérequis : `npm install` (mapshaper) et les fichiers de sources/ (voir README).
Usage (depuis la racine du projet) : python -I scripts/build.py
"""
import collections
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "sources")
# Fichiers intermédiaires volumineux (> 300 Mo) : hors du dossier projet (synchronisé OneDrive)
TMP = os.path.join(tempfile.gettempdir(), "carte-elections-build")
OUT = os.path.join(ROOT, "site", "data")
MAPSHAPER = os.path.join(ROOT, "node_modules", ".bin", "mapshaper" + (".cmd" if os.name == "nt" else ""))

SEUIL_CIRCO = 0.05      # part minimale de surface pour considérer une commune partagée entre circonscriptions
SEUIL_CANTON = 0.01     # idem pour les morceaux de canton dans une commune découpée
SIMPL_DETAIL = "interval=50"   # mètres — couches chargées au zoom (par département)
SIMPL_LEGER = "interval=400"   # mètres — couches nationales (vue d'ensemble)
COULEURS = ["values=0,1,2,3,4,5,6", "save-as=ci"]


def ms(*args):
    print(">", " ".join(args[:4]), "...", flush=True)
    env = dict(os.environ, NODE_OPTIONS="--max-old-space-size=12000")
    subprocess.run([MAPSHAPER, "-quiet", *args], check=True, cwd=ROOT, shell=(os.name == "nt"), env=env)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def fc(features):
    return {"type": "FeatureCollection", "features": features}


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


def label(xy, **props):
    return {"type": "Feature", "properties": {"lbl": 1, **props},
            "geometry": {"type": "Point", "coordinates": [round(xy[0], 5), round(xy[1], 5)]}}


def vider(dossier):
    """Supprime les fichiers d'un dossier ; les dossiers vides verrouillés par OneDrive sont tolérés."""
    for racine, dirs, fichiers in os.walk(dossier, topdown=False):
        for f in fichiers:
            os.remove(os.path.join(racine, f))
        for d in dirs:
            try:
                os.rmdir(os.path.join(racine, d))
            except OSError:
                pass


def main():
    vider(TMP)
    for d in ("complet", "detail", "leger", "iris"):
        os.makedirs(os.path.join(TMP, d), exist_ok=True)   # mapshaper exige des dossiers de sortie existants
    vider(OUT)
    s = lambda n: os.path.join(SRC, n)
    b = lambda n: os.path.join(TMP, n)

    # ---- 1. Union communes × cantons × circonscriptions ----
    ms("-i", s("ae_communes.geojson"), s("ae_cantons.geojson"), s("circonscriptions.geojson"), "combine-files",
       "-rename-layers", "communes,cantons,circos",
       "-each", "com=code_insee, dep=code_insee_du_departement, cc=code_insee_du_canton", "target=communes",
       "-filter-fields", "com,dep,cc", "target=communes",
       "-each", "can=code_insee", "target=cantons", "-filter-fields", "can", "target=cantons",
       "-filter", "codeDepartement < '97'", "target=circos",
       "-each", "cir=codeCirconscription", "target=circos", "-filter-fields", "cir", "target=circos",
       # chevauchements dans la source (ex. Montpellier-5 inclus dans Montpellier-4) : le plus petit l'emporte
       "-clean", "overlap-rule=min-area", "target=cantons",
       "-clean", "overlap-rule=min-area", "target=circos",
       "-union", "target=communes,cantons,circos", "name=pieces",
       "-filter", "com != null",
       "-each", "area=this.area",
       "-o", b("pieces.json"), "format=geojson")

    communes_src = load(os.path.join(SRC, "ae_communes.geojson"))["features"]
    cantons_src = load(os.path.join(SRC, "ae_cantons.geojson"))["features"]
    circos_src = load(os.path.join(SRC, "circonscriptions.geojson"))["features"]
    infos_com = {f["properties"]["code_insee"]: f["properties"] for f in communes_src}
    infos_can = {f["properties"]["code_insee"]: f["properties"] for f in cantons_src}
    infos_cir = {f["properties"]["codeCirconscription"]: f["properties"] for f in circos_src}
    circos_geo = [f for f in circos_src if f["properties"]["codeDepartement"] < "97"]
    del communes_src, cantons_src, circos_src

    # ---- 2. Attribution canton / circonscription de chaque morceau ----
    pieces = load(os.path.join(TMP, "pieces.json"))
    surf = collections.Counter()
    part_can = collections.defaultdict(collections.Counter)
    part_cir = collections.defaultdict(collections.Counter)
    for f in pieces["features"]:
        p = f["properties"]
        surf[p["com"]] += p["area"]
        if p.get("can"):
            part_can[p["com"]][p["can"]] += p["area"]
        if p.get("cir"):
            part_cir[p["com"]][p["cir"]] += p["area"]

    def gardes(parts, total, seuil, surface_min):
        """Territoires retenus pour une commune : le dominant + ceux qui en couvrent une part
        significative (en proportion OU en surface absolue, pour les très grandes villes)."""
        if not parts:
            return []
        dom = parts.most_common(1)[0][0]
        return [k for k, v in parts.most_common() if k == dom or v / total >= seuil or v >= surface_min]

    cantons_com, circos_com = {}, {}
    for com, total in surf.items():
        cc = infos_com[com]["code_insee_du_canton"]
        if cc == "NR":                       # Paris, Métropole de Lyon : pas de canton
            cantons_com[com] = []
        elif cc in infos_can:                # commune entière dans un canton
            cantons_com[com] = [cc]
        else:                                # commune découpée entre plusieurs cantons
            cantons_com[com] = gardes(part_can[com], total, SEUIL_CANTON, 300_000)
        circos_com[com] = gardes(part_cir[com], total, SEUIL_CIRCO, 1_000_000)

    # Îles non couvertes par le contour des circonscriptions : circonscription la plus proche du département
    pt_com = {}
    for f in pieces["features"]:
        c = f["geometry"]["coordinates"]
        while isinstance(c[0][0], list):
            c = c[0]
        pt_com.setdefault(f["properties"]["com"], c[0])
    sommets = collections.defaultdict(list)
    for f in circos_geo:
        def walk(c, out):
            if isinstance(c[0], (int, float)):
                out.append(c)
            else:
                for x in c:
                    walk(x, out)
        pts = []
        walk(f["geometry"]["coordinates"], pts)
        sommets[f["properties"]["codeDepartement"]].append((f["properties"]["codeCirconscription"], pts))
    for com, cirs in circos_com.items():
        if cirs:
            continue
        x, y = pt_com[com]
        dep = infos_com[com]["code_insee_du_departement"]
        best = min(((cx - x) ** 2 + (cy - y) ** 2, code)
                   for code, pts in sommets[dep] for cx, cy in pts)
        circos_com[com] = [best[1]]
        print("  circonscription la plus proche :", infos_com[com]["nom_officiel"], "->", best[1])

    for f in pieces["features"]:
        p = f["properties"]
        com = p["com"]
        cans, cirs = cantons_com[com], circos_com[com]
        p["can"] = (p.get("can") if p.get("can") in cans else cans[0]) if cans else None
        p["cir"] = (p.get("cir") if p.get("cir") in cirs else cirs[0]) if cirs else None
        del p["area"], p["cc"]
    dump(pieces, os.path.join(TMP, "pieces_ok.json"))
    del pieces

    # ---- 3. Fusion + simplification commune à toutes les couches ----
    ms("-i", b("pieces_ok.json"), "name=pieces",
       "-dissolve2", "com", "copy-fields=dep", "target=pieces", "+", "name=communes",
       "-filter", "can != null", "target=pieces", "+", "name=morceaux_can",
       "-dissolve2", "can", "copy-fields=dep", "target=morceaux_can", "name=cantons",
       "-dissolve2", "cir", "copy-fields=dep", "target=pieces", "+", "name=circos",
       "-dissolve2", "dep", "target=communes", "+", "name=deps",
       "-classify", "non-adjacent", *COULEURS, "target=cantons",
       "-classify", "non-adjacent", *COULEURS, "target=circos",
       "-points", "inner", "target=communes", "+", "name=communes_lbl",
       "-points", "inner", "target=cantons", "+", "name=cantons_lbl",
       "-points", "inner", "target=circos", "+", "name=circos_lbl",
       "-o", b("complet") + os.sep, "format=geojson",
       "target=communes,cantons,circos,deps,communes_lbl,cantons_lbl,circos_lbl")
    # Simplification dans un jeu de données unique (combine-files) : les limites communes aux
    # couches sont un même arc, simplifié une seule fois -> elles restent superposées.
    complet = [b(os.path.join("complet", n + ".json")) for n in ("communes", "cantons", "circos", "deps")]
    ms("-i", *complet, "combine-files",
       "-simplify", SIMPL_DETAIL, "keep-shapes",
       "-o", b("detail") + os.sep, "format=geojson", "precision=0.00001", "target=communes,cantons,circos,deps")
    ms("-i", *complet[1:], "combine-files",
       "-simplify", SIMPL_LEGER, "keep-shapes",
       "-o", b("leger") + os.sep, "format=geojson", "precision=0.0001", "target=cantons,circos,deps")
    for n in ("communes_lbl", "cantons_lbl", "circos_lbl"):
        os.replace(b(os.path.join("complet", n + ".json")), b(os.path.join("detail", n + ".json")))

    # ---- 4. Quartiers = IRIS des communes découpées (hors type Z) ----
    ms("-i", s("iris.geojson"),
       "-filter", "code_insee.slice(0,2) < '97'",
       "-each", "dep=code_insee.slice(0,2), nom=nom_iris, com=code_insee, nomcom=nom_commune, "
                "delete code_iris, delete nom_iris, delete code_insee, delete nom_commune, delete type_iris",
       "-simplify", "30%", "weighted", "keep-shapes",
       "-points", "inner", "+", "name=iris_lbl",
       "-o", b("iris") + os.sep, "format=geojson", "precision=0.00001", "target=iris,iris_lbl")

    D = lambda n: load(os.path.join(TMP, "detail", n + ".json"))["features"]
    L = lambda n: load(os.path.join(TMP, "leger", n + ".json"))["features"]
    communes, cantons, circos, deps_d = D("communes"), D("cantons"), D("circos"), D("deps")
    communes_lbl, cantons_lbl, circos_lbl = D("communes_lbl"), D("cantons_lbl"), D("circos_lbl")
    deps_l, cantons_l, circos_l = L("deps"), L("cantons"), L("circos")
    iris = load(os.path.join(TMP, "iris", "iris.json"))["features"]
    iris_lbl = load(os.path.join(TMP, "iris", "iris_lbl.json"))["features"]

    # ---- 5. Attributs lisibles ----
    nom_dep = {}
    for p in infos_com.values():
        nom_dep.setdefault(p["code_insee_du_departement"], None)
    for p in infos_cir.values():
        nom_dep[p["codeDepartement"]] = p["nomDepartement"]

    def props_canton(p):
        i = infos_can.get(p["can"], {})
        return {"code": p["can"], "nom": i.get("nom_officiel", p["can"]), "dep": p["dep"], "ci": p.get("ci")}

    def props_circo(p):
        num = int(p["cir"][-2:])
        return {"code": p["cir"], "dep": p["dep"], "num": num, "court": f"{p['dep']}-{num:02d}",
                "nom": f"{nom_dep.get(p['dep'], p['dep'])} — {ordinal(num)} circonscription", "ci": p.get("ci")}

    for lst in (cantons, cantons_l):
        for f in lst:
            f["properties"] = props_canton(f["properties"])
    for lst in (circos, circos_l):
        for f in lst:
            f["properties"] = props_circo(f["properties"])
    for f in deps_l:
        f["properties"] = {"code": f["properties"]["dep"], "nom": nom_dep.get(f["properties"]["dep"])}

    sans_canton = {
        "75": "Ville de Paris : pas de canton (conseil de Paris)",
        "69": "Métropole de Lyon : pas de canton (conseil de la Métropole)",
    }
    for f in communes:
        p = f["properties"]
        i = infos_com[p["com"]]
        cans = cantons_com[p["com"]]
        f["properties"] = q = {"code": p["com"], "nom": i["nom_officiel"], "dep": p["dep"],
                               "pop": i.get("population"), "cantons": cans, "circos": circos_com[p["com"]]}
        if not cans:
            q["sanscanton"] = sans_canton.get(p["dep"], "Pas de canton")

    nb_iris = collections.Counter(f["properties"]["com"] for f in iris)
    # arrondissements municipaux -> commune
    for code in list(nb_iris):
        if code.startswith("751"):
            nb_iris["75056"] += nb_iris[code]
        elif code.startswith("6938"):
            nb_iris["69123"] += nb_iris[code]
        elif code.startswith("132") and 13201 <= int(code) <= 13216:
            nb_iris["13055"] += nb_iris[code]
    for f in communes:
        f["properties"]["quartiers"] = nb_iris.get(f["properties"]["code"], 0)

    # ---- 6. Écriture : national (léger) + par département (détaillé) ----
    par_dep = collections.defaultdict(lambda: collections.defaultdict(list))
    for f in communes:
        par_dep[f["properties"]["dep"]]["communes"].append(f)
    for f in communes_lbl:
        p = f["properties"]
        par_dep[p["dep"]]["communes"].append(label(f["geometry"]["coordinates"], nom=infos_com[p["com"]]["nom_officiel"]))
    for f in iris:
        par_dep[f["properties"]["dep"]]["iris"].append(f)
    for f in iris_lbl:
        par_dep[f["properties"]["dep"]]["iris"].append(label(f["geometry"]["coordinates"], nom=f["properties"]["nom"]))
    for f in cantons:
        par_dep[f["properties"]["dep"]]["cantons"].append(f)
    for f in circos:
        par_dep[f["properties"]["dep"]]["circos"].append(f)
    for f in deps_d:
        dep = f["properties"]["dep"]
        f["properties"] = {"code": dep}
        par_dep[dep]["departement"].append(f)
    for dep, couches in sorted(par_dep.items()):
        for nom in ("communes", "iris", "cantons", "circos", "departement"):
            dump(fc(couches.get(nom, [])), os.path.join(OUT, "dep", dep, f"{nom}.json"))

    can_lbl = [label(f["geometry"]["coordinates"], code=f["properties"]["can"],
                     nom=infos_can.get(f["properties"]["can"], {}).get("nom_officiel", ""))
               for f in cantons_lbl]
    cir_lbl = [label(f["geometry"]["coordinates"], code=f["properties"]["cir"],
                     court=f"{f['properties']['dep']}-{int(f['properties']['cir'][-2:]):02d}")
               for f in circos_lbl]
    dump(fc(deps_l), os.path.join(OUT, "departements.json"))
    dump(fc(cantons_l + can_lbl), os.path.join(OUT, "cantons.json"))
    dump(fc(circos_l + cir_lbl), os.path.join(OUT, "circonscriptions.json"))

    index = sorted(([p["code"], p["nom"], p["dep"], bbox(f["geometry"]), p["cantons"], p["circos"]]
                    for f in communes for p in [f["properties"]]), key=lambda c: c[0])
    dump({"deps": {f["properties"]["code"]: {"nom": f["properties"]["nom"], "bbox": bbox(f["geometry"])} for f in deps_l},
          "cantons": {f["properties"]["code"]: f["properties"]["nom"] for f in cantons},
          "circos": {f["properties"]["code"]: f["properties"]["nom"] for f in circos},
          "communes": index},
         os.path.join(OUT, "index.json"))

    # ---- Contrôles ----
    print(f"départements={len(deps_l)} cantons={len(cantons)} circos={len(circos)} "
          f"communes={len(communes)} iris={len(iris)}")
    sans = [c for c in index if not c[4]]
    print("communes sans canton :", collections.Counter(c[2] for c in sans))
    print("communes sans circo :", [c[1] for c in index if not c[5]][:10])
    print("partagées cantons :", sum(1 for c in index if len(c[4]) > 1),
          "| circos :", sum(1 for c in index if len(c[5]) > 1))
    print("plus découpées :", [(c[1], len(c[4]), len(c[5])) for c in sorted(index, key=lambda c: -len(c[4]))[:5]])
    print("deps sans nom :", [f["properties"]["code"] for f in deps_l if not f["properties"]["nom"]])


if __name__ == "__main__":
    sys.exit(main())
