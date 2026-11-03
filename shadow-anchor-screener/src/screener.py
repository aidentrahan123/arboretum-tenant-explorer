#!/usr/bin/env python3
"""Shadow-Anchor Screener.

Finds retail centers in a market that sit next to a "shadow anchor"
(Target, Walmart, Lowe's, Home Depot, Costco) and ranks them.

Usage:
    python3 screener.py "Hoover, AL"            # writes out/hoover-al.html
    python3 screener.py "Hoover, AL" --pad 3    # widen search by 3 miles

Data: OpenStreetMap (Overpass API) + Nominatim geocoder. Standard library only.
"""
import argparse, json, math, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "out")
UA = "shadow-anchor-screener/0.1 (student project)"
ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Anchor brands, matched on OSM brand / name tags.
ANCHORS = {
    "Costco":     re.compile(r"^costco", re.I),
    "Target":     re.compile(r"^(super )?target$", re.I),
    "Walmart":    re.compile(r"^walmart( supercenter)?$", re.I),
    "Lowe's":     re.compile(r"^lowe[’']?s( home improvement)?$", re.I),
    "Home Depot": re.compile(r"^(the )?home depot$", re.I),
}
MI = 1609.344
SHADOW_MI = 0.25      # "next to" = within a quarter mile of the center's edge
MIN_ACRES = 3.0       # ignore tiny retail parcels


# ---------- data fetch ----------
def fetch(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def cached(key, fn):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, re.sub(r"[^a-z0-9]+", "-", key.lower()) + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    val = fn()
    json.dump(val, open(path, "w"))
    return val


def overpass(query):
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                txt = fetch(ep, body)
                if txt.lstrip().startswith("{"):
                    return json.loads(txt)
                last = txt[:120]
            except Exception as e:  # busy / timeout -> next mirror
                last = str(e)
        time.sleep(5 * (attempt + 1))
    sys.exit(f"Overpass unavailable: {last}")


def geocode(place):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": place, "format": "json", "limit": 1})
    res = json.loads(fetch(url))
    if not res:
        sys.exit(f"Could not geocode {place!r}")
    s, n, w, e = map(float, res[0]["boundingbox"])
    return res[0]["display_name"], (s, w, n, e)


# ---------- geometry (local flat projection, fine at metro scale) ----------
class Proj:
    def __init__(self, lat0):
        self.kx = 111320 * math.cos(math.radians(lat0))
        self.ky = 110540

    def xy(self, lat, lon):
        return lon * self.kx, lat * self.ky


def ring_area(pts):
    a = 0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def point_in_ring(x, y, pts):
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def dist_to_rings(x, y, rings):
    """0 if inside any ring, else metres to the nearest edge."""
    if any(point_in_ring(x, y, r) for r in rings):
        return 0.0
    return min(seg_dist(x, y, *a, *b) for r in rings for a, b in zip(r, r[1:] + r[:1]))


def rings_of(el):
    """Outer rings (lat/lon lists) for an OSM way or multipolygon relation."""
    if el["type"] == "way" and "geometry" in el:
        return [[(p["lat"], p["lon"]) for p in el["geometry"]]]
    if el["type"] == "relation":
        out = []
        for m in el.get("members", []):
            if m.get("role") == "outer" and "geometry" in m:
                out.append([(p["lat"], p["lon"]) for p in m["geometry"]])
        return out
    return []


def center_of(el):
    if "lat" in el:
        return el["lat"], el["lon"]
    if "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    g = el.get("geometry") or []
    if g:
        return sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g)
    return None


# ---------- screener ----------
def anchor_brand(tags):
    for field in ("brand", "name"):
        v = (tags.get(field) or "").strip()
        for brand, rx in ANCHORS.items():
            if rx.match(v):
                return brand
    return None


def run(place, pad_mi):
    label, (s, w, n, e) = cached("geo " + place, lambda: geocode(place))
    dlat = pad_mi * MI / 110540
    dlon = pad_mi * MI / (111320 * math.cos(math.radians((s + n) / 2)))
    bb = f"{s - dlat:.5f},{w - dlon:.5f},{n + dlat:.5f},{e + dlon:.5f}"

    print(f"Market: {label}\nSearch box: {bb}")
    anchors_raw = cached("anchors " + bb, lambda: overpass(
        f'[out:json][timeout:120];'
        f'(nwr["brand"~"Costco|Target|Walmart|Lowe|Home Depot",i]({bb});'
        f' nwr["name"~"^(Costco|Target|Super Target|Walmart|Walmart Supercenter|Lowe.s|The Home Depot|Home Depot)",i]({bb}););'
        f'out tags center;'))
    areas_raw = cached("areas " + bb, lambda: overpass(
        f'[out:json][timeout:180];'
        f'(way["landuse"="retail"]({bb}); relation["landuse"="retail"]({bb});'
        f' way["shop"="mall"]({bb}); relation["shop"="mall"]({bb}););'
        f'out tags geom;'))
    shops_raw = cached("shops " + bb, lambda: overpass(
        f'[out:json][timeout:180];'
        f'(nwr["shop"]({bb}); nwr["amenity"~"^(restaurant|fast_food|cafe|bank|pharmacy|cinema)$"]({bb}););'
        f'out tags center;'))

    lat0 = (s + n) / 2
    P = Proj(lat0)

    # Anchors: keep store-like features, de-dupe by brand + ~150 m.
    anchors = []
    for el in anchors_raw["elements"]:
        t = el.get("tags", {})
        b = anchor_brand(t)
        c = center_of(el)
        if not b or not c:
            continue
        if t.get("amenity") == "fuel" or t.get("shop") in ("car_repair", "tyres") \
                or "pharmacy" in (t.get("name") or "").lower() or t.get("amenity") == "pharmacy":
            continue  # skip Costco gas, Target pharmacy nodes, tire centers
        if "neighborhood market" in (t.get("name") or "").lower():
            continue  # small-format Walmart, not a big-box shadow anchor
        x, y = P.xy(*c)
        if any(a["brand"] == b and math.hypot(a["x"] - x, a["y"] - y) < 150 for a in anchors):
            continue
        anchors.append({"brand": b, "name": t.get("name") or b, "lat": c[0], "lon": c[1], "x": x, "y": y,
                        "addr": " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street")]))})

    # Shops (for naming unnamed areas + counting tenants).
    shops = []
    for el in shops_raw["elements"]:
        t = el.get("tags", {})
        c = center_of(el)
        if c and t.get("name"):
            x, y = P.xy(*c)
            shops.append({"name": t["name"], "x": x, "y": y, "brand": anchor_brand(t)})

    # Candidate centers.
    centers = []
    for el in areas_raw["elements"]:
        rings_ll = rings_of(el)
        if not rings_ll:
            continue
        rings = [[P.xy(*p) for p in r[:-1] if True] for r in rings_ll]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            continue
        acres = sum(ring_area(r) for r in rings) / 4046.86
        if acres < MIN_ACRES:
            continue
        t = el.get("tags", {})

        tenants = sorted({sh["name"] for sh in shops if any(point_in_ring(sh["x"], sh["y"], r) for r in rings)})

        hits = []
        for a in anchors:
            d = dist_to_rings(a["x"], a["y"], rings)
            if d <= SHADOW_MI * MI:
                hits.append({"brand": a["brand"], "dist_ft": round(d * 3.28084), "inside": d == 0,
                             "lat": a["lat"], "lon": a["lon"]})
        if not hits:
            continue
        hits.sort(key=lambda h: h["dist_ft"])

        name = t.get("name")
        named = bool(name)
        if not name:
            non_anchor = [x for x in tenants if not anchor_brand({"name": x})]
            name = ("Retail area: " + ", ".join(non_anchor[:3])) if non_anchor else "Unnamed retail area"

        brands = sorted({h["brand"] for h in hits})
        shadow = [h for h in hits if not h["inside"]]
        # Ranking: distinct anchor brands nearby, shadow (not owned-in) anchors, closeness, then size.
        score = (len(brands) * 30
                 + len({h["brand"] for h in shadow}) * 10
                 + max(0, 20 - hits[0]["dist_ft"] / 66)       # up to 20 pts for being right next door
                 + min(acres, 40) / 2                          # up to 20 pts for size
                 + min(len(tenants), 30) / 3)                  # up to 10 pts for tenant depth
        c = center_of(el) or rings_ll[0][0]
        centers.append({
            "id": f"{el['type']}/{el['id']}", "name": name, "named": named,
            "acres": round(acres, 1), "tenant_count": len(tenants), "tenants": tenants[:40],
            "anchors": hits, "brands": brands,
            "shadow_only": all(not h["inside"] for h in hits),
            "score": round(score, 1),
            "lat": c[0], "lon": c[1],
            "rings": [[[round(p[0], 6), round(p[1], 6)] for p in r] for r in rings_ll],
        })

    centers.sort(key=lambda c: -c["score"])
    for i, c in enumerate(centers, 1):
        c["rank"] = i
    return {
        "market": place, "label": label, "bbox": bb,
        "params": {"shadow_mi": SHADOW_MI, "min_acres": MIN_ACRES},
        "generated": time.strftime("%Y-%m-%d"),
        "anchors": [{k: a[k] for k in ("brand", "name", "lat", "lon", "addr")} for a in anchors],
        "centers": centers,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("place")
    ap.add_argument("--pad", type=float, default=1.0, help="miles to widen the search box")
    args = ap.parse_args()
    data = run(args.place, args.pad)
    os.makedirs(OUT, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", args.place.lower()).strip("-")
    json.dump(data, open(os.path.join(OUT, slug + ".json"), "w"), indent=1)
    tpl = open(os.path.join(HERE, "template.html")).read()
    # Inline Leaflet so the page works even if the CDN is slow (map tiles still need internet).
    vend = os.path.join(HERE, "vendor")
    if os.path.exists(os.path.join(vend, "leaflet.min.js")):
        js = open(os.path.join(vend, "leaflet.min.js")).read()
        css = open(os.path.join(vend, "leaflet.min.css")).read()
        tpl = tpl.replace('<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">',
                          "<style>" + css + "</style>")
        tpl = tpl.replace('<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>',
                          "<script>" + js.replace("</script", "<\\/script") + "</script>")
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    open(os.path.join(OUT, slug + ".html"), "w").write(html)
    print(f"\n{len(data['anchors'])} anchors, {len(data['centers'])} shadow-anchored centers")
    for c in data["centers"][:15]:
        a = ", ".join(f"{h['brand']} {'(inside)' if h['inside'] else str(h['dist_ft']) + ' ft'}" for h in c["anchors"][:3])
        print(f"{c['rank']:>3}. {c['name'][:48]:<48} {c['acres']:>6} ac {c['tenant_count']:>3} tenants | {a}")
    print(f"\nWrote out/{slug}.html")


if __name__ == "__main__":
    main()
