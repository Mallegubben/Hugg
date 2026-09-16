#!/usr/bin/env python3
"""
Riktad GBIF-berikning mot spegelluckor / tomma FVO.

iFiske används bara för att veta *vilka* luckor som finns (kontrollmeta).
Artdata hämtas uteslutande från GBIF inom FVO-omslutning.
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
import pyproj
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
KEYS = RAW / "gbif_species_keys.json"
UA = "HuggFVOBot/1.0 (https://github.com/Mallegubben/Hugg; gbif-gaps)"


def to_wgs84_envelope_wkt(geom) -> str | None:
    if geom is None or geom.is_empty:
        return None
    project = pyproj.Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    g = transform(project, geom)
    return g.envelope.wkt


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

    todo = [
        f
        for f in fvos
        if (f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof")
        or not f.get("arter")
    ]
    # Prioritera de utan GBIF tidigare
    todo.sort(
        key=lambda f: (
            0 if not f.get("gbif_enrichment") else 1,
            0 if not f.get("arter") else 1,
            -len((f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof") or []),
            f.get("namn") or "",
        )
    )
    print(f"GBIF mot luckor/tomma: {len(todo)} FVO…")

    results: dict[int, list[str]] = {}
    errors = 0
    no_geom = 0

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

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(work, f): f for f in todo}
        done = 0
        for fut in as_completed(futs):
            oid, found, err = fut.result()
            done += 1
            if err == "no_geom" or err == "empty_geom":
                no_geom += 1
            elif err:
                errors += 1
            elif oid is not None and found:
                results[oid] = found
            if done % 50 == 0:
                print(f"  {done}/{len(todo)} träffar={len(results)} fel={errors}")
            time.sleep(0.015)

    enriched = 0
    added_total = 0
    gaps_closed = 0
    for fvo in fvos:
        oid = fvo.get("original_id")
        found = results.get(oid) or []
        if not found:
            continue
        before = set(fvo.get("arter") or [])
        extra = [a for a in found if a not in before]
        if not extra:
            continue
        after = sorted(before | set(found), key=lambda s: s.casefold())
        fvo["arter"] = after
        fvo.setdefault("kallor", [])
        if "gbif" not in fvo["kallor"]:
            fvo["kallor"].append("gbif")
        prev = fvo.get("gbif_enrichment") or {}
        fvo["gbif_enrichment"] = {
            "arter_i_omslutning": sorted(
                set(prev.get("arter_i_omslutning") or []) | set(found),
                key=lambda s: s.casefold(),
            ),
            "tillagda_arter": sorted(
                set(prev.get("tillagda_arter") or []) | set(extra),
                key=lambda s: s.casefold(),
            ),
        }
        fvo["statistik"]["antal_arter"] = len(after)
        enriched += 1
        added_total += len(extra)

        ctrl = fvo.get("ifiske_spegel_kontroll")
        if ctrl and ctrl.get("spegel_arter"):
            prev_gaps = set(ctrl.get("luckor_ej_funna_hos_fvof") or [])
            still = sorted(set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold())
            gaps_closed += len(prev_gaps - set(still))
            ctrl["luckor_ej_funna_hos_fvof"] = still

    # coverage + spegelstats
    catalog: set[str] = set()
    coverage = {
        "har_fiskekartan_arter": 0,
        "har_survey_arter": 0,
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_vattenposter": 0,
        "har_extern_enrichment": 0,
        "har_gbif_enrichment": 0,
        "har_ifiske_spegel_kontroll": 0,
    }
    spegel = {"battre": 0, "lika": 0, "samre": 0, "med_spegel": 0, "kvar_luckor": 0}
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
        if f.get("ifiske_spegel_kontroll"):
            coverage["har_ifiske_spegel_kontroll"] += 1
        ctrl = f.get("ifiske_spegel_kontroll") or {}
        mirror = set(ctrl.get("spegel_arter") or [])
        if not mirror:
            continue
        spegel["med_spegel"] += 1
        our = set(f.get("arter") or [])
        if our >= mirror and len(our) > len(mirror):
            spegel["battre"] += 1
        elif our >= mirror:
            spegel["lika"] += 1
        else:
            spegel["samre"] += 1
            spegel["kvar_luckor"] += len(mirror - our)

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["gbif_gap_pass"] = {
        "enriched_fvo": enriched,
        "added_species_entries": added_total,
        "mirror_gaps_closed": gaps_closed,
        "errors": errors,
        "no_geom": no_geom,
        "spegel_stats": spegel,
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    import csv

    gaps = []
    for f in fvos:
        luckor = (f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof") or []
        if luckor:
            gaps.append(
                {
                    "namn": f["namn"],
                    "lan": f.get("ansvarigt_lan"),
                    "luckor": "; ".join(luckor),
                    "url_fvof": f.get("url_fvof") or "",
                    "spegel_url": (f.get("ifiske_spegel_kontroll") or {}).get("url") or "",
                }
            )
    with (OUT / "luckor_mot_ifiske_spegel.csv").open("w", encoding="utf-8", newline="") as fh:
        if gaps:
            w = csv.DictWriter(fh, fieldnames=list(gaps[0].keys()))
            w.writeheader()
            w.writerows(gaps)

    (OUT / "gbif_gap_pass_log.json").write_text(
        json.dumps(
            {
                "results": {str(k): v for k, v in results.items()},
                "enriched_fvo": enriched,
                "added": added_total,
                "gaps_closed": gaps_closed,
                "errors": errors,
            },
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
                "gaps_closed": gaps_closed,
                "remaining_gap_fvo": len(gaps),
                "errors": errors,
                "spegel_stats": spegel,
                "coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
