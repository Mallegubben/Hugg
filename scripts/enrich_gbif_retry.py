#!/usr/bin/env python3
"""Retry GBIF enrichment for FVO that still lack species (sequential, with backoff)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pyproj
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
UA = "HuggFVOBot/1.0 (https://github.com/Mallegubben/Hugg; research)"


def to_wgs84_envelope_wkt(geom) -> str | None:
    if geom is None or geom.is_empty:
        return None
    project = pyproj.Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    return transform(project, geom).envelope.wkt


def query_gbif(species_keys: list[int], wkt: str, retries: int = 5) -> dict[str, int]:
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
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.load(resp)
            counts: dict[str, int] = {}
            for fac in data.get("facets") or []:
                if fac.get("field") != "TAXON_KEY":
                    continue
                for c in fac.get("counts") or []:
                    counts[str(c["name"])] = int(c.get("count") or 0)
            return counts
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
    return {}


def main() -> None:
    keys_raw = json.loads((RAW / "gbif_species_keys.json").read_text(encoding="utf-8"))
    key_to_sv = {str(v["key"]): v["sv"] for v in keys_raw.values() if v.get("key")}
    species_keys = [int(k) for k in key_to_sv]

    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    geom = gpd.read_file(RAW / "fiskekartan_fvof" / "lstext.fiskekartan_fvof.gpkg")[
        ["ORIGINALID", "geometry"]
    ]
    geom_by_id = {
        int(r.ORIGINALID): r.geometry for _, r in geom.iterrows() if r.ORIGINALID is not None
    }

    # Focus on gaps + thin lists (<3 species) without prior GBIF success
    todo = [
        f
        for f in fvos
        if f.get("original_id") in geom_by_id
        and (
            not f.get("arter")
            or (
                len(f.get("arter") or []) < 3
                and not f.get("gbif_enrichment")
            )
        )
    ]
    print(f"GBIF retry för {len(todo)} FVO…")

    enriched = 0
    added_total = 0
    empty = 0
    errors = 0

    for i, fvo in enumerate(todo, 1):
        oid = fvo["original_id"]
        wkt = to_wgs84_envelope_wkt(geom_by_id[oid])
        if not wkt:
            errors += 1
            continue
        try:
            counts = query_gbif(species_keys, wkt)
        except Exception as e:
            errors += 1
            print(f"  fel {oid} {fvo.get('namn')}: {type(e).__name__}")
            time.sleep(1)
            continue

        found = sorted(
            {key_to_sv[k] for k, n in counts.items() if k in key_to_sv and n > 0},
            key=lambda s: s.casefold(),
        )
        if not found:
            empty += 1
        else:
            before = set(fvo.get("arter") or [])
            extra = [a for a in found if a not in before]
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

        if i % 25 == 0:
            print(f"  {i}/{len(todo)} enriched={enriched} empty={empty} errors={errors}")
        time.sleep(0.35)

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
    data["meta"]["gbif_retry"] = {
        "fvo_uppdaterade": enriched,
        "artposter_tillagda": added_total,
        "tomma_omslutningar": empty,
        "fel": errors,
        "kandidater": len(todo),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"enriched": enriched, "added": added_total, "coverage": coverage}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
