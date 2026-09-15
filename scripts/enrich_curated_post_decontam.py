#!/usr/bin/env python3
"""
Kuraterad berikning efter dekontaminering.

Endast 1:1-mappningar (exakt FVO-namn + län). Ingen fuzzy cross-match.
Tillåtna källor: Drömfiske, Vindelälven/Laxportalen, lokala turismfoldrar,
Gällivare kommun, m.fl. — aldrig iFiske/NatureIT/Artportalen som artdata.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"

# Exakt namn + län → arter + url + source
CURATED = [
    {
        "namn": "Rödåbygdens fvof",
        "lan": "Västerbotten",
        "arter": ["Abborre", "Gädda", "Öring", "Harr", "Lax"],
        "url": "https://vindelalven.laxportalen.se/rodabygdens-fvo/",
        "source": "vindelalven_laxportalen",
        "note": "Explicit artlista på sidan",
    },
    {
        "namn": "Haverö fvof",
        "lan": "Västernorrland",
        "arter": ["Abborre", "Gädda", "Öring", "Röding", "Harr"],
        "url": "https://fliphtml5.com/cvwc/xgsy/Fiske_i_Ljungan_-_Haver%C3%B6/",
        "source": "haverö_ljungan_fiskeguide",
        "note": "Lokal fiskeguide 'Fiske i Ljungan – Haverö' (folder)",
    },
    {
        "namn": "Dammåns fvof",
        "lan": "Jämtland",
        "arter": ["Öring", "Röding", "Harr"],
        "url": "https://dromfiske.com/dammans-fvo/",
        "source": "dromfiske",
        "note": "Drömfiske: öring, röding, harr (återställer Röding efter Bollnäs-rättning)",
    },
    {
        "namn": "Åkersjöns fvof",
        "lan": "Jämtland",
        "arter": ["Röding", "Öring", "Harr"],
        "url": "https://dromfiske.com/akersjons-fvo/",
        "source": "dromfiske",
        "note": "Drömfiske: röding/öring i sjön; harr i Åkerån",
    },
    {
        "namn": "Älmeshultasjöns fvof",
        "lan": "Jönköping",
        "arter": ["Abborre", "Braxen", "Gädda", "Lake", "Mört", "Sarv", "Siklöja", "Sutare"],
        "url": "https://www.naturkartan.se/sv/jonkopings-lan/almeshultasjons-fvof",
        "source": "naturkartan",
        "note": "Naturkartan: explicit artlista för Älmeshultasjön",
    },
]


def apply_add(fvo: dict, spp: list[str], source: str, url: str | None) -> dict | None:
    before = set(fvo.get("arter") or [])
    extra = set(spp) - before
    if url:
        tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [url]))
        fvo["lokala_tipslankar"] = tips
    if not extra:
        return None
    after = sorted(before | set(spp), key=lambda s: s.casefold())
    fvo["arter"] = after
    fvo.setdefault("kallor", [])
    if source not in fvo["kallor"]:
        fvo["kallor"].append(source)
    prev = fvo.get("extern_enrichment") or {}
    # preserve rattning if present
    new_ext = {
        "tillagda_arter": sorted(
            set(prev.get("tillagda_arter") or []) | extra, key=lambda s: s.casefold()
        ),
        "kallor_url:er": list(
            dict.fromkeys((prev.get("kallor_url:er") or []) + ([url] if url else []))
        ),
    }
    if prev.get("rattning"):
        new_ext["rattning"] = prev["rattning"]
    fvo["extern_enrichment"] = new_ext
    if isinstance(fvo.get("statistik"), dict):
        fvo["statistik"]["antal_arter"] = len(after)
    closed = 0
    ctrl = fvo.get("ifiske_spegel_kontroll")
    if ctrl and ctrl.get("spegel_arter"):
        prev_gaps = set(ctrl.get("luckor_ej_funna_hos_fvof") or [])
        still = sorted(set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold())
        closed = len(prev_gaps - set(still))
        ctrl["luckor_ej_funna_hos_fvof"] = still
    return {
        "fvo": fvo["namn"],
        "lan": fvo.get("ansvarigt_lan"),
        "tillagda": sorted(extra, key=lambda s: s.casefold()),
        "source": source,
        "url": url,
        "gaps_closed": closed,
    }


def lan_stats(fvos, lan):
    rows = [f for f in fvos if f.get("ansvarigt_lan") == lan]
    return {
        "antal": len(rows),
        "tomma": sum(1 for f in rows if not f.get("arter")),
        "tunna_le3": sum(1 for f in rows if 0 < len(f.get("arter") or []) <= 3),
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    by = {(f["namn"], f.get("ansvarigt_lan")): f for f in fvos}

    before_empty = sum(1 for f in fvos if not f.get("arter"))
    before_jh = lan_stats(fvos, "Jämtland")
    before_nb = lan_stats(fvos, "Norrbotten")
    applied = []
    missed = []

    for rec in CURATED:
        key = (rec["namn"], rec["lan"])
        fvo = by.get(key)
        if not fvo:
            missed.append(rec["namn"])
            continue
        res = apply_add(fvo, rec["arter"], rec["source"], rec["url"])
        if res:
            res["note"] = rec.get("note")
            applied.append(res)

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
    for f in fvos:
        catalog.update(f.get("arter") or [])
        fk = f.get("fiskekartan") or {}
        if fk.get("arter") or fk.get("vanliga_arter") or fk.get("ovriga_arter"):
            coverage["har_fiskekartan_arter"] += 1
        if any(k in (f.get("kallor") or []) for k in ("slu_provfiske", "nors", "sers", "kul")):
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

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    data["meta"]["curated_post_decontam_pass"] = {
        "applied": len(applied),
        "added_species_entries": sum(len(a["tillagda"]) for a in applied),
        "mirror_gaps_closed": sum(a["gaps_closed"] for a in applied),
        "tomma_fore": before_empty,
        "tomma_efter": coverage["saknar_arter"],
        "jamtland_before": before_jh,
        "jamtland_after": lan_stats(fvos, "Jämtland"),
        "norrbotten_before": before_nb,
        "norrbotten_after": lan_stats(fvos, "Norrbotten"),
        "missed": missed,
        "blocked_sources": ["natureit", "artportalen", "ifiske_artdata"],
    }
    pol = data["meta"].setdefault("policy", {})
    pol["beskrivning"] = (
        "iFiske endast spegel/länktips. NatureIT och Artportalen används inte. "
        "Artdata från Fiskekartan, SLU, GBIF, FVOF, FiskaiBerg, Drömfiske, "
        "Strömsunds fiskebroschyr, Gällivare kommun, Vindelälven/Laxportalen, "
        "lokala fiskeguider. Cross-match-kontaminering (t.ex. bollnasfvf.se) rättad."
    )

    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "curated_post_decontam_log.json").write_text(
        json.dumps(
            {"applied": applied, "stats": data["meta"]["curated_post_decontam_pass"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

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

    empty = [
        {"lan": f.get("ansvarigt_lan"), "namn": f["namn"]}
        for f in fvos
        if not f.get("arter")
    ]
    print(
        json.dumps(
            {
                "stats": data["meta"]["curated_post_decontam_pass"],
                "applied": applied,
                "empty_remaining": empty,
                "empty_by_lan": {
                    lan: sum(1 for e in empty if e["lan"] == lan)
                    for lan in sorted({e["lan"] for e in empty})
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
