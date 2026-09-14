#!/usr/bin/env python3
"""Skapa en komplett indexfil från fvo_artlista.json."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "processed" / "fvo_artlista.json"
OUT = ROOT / "data" / "processed" / "fvo_komplett_index.json"
OUT_COMPACT = ROOT / "data" / "processed" / "fvo_artlista_index.json"


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    generated_at = datetime.now(timezone.utc).isoformat()

    arter_till_fvo: dict[str, list[dict]] = defaultdict(list)
    lan_till_fvo: dict[str, list[int]] = defaultdict(list)
    vatten_index: dict[str, list[dict]] = defaultdict(list)
    namn_till_id: dict[str, list[int]] = defaultdict(list)

    fvo_entries = []
    for f in fvos:
        oid = f["original_id"]
        namn = f.get("namn") or ""
        lan = f.get("ansvarigt_lan") or "Okänt"
        arter = list(f.get("arter") or [])

        vatten = []
        for v in f.get("vatten") or []:
            vatten_entry = {
                "typ": v.get("typ"),
                "namn": v.get("namn"),
                "smhi_id": v.get("smhi_id"),
                "eu_cd": v.get("eu_cd"),
                "lokaler": v.get("lokaler"),
                "arter": list(v.get("arter") or []),
                "kallor": list(v.get("kallor") or []),
                "senaste_fiskeaar": v.get("senaste_fiskeaar"),
            }
            vatten.append(vatten_entry)
            vnamn = (v.get("namn") or "").strip()
            if vnamn:
                vatten_index[vnamn].append(
                    {
                        "fvo_original_id": oid,
                        "fvo_namn": namn,
                        "typ": v.get("typ"),
                        "smhi_id": v.get("smhi_id"),
                        "eu_cd": v.get("eu_cd"),
                        "arter": list(v.get("arter") or []),
                        "kallor": list(v.get("kallor") or []),
                    }
                )

        entry = {
            "original_id": oid,
            "object_id": f.get("object_id"),
            "namn": namn,
            "foreningstyp": f.get("foreningstyp"),
            "ansvarigt_lan": lan,
            "lan": f.get("lan"),
            "kommuner": f.get("kommuner"),
            "huvudavrinningsomrade": f.get("huvudavrinningsomrade"),
            "url_fiskekartan": f.get("url_fiskekartan"),
            "url_fvof": f.get("url_fvof"),
            "url_fiskekort": f.get("url_fiskekort"),
            "kallor": list(f.get("kallor") or []),
            "fiskekartan": f.get("fiskekartan") or {
                "vanliga_arter": [],
                "ovriga_arter": [],
                "arter": [],
            },
            "arter": arter,
            "statistik": f.get("statistik")
            or {
                "antal_arter": len(arter),
                "antal_vatten_med_survey": len(vatten),
                "antal_sjoar_survey": sum(1 for v in vatten if v.get("typ") == "sjö"),
                "antal_vattendrag_survey": sum(
                    1 for v in vatten if v.get("typ") == "vattendrag"
                ),
            },
            "vatten": vatten,
        }
        if f.get("extern_enrichment"):
            entry["extern_enrichment"] = f["extern_enrichment"]

        fvo_entries.append(entry)

        if oid is not None:
            lan_till_fvo[lan].append(oid)
            namn_till_id[namn].append(oid)
            for art in arter:
                arter_till_fvo[art].append(
                    {
                        "original_id": oid,
                        "namn": namn,
                        "ansvarigt_lan": lan,
                    }
                )

    # Sort lookups
    arter_index = {
        art: sorted(refs, key=lambda x: (x["namn"] or "").casefold())
        for art, refs in sorted(arter_till_fvo.items(), key=lambda kv: kv[0].casefold())
    }
    lan_index = {
        lan: sorted(ids)
        for lan, ids in sorted(lan_till_fvo.items(), key=lambda kv: kv[0].casefold())
    }
    vatten_lookup = {
        namn: sorted(
            refs,
            key=lambda x: (
                (x["fvo_namn"] or "").casefold(),
                x.get("typ") or "",
            ),
        )
        for namn, refs in sorted(vatten_index.items(), key=lambda kv: kv[0].casefold())
    }
    namn_index = {
        namn: ids if len(ids) > 1 else ids[0]
        for namn, ids in sorted(namn_till_id.items(), key=lambda kv: kv[0].casefold())
    }

    fvo_entries.sort(key=lambda x: (x["namn"] or "").casefold())
    by_id = {
        e["original_id"]: e
        for e in fvo_entries
        if e.get("original_id") is not None
    }

    komplett = {
        "meta": {
            "generated_at": generated_at,
            "beskrivning": "Komplett index över alla FVO med arter, vatten och uppslagstabeller",
            "kalla": "data/processed/fvo_artlista.json",
            "antal_fvo": len(fvo_entries),
            "antal_unika_arter": len(arter_index),
            "antal_vatten_i_index": len(vatten_lookup),
            "antal_vattenposter": sum(len(e["vatten"]) for e in fvo_entries),
            "coverage": data.get("meta", {}).get("coverage"),
        },
        "arter_katalog": sorted(arter_index.keys(), key=lambda s: s.casefold()),
        "uppslag": {
            "efter_namn": namn_index,
            "efter_lan": lan_index,
            "efter_art": {
                art: [r["original_id"] for r in refs] for art, refs in arter_index.items()
            },
            "efter_vattennamn": {
                namn: [
                    {
                        "fvo_original_id": r["fvo_original_id"],
                        "typ": r["typ"],
                        "smhi_id": r.get("smhi_id"),
                        "eu_cd": r.get("eu_cd"),
                    }
                    for r in refs
                ]
                for namn, refs in vatten_lookup.items()
            },
        },
        "arter_detalj": arter_index,
        "vatten_detalj": vatten_lookup,
        "fvo_efter_id": {str(k): v for k, v in by_id.items()},
        "fvo": fvo_entries,
    }

    OUT.write_text(json.dumps(komplett, ensure_ascii=False, indent=2), encoding="utf-8")

    # Keep compact index in sync too
    compact = {
        "generated_at": generated_at,
        "antal_fvo": len(fvo_entries),
        "fvo": [
            {
                "original_id": e["original_id"],
                "namn": e["namn"],
                "ansvarigt_lan": e["ansvarigt_lan"],
                "kommuner": e.get("kommuner"),
                "arter": e["arter"],
                "antal_arter": e["statistik"]["antal_arter"],
                "antal_vatten_med_survey": e["statistik"]["antal_vatten_med_survey"],
                "antal_sjoar_survey": e["statistik"].get("antal_sjoar_survey"),
                "antal_vattendrag_survey": e["statistik"].get("antal_vattendrag_survey"),
                "kallor": e["kallor"],
                "url_fiskekartan": e["url_fiskekartan"],
                "url_fvof": e["url_fvof"],
            }
            for e in fvo_entries
        ],
    }
    OUT_COMPACT.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "skrev": str(OUT),
                "storlek_mb": round(OUT.stat().st_size / 1e6, 2),
                "antal_fvo": len(fvo_entries),
                "antal_arter": len(arter_index),
                "antal_vattennamn": len(vatten_lookup),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
