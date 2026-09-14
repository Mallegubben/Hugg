#!/usr/bin/env python3
"""
Bygg artlista för Sveriges FVO med Fiskekartan som ram.

Källor:
- Fiskekartan REST (primär FVO-katalog + VANL_ART/OVRI_ART)
- Fiskekartan GeoPackage (geometri för spatial join)
- SLU NORS (sjöprovfiske, sjö-nivå)
- SLU SERS (elfiske, vattendrag-nivå)

iFiske används aldrig som artdata. URL_FKORT till iFiske sparas endast
så att externa länkar kan hämtas i enrich_from_external_links.py.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
REST_URL = (
    "https://ext-geodata-applikationer.lansstyrelsen.se/arcgis/rest/services/"
    "SvenskaFiskekartan/lst_svenskafiskekartan_webbgis/MapServer/1/query"
)

# Normalisering av artnamn (svenska trivialnamn)
SPECIES_ALIASES = {
    "gers": "Gärs",
    "gärss": "Gärs",
    "gärs": "Gärs",
    "öring": "Öring",
    "oring": "Öring",
    "regnbåge": "Regnbåge",
    "regnbågslax": "Regnbåge",
    "rainbow": "Regnbåge",
    "bäckröding": "Bäckröding",
    "kanadaröding": "Kanadaröding",
    "röding": "Röding",
    "abborre": "Abborre",
    "gädda": "Gädda",
    "mört": "Mört",
    "lake": "Lake",
    "braxen": "Braxen",
    "sutare": "Sutare",
    "gös": "Gös",
    "sik": "Sik",
    "siklöja": "Siklöja",
    "ål": "Ål",
    "harr": "Harr",
    "benlöja": "Benlöja",
    "löja": "Benlöja",
    "sarv": "Sarv",
    "ruda": "Ruda",
    "id": "Id",
    "björkna": "Björkna",
    "nors": "Nors",
    "lax": "Lax",
    "elritsa": "Elritsa",
    "bergsimpa": "Bergsimpa",
    "stensimpa": "Stensimpa",
    "havsöring": "Havsöring",
    "färna": "Färna",
    "karp": "Karp",
    "asp": "Asp",
    "mal": "Mal",
    "grönling": "Grönling",
    "flodnejonöga": "Flodnejonöga",
    "bäcknejonöga": "Bäcknejonöga",
    "nejonöga": "Nejonöga",
    "nejonöga obestämd": "Nejonöga",
    "simpa (berg-/sten-)": "Simpa",
    "simpa": "Simpa",
    "signalkräfta": "Signalkräfta",
    "flodkräfta": "Flodkräfta",
    "kräfta": "Kräfta",
    "småspigg": "Småspigg",
    "storspigg": "Storspigg",
    "hornsimpa": "Hornsimpa",
    "nissöga": "Nissöga",
    "groplöja": "Groplöja",
    "vimma": "Vimma",
    "färna": "Färna",
    "stäm": "Stäm",
    "färna": "Färna",
    "skärkniv": "Skärkniv",
    "piggvar": "Piggvar",
    "tånglake": "Tånglake",
    "skrämma": "Skrämma",
    "färna": "Färna",
}

NON_SPECIES = {
    "",
    "-",
    "ingen fångst",
    "inga",
    "okänt",
    "okand",
    "okänd",
    "m fl",
    "m.fl",
    "m.fl.",
    "mm",
    "m.m",
    "etc",
    "nan",
    "none",
    "null",
}


def normalize_species_token(raw: str) -> str | None:
    t = raw.strip()
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" .;:()[]")
    if not t:
        return None
    low = t.lower()
    if low in NON_SPECIES or low.startswith("ingen ") or low == "nan":
        return None
    if low in SPECIES_ALIASES:
        return SPECIES_ALIASES[low]
    # Title-case Swedish names carefully
    if t[:1].islower():
        t = t[:1].upper() + t[1:]
    return t


def split_species_field(value: str | None) -> list[str]:
    if value is None:
        return []
    try:
        # pandas NaN / float nan
        if isinstance(value, float) and pd.isna(value):
            return []
    except Exception:
        pass
    try:
        import math

        if isinstance(value, float) and math.isnan(value):
            return []
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return []
    # Normalize compound tokens before splitting
    text = re.sub(
        r"Simpa\s*\(Berg-?/?\s*Sten-?\)",
        "Simpa",
        text,
        flags=re.I,
    )
    text = re.sub(r"Nejonöga\s+obestämd", "Nejonöga", text, flags=re.I)
    parts = re.split(r"[,;/]| och | & |\n", text)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = normalize_species_token(part)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def fetch_fiskekartan_rest() -> list[dict]:
    fields = (
        "OBJECTID,ORIGINALID,FOR_NAMN,FORENI_TYP,VANL_ART,VANL_ART_2,"
        "OVRI_ART,OVRI_ART_2,URL_FVOF,URL_FKORT,URL,ARRENDATOR,ANSV_LAN,"
        "ANSV_LANSKOD,LAN,LANSKOD,KNAMN,KKOD,HARO,HARO_2,BAT,BATRAMP,"
        "HANP_FISKE,UNG_FISKE,KRAFTFISKE,DJUPKARTA,REVDATUM"
    )
    params = {
        "where": "1=1",
        "outFields": fields,
        "returnGeometry": "false",
        "resultOffset": "0",
        "resultRecordCount": "5000",
        "f": "json",
    }
    url = REST_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = json.load(resp)
    return [f["attributes"] for f in data.get("features", [])]


def parse_sjo_name(sjo: str) -> tuple[str | None, str]:
    """NORS format: '624653-149911 Mörkegöl' -> (smhi_id, name)."""
    if not sjo:
        return None, ""
    m = re.match(r"^(\d{6}-\d{6})\s+(.*)$", sjo.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return None, sjo.strip()


def load_nors_points() -> gpd.GeoDataFrame:
    rows = json.loads((RAW / "nors_data_aggregerad.json").read_text(encoding="utf-8"))
    records = []
    for r in rows:
        n = r.get("sweref99N")
        e = r.get("sweref99E")
        if n is None or e is None:
            continue
        smhi_id, name = parse_sjo_name(r.get("sjö") or "")
        arts = split_species_field(r.get("fångadeArter"))
        if not arts:
            continue
        records.append(
            {
                "vatten_typ": "sjö",
                "vatten_namn": name,
                "smhi_id": smhi_id,
                "eu_cd": (r.get("eU_CD") or "").strip() or None,
                "lan": r.get("län"),
                "haro": r.get("haro"),
                "arter": arts,
                "kalla": "NORS",
                "senaste_fiskeaar": r.get("senasteFiskeår"),
                "nors_url": r.get("url"),
                "geometry": Point(float(e), float(n)),
            }
        )
    return gpd.GeoDataFrame(records, crs="EPSG:3006")


def load_sers_points() -> gpd.GeoDataFrame:
    rows = json.loads((RAW / "sers_data_aggregerad.json").read_text(encoding="utf-8"))
    records = []
    for r in rows:
        n = r.get("sweref99N")
        e = r.get("sweref99E")
        if n is None or e is None:
            continue
        arts = split_species_field(r.get("arter"))
        # Drop pure no-catch rows
        arts = [a for a in arts if a.lower() != "ingen fångst"]
        if not arts:
            continue
        records.append(
            {
                "vatten_typ": "vattendrag",
                "vatten_namn": (r.get("vattendrag") or "").strip(),
                "lokal": (r.get("lokal") or "").strip() or None,
                "lokal_id": r.get("lokalID"),
                "eu_cd": (r.get("eU_CD") or "").strip() or None,
                "lan": r.get("län"),
                "haro": r.get("huvudflodområde"),
                "arter": arts,
                "kalla": "SERS",
                "senaste_fiskeaar": r.get("senasteFiskeår"),
                "sers_url": r.get("url"),
                "geometry": Point(float(e), float(n)),
            }
        )
    return gpd.GeoDataFrame(records, crs="EPSG:3006")


def aggregate_vatten(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Aggregate survey points into unique waterbodies within one FVO."""
    if gdf.empty:
        return []
    groups: dict[tuple, dict] = {}
    for _, row in gdf.iterrows():
        key = (
            row.get("vatten_typ"),
            (row.get("vatten_namn") or "").lower(),
            row.get("smhi_id") or row.get("eu_cd") or row.get("lokal_id"),
        )
        if key not in groups:
            groups[key] = {
                "typ": row.get("vatten_typ"),
                "namn": row.get("vatten_namn") or None,
                "smhi_id": row.get("smhi_id"),
                "eu_cd": row.get("eu_cd"),
                "lokaler": set(),
                "arter": set(),
                "kallor": set(),
                "senaste_fiskeaar": None,
                "referenser": set(),
            }
        g = groups[key]
        g["arter"].update(row.get("arter") or [])
        g["kallor"].add(row.get("kalla"))
        lokal = row.get("lokal")
        if lokal is not None and str(lokal).strip() and str(lokal).lower() != "nan":
            g["lokaler"].add(str(lokal).strip())
        yr = row.get("senaste_fiskeaar")
        if yr is not None and str(yr).lower() != "nan" and (
            g["senaste_fiskeaar"] is None or yr > g["senaste_fiskeaar"]
        ):
            g["senaste_fiskeaar"] = yr
        for ref_key in ("nors_url", "sers_url"):
            ref = row.get(ref_key)
            if ref is not None and str(ref).strip() and str(ref).lower() != "nan":
                g["referenser"].add(str(ref).strip())

    out = []
    for g in groups.values():
        out.append(
            {
                "typ": g["typ"],
                "namn": g["namn"],
                "smhi_id": g["smhi_id"],
                "eu_cd": g["eu_cd"],
                "lokaler": sorted(g["lokaler"]) or None,
                "arter": sorted(g["arter"], key=lambda s: s.casefold()),
                "kallor": sorted(str(x) for x in g["kallor"]),
                "senaste_fiskeaar": g["senaste_fiskeaar"],
                "referenser": sorted(g["referenser"])[:5],
            }
        )
    out.sort(key=lambda x: ((x["namn"] or "").casefold(), x["typ"] or ""))
    return out


def clean_str(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    return s


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    print("Hämtar Fiskekartan REST…")
    rest_attrs = fetch_fiskekartan_rest()
    (RAW / "fiskekartan_fvof_rest.json").write_text(
        json.dumps(rest_attrs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  {len(rest_attrs)} FVO")

    print("Läser geometrier…")
    gpkg = RAW / "fiskekartan_fvof" / "lstext.fiskekartan_fvof.gpkg"
    geom = gpd.read_file(gpkg)[["ORIGINALID", "FOR_NAMN", "geometry"]].copy()
    geom["ORIGINALID"] = geom["ORIGINALID"].astype("Int64")

    rest_df = pd.DataFrame(rest_attrs)
    rest_df["ORIGINALID"] = rest_df["ORIGINALID"].astype("Int64")

    # Prefer REST attributes; attach geometry by ORIGINALID then name
    merged = rest_df.merge(
        geom.rename(columns={"FOR_NAMN": "FOR_NAMN_GEOM"}),
        on="ORIGINALID",
        how="left",
    )
    missing_geom = merged["geometry"].isna().sum()
    if missing_geom:
        # fallback name match for unmatched
        by_name = geom.drop_duplicates("FOR_NAMN").set_index("FOR_NAMN")["geometry"]
        mask = merged["geometry"].isna()
        merged.loc[mask, "geometry"] = merged.loc[mask, "FOR_NAMN"].map(by_name)
        missing_geom = merged["geometry"].isna().sum()
    print(f"  geometri saknas för {missing_geom} FVO")

    fvo_gdf = gpd.GeoDataFrame(merged, geometry="geometry", crs="EPSG:3006")

    print("Läser NORS/SERS…")
    nors = load_nors_points()
    sers = load_sers_points()
    print(f"  NORS-sjöar med arter: {len(nors)}")
    print(f"  SERS-lokaler med arter: {len(sers)}")

    # Buffra FVO något så kantnära provfiskepunkter inte missas
    BUFFER_M = 500
    fvo_for_join = fvo_gdf[["ORIGINALID", "geometry"]].copy()
    fvo_for_join["geometry"] = fvo_for_join.geometry.buffer(BUFFER_M)

    print(f"Spatial join NORS → FVO (buffer {BUFFER_M} m)…")
    nors_join = gpd.sjoin(nors, fvo_for_join, how="inner", predicate="within")
    print(f"Spatial join SERS → FVO (buffer {BUFFER_M} m)…")
    sers_join = gpd.sjoin(sers, fvo_for_join, how="inner", predicate="within")

    kul_path = RAW / "kul_points_agg.json"
    kul_by_fvo: dict = {}
    if kul_path.exists():
        print("Spatial join KUL → FVO…")
        kul_rows = json.loads(kul_path.read_text(encoding="utf-8"))
        # KUL är WGS84 → SWEREF99TM
        kul_gdf = gpd.GeoDataFrame(
            [
                {
                    "vatten_typ": "kust",
                    "vatten_namn": r.get("omrade") or "KUL-lokal",
                    "smhi_id": None,
                    "eu_cd": None,
                    "lan": None,
                    "haro": None,
                    "arter": r.get("arter") or [],
                    "kalla": "KUL",
                    "senaste_fiskeaar": None,
                    "nors_url": None,
                    "sers_url": None,
                    "geometry": Point(float(r["lon"]), float(r["lat"])),
                }
                for r in kul_rows
                if r.get("arter") and r.get("lat") is not None and r.get("lon") is not None
            ],
            crs="EPSG:4326",
        ).to_crs("EPSG:3006")
        kul_join = gpd.sjoin(kul_gdf, fvo_for_join, how="inner", predicate="within")
        kul_by_fvo = {oid: g for oid, g in kul_join.groupby("ORIGINALID")}
        print(f"  KUL-träffar i FVO: {kul_join['ORIGINALID'].nunique()}")

    nors_by_fvo = {oid: g for oid, g in nors_join.groupby("ORIGINALID")}
    sers_by_fvo = {oid: g for oid, g in sers_join.groupby("ORIGINALID")}

    species_catalog: set[str] = set()
    fvo_list: list[dict] = []
    coverage = defaultdict(int)

    for _, row in fvo_gdf.iterrows():
        oid = int(row["ORIGINALID"]) if pd.notna(row["ORIGINALID"]) else None
        vanliga = split_species_field(row.get("VANL_ART"))
        ovriga = split_species_field(row.get("OVRI_ART"))
        fiskekartan_arter = sorted(set(vanliga) | set(ovriga), key=lambda s: s.casefold())

        vatten: list[dict] = []
        survey_arter: set[str] = set()
        if oid is not None:
            if oid in nors_by_fvo:
                vatten.extend(aggregate_vatten(nors_by_fvo[oid]))
            if oid in sers_by_fvo:
                vatten.extend(aggregate_vatten(sers_by_fvo[oid]))
            if oid in kul_by_fvo:
                vatten.extend(aggregate_vatten(kul_by_fvo[oid]))

        for v in vatten:
            survey_arter.update(v["arter"])

        # FVO-nivå: union av Fiskekartan + survey
        alla = sorted(set(fiskekartan_arter) | survey_arter, key=lambda s: s.casefold())
        species_catalog.update(alla)

        sources = []
        if fiskekartan_arter:
            sources.append("fiskekartan")
            coverage["har_fiskekartan_arter"] += 1
        if survey_arter:
            sources.append("slu_provfiske")
            coverage["har_survey_arter"] += 1
        if alla:
            coverage["har_nagon_art"] += 1
        else:
            coverage["saknar_arter"] += 1
        if vatten:
            coverage["har_vattenposter"] += 1

        url_fkort = clean_str(row.get("URL_FKORT"))
        ifiske_url = None
        if url_fkort and "ifiske" in url_fkort.lower():
            ifiske_url = url_fkort if url_fkort.startswith("http") else "https://" + url_fkort

        fvo_list.append(
            {
                "original_id": oid,
                "object_id": int(row["OBJECTID"]) if pd.notna(row.get("OBJECTID")) else None,
                "namn": clean_str(row.get("FOR_NAMN")) or "",
                "foreningstyp": clean_str(row.get("FORENI_TYP")),
                "ansvarigt_lan": clean_str(row.get("ANSV_LAN")),
                "lan": clean_str(row.get("LAN")),
                "kommuner": clean_str(row.get("KNAMN")),
                "huvudavrinningsomrade": clean_str(row.get("HARO")),
                "url_fiskekartan": clean_str(row.get("URL")),
                "url_fvof": clean_str(row.get("URL_FVOF")),
                "url_fiskekort": url_fkort,
                "url_ifiske_for_externa_lankar": ifiske_url,
                "fiskekartan": {
                    "vanliga_arter": vanliga,
                    "ovriga_arter": ovriga,
                    "arter": fiskekartan_arter,
                },
                "arter": alla,
                "kallor": sources,
                "vatten": vatten,
                "statistik": {
                    "antal_arter": len(alla),
                    "antal_vatten_med_survey": len(vatten),
                    "antal_sjoar_survey": sum(1 for v in vatten if v["typ"] == "sjö"),
                    "antal_vattendrag_survey": sum(
                        1 for v in vatten if v["typ"] == "vattendrag"
                    ),
                },
            }
        )

    fvo_list.sort(key=lambda x: (x["namn"] or "").casefold())

    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "meta": {
            "generated_at": generated_at,
            "ram": "Fiskekartan (LST Fiskevårdsområden FVOF)",
            "antal_fvo": len(fvo_list),
            "kallor": [
                {
                    "id": "fiskekartan",
                    "beskrivning": "Länsstyrelsernas Fiskekartan – FVO-katalog och artfält",
                    "url": "https://fiskekartan.se/",
                },
                {
                    "id": "nors",
                    "beskrivning": "SLU NORS – Nationellt register över sjöprovfisken",
                    "url": "https://dvfisk.slu.se/",
                },
                {
                    "id": "sers",
                    "beskrivning": "SLU SERS – Svenskt elfiskeregister",
                    "url": "https://dvfisk.slu.se/",
                },
                {
                    "id": "kul",
                    "beskrivning": "SLU KUL – kustprovfiske",
                    "url": "https://dvfisk.slu.se/",
                },
            ],
            "policy": {
                "ifiske_artdata": False,
                "ifiske_anvandning": "Endast för att hitta externa FVOF-länkar (se enrich-script)",
            },
            "coverage": dict(coverage),
            "antal_unika_arter": len(species_catalog),
        },
        "arter_katalog": sorted(species_catalog, key=lambda s: s.casefold()),
        "fvo": fvo_list,
    }

    out_path = OUT / "fvo_artlista.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Compact index for app use
    index = [
        {
            "original_id": f["original_id"],
            "namn": f["namn"],
            "ansvarigt_lan": f["ansvarigt_lan"],
            "arter": f["arter"],
            "antal_arter": f["statistik"]["antal_arter"],
            "antal_vatten_med_survey": f["statistik"]["antal_vatten_med_survey"],
            "url_fiskekartan": f["url_fiskekartan"],
            "url_fvof": f["url_fvof"],
        }
        for f in fvo_list
    ]
    (OUT / "fvo_artlista_index.json").write_text(
        json.dumps(
            {"generated_at": generated_at, "antal_fvo": len(index), "fvo": index},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # CSV summary
    rows = []
    for f in fvo_list:
        rows.append(
            {
                "original_id": f["original_id"],
                "namn": f["namn"],
                "ansvarigt_lan": f["ansvarigt_lan"],
                "kommuner": f["kommuner"],
                "antal_arter": f["statistik"]["antal_arter"],
                "arter": "; ".join(f["arter"]),
                "fiskekartan_arter": "; ".join(f["fiskekartan"]["arter"]),
                "antal_sjoar_survey": f["statistik"]["antal_sjoar_survey"],
                "antal_vattendrag_survey": f["statistik"]["antal_vattendrag_survey"],
                "kallor": "; ".join(f["kallor"]),
                "url_fvof": f["url_fvof"] or "",
                "url_fiskekartan": f["url_fiskekartan"] or "",
            }
        )
    pd.DataFrame(rows).to_csv(OUT / "fvo_artlista.csv", index=False)

    print("Klart.")
    print(json.dumps(payload["meta"]["coverage"], ensure_ascii=False, indent=2))
    print(f"Unika arter: {len(species_catalog)}")
    print(f"Skrev {out_path}")


if __name__ == "__main__":
    main()
