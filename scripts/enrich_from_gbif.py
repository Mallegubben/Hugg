#!/usr/bin/env python3
"""
Berika FVO-artlistor via GBIF-förekomster inom respektive FVO-omslutning (WGS84).

Använder endast våra kartlagda svenska fiskarter (taxonKey). Ingen iFiske-data.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from shapely.ops import transform
import pyproj

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
KEYS = RAW / "gbif_species_keys.json"
UA = "HuggFVOBot/1.0 (https://github.com/Mallegubben/Hugg; research)"


def to_wgs84_envelope_wkt(geom) -> str | None:
    if geom is None or geom.is_empty:
        return None
    project = pyproj.Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    g = transform(project, geom)
    # Förenkla stora polygoner via envelope (snabbare/stabilare GBIF-frågor)
    env = g.envelope
    return env.wkt


def query_gbif(species_keys: list[int], wkt: str) -> dict[str, int]:
    params = [
        ("country", "SE"),
        ("hasCoordinate", "true"),
        ("limit", "0"),
        ("facet", "taxonKey"),
        ("facetLimit", "300"),
        ("geometry", wkt),
    ]
    for k in species_keys:
        params.append(("taxonKey", str(k)))
    url = "https://api.gbif.org/v1/occurrence/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.load(resp)
    counts: dict[str, int] = {}
    for fac in data.get("facets") or []:
        if fac.get("field") != "TAXON_KEY":
            continue
        for c in fac.get("counts") or []:
            counts[str(c["name"])] = int(c.get("count") or 0)
    return counts


def main() -> None:
    keys_raw = json.loads(KEYS.read_text(encoding="utf-8"))
    key_to_sv = {str(v["key"]): v["sv"] for v in keys_raw.values() if v.get("key")}
    species_keys = [int(k) for k in key_to_sv.keys()]

    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]

    geom = gpd.read_file(RAW / "fiskekartan_fvof" / "lstext.fiskekartan_fvof.gpkg")[
        ["ORIGINALID", "geometry"]
    ]
    geom_by_id = {
        int(r.ORIGINALID): r.geometry
        for _, r in geom.iterrows()
        if r.ORIGINALID is not None
    }

    # Prioritera FVO utan/få arter
    todo = sorted(
        fvos,
        key=lambda f: (
            0 if not f.get("arter") else 1,
            f.get("statistik", {}).get("antal_arter", 0),
            f.get("namn") or "",
        ),
    )

    results: dict[int, list[str]] = {}
    errors = 0

    def work(fvo: dict) -> tuple[int | None, list[str], str | None]:
        oid = fvo.get("original_id")
        if oid is None or oid not in geom_by_id:
            return oid, [], "no_geom"
        wkt = to_wgs84_envelope_wkt(geom_by_id[oid])
        if not wkt:
            return oid, [], "empty_geom"
        try:
            counts = query_gbif(species_keys, wkt)
        except Exception as e:
            return oid, [], type(e).__name__
        found = sorted(
            {key_to_sv[k] for k, n in counts.items() if k in key_to_sv and n > 0},
            key=lambda s: s.casefold(),
        )
        return oid, found, None

    print(f"GBIF-berikar {len(todo)} FVO…")
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(work, f) for f in todo]
        done = 0
        for fut in as_completed(futs):
            oid, found, err = fut.result()
            done += 1
            if err:
                errors += 1
            elif oid is not None and found:
                results[oid] = found
            if done % 100 == 0:
                print(f"  {done}/{len(todo)} träffar={len(results)} fel={errors}")
            time.sleep(0.01)

    enriched = 0
    added_total = 0
    for fvo in fvos:
        oid = fvo.get("original_id")
        found = results.get(oid) or []
        if not found:
            continue
        before = set(fvo.get("arter") or [])
        extra = [a for a in found if a not in before]
        if not extra and before:
            # still record gbif confirmation? skip if nothing new
            continue
        after = sorted(before | set(found), key=lambda s: s.casefold())
        if after != sorted(before, key=lambda s: s.casefold()):
            fvo["arter"] = after
            fvo.setdefault("kallor", [])
            if "gbif" not in fvo["kallor"]:
                fvo["kallor"].append("gbif")
            fvo["gbif_enrichment"] = {
                "arter_i_omslutning": found,
                "tillagda_arter": extra,
            }
            fvo["statistik"]["antal_arter"] = len(after)
            enriched += 1
            added_total += len(extra)

    # coverage
    catalog: set[str] = set()
    coverage = {
        "har_fiskekartan_arter": 0,
        "har_survey_arter": 0,
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_vattenposter": 0,
        "har_extern_enrichment": 0,
        "har_gbif_enrichment": 0,
    }
    for f in fvos:
        catalog.update(f.get("arter") or [])
        if f.get("fiskekartan", {}).get("arter"):
            coverage["har_fiskekartan_arter"] += 1
        if "slu_provfiske" in (f.get("kallor") or []):
            coverage["har_survey_arter"] += 1
        if f.get("arter"):
            coverage["har_nagon_art"] += 1
        else:
            coverage["saknar_arter"] += 1
        if f.get("vatten"):
            coverage["har_vattenposter"] += 1
        if f.get("extern_enrichment"):
            coverage["har_extern_enrichment"] += 1
        if f.get("gbif_enrichment"):
            coverage["har_gbif_enrichment"] += 1

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["gbif_enrichment"] = {
        "fvo_uppdaterade": enriched,
        "artposter_tillagda": added_total,
        "gbif_fel": errors,
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    # ensure gbif listed as source
    ids = {k["id"] for k in data["meta"].get("kallor", [])}
    if "gbif" not in ids:
        data["meta"].setdefault("kallor", []).append(
            {
                "id": "gbif",
                "beskrivning": "GBIF-förekomster av kartlagda fiskarter inom FVO-omslutning",
                "url": "https://www.gbif.org/",
            }
        )

    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "gbif_enrichment_log.json").write_text(
        json.dumps(
            {str(k): v for k, v in results.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "enriched_fvo": enriched,
                "added": added_total,
                "errors": errors,
                "coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
