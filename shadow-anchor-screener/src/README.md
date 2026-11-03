# Shadow-Anchor Screener

Finds the retail centers in a market that sit next to a big-box "shadow anchor" (Target, Walmart, Lowe's, Home Depot or Costco) and ranks them. Built from RCG's thesis: *"go follow the retailers."*

```bash
python3 screener.py "Hoover, AL"         # -> out/hoover-al.html (+ .json)
python3 screener.py "Little Rock, AR" --pad 2
```

Uses only the Python standard library. It needs internet access for the data pull and for the map tiles.

## How it works
1. Geocodes the market (Nominatim) and widens the search box by `--pad` miles.
2. Pulls from OpenStreetMap through Overpass, falling back across mirrors:
   - big-box anchors, matched by brand;
   - retail footprints (`landuse=retail`, `shop=mall`);
   - named shops and restaurants.
3. For each footprint of 3 acres or more, measures the distance from its edge to every anchor. A center counts if an anchor is within ¼ mi.
   - **Next door:** the anchor is outside the footprint, which is the classic shadow anchor.
   - **On site:** the anchor is inside the footprint. It often owns its own parcel anyway.
4. Ranks centers. The ranking favors more distinct anchor brands, anchors that are next door rather than on site, closeness, acreage and tenant count. The weights are at the bottom of `run()`.
5. Writes a single-file interactive map. Clicking a center zooms to it in satellite view and lists its mapped tenants.

## Limits
- OSM footprints and tenant lists are crowd-sourced and incomplete.
- No ownership, rent, sales or traffic data. Treat the output as a first-pass screen.
- Results are cached in `cache/`. Delete that folder to pull fresh data.

## Tested
- **Hoover, AL:** Patton Creek ranks #1, with Costco 418 ft away and Home Depot 679 ft away.
- **Mansfield, OH:** 5 centers.
