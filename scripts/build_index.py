#!/usr/bin/env python3
"""Skapa den enda indexfilen: data/index.json."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "processed" / "fvo_artlista.json"
OUT = ROOT / "data" / "index.json"

# Officiella 21 län (korta namn som i Fiskekartan ANSV_LAN)
OFFICIELLA_LAN = [
    "Blekinge",
    "Dalarna",
    "Gotland",
    "Gävleborg",
    "Halland",
    "Jämtland",
    "Jönköping",
    "Kalmar",
    "Kronoberg",
    "Norrbotten",
    "Skåne",
    "Stockholm",
    "Södermanland",
    "Uppsala",
    "Värmland",
    "Västerbotten",
    "Västernorrland",
    "Västmanland",
    "Västra Götaland",
    "Örebro",
    "Östergötland",
]


def tackning_per_lan(fvos_in: list[dict], lan_to_ids: dict[str, list[int]]) -> list[dict]:
    by_lan: dict[str, list[dict]] = defaultdict(list)
    for f in fvos_in:
        by_lan[f.get("ansvarigt_lan") or "Okänt"].append(f)

    rows: list[dict] = []
    for lan in OFFICIELLA_LAN:
        fs = by_lan.get(lan, [])
        med = sum(1 for f in fs if f.get("arter"))
        utan = len(fs) - med
        med_vatten = sum(1 for f in fs if f.get("vatten"))
        vatten_n = sum(len(f.get("vatten") or []) for f in fs)
        pct = round(100.0 * med / len(fs), 1) if fs else 0.0
        rows.append(
            {
                "lan": lan,
                "antal_fvo": len(fs),
                "med_artlista": med,
                "utan_artlista": utan,
                "tackning_pct": pct,
                "fvo_med_vattenposter": med_vatten,
                "antal_vattenposter": vatten_n,
                "fvo_ids": sorted(lan_to_ids.get(lan, [])),
                "kalla": "saknas_i_fiskekartan" if lan == "Gotland" and not fs else "fiskekartan",
            }
        )
    return rows


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    fvos_in = data["fvo"]
    generated_at = datetime.now(timezone.utc).isoformat()

    art_to_ids: dict[str, list[int]] = defaultdict(list)
    lan_to_ids: dict[str, list[int]] = defaultdict(list)
    namn_to_id: dict[str, int | list[int]] = {}
    namn_acc: dict[str, list[int]] = defaultdict(list)
    vatten_to_refs: dict[str, list[dict]] = defaultdict(list)
    utan_artlista: list[dict] = []

    fvo_out = []
    for f in fvos_in:
        oid = f["original_id"]
        namn = f.get("namn") or ""
        lan = f.get("ansvarigt_lan") or "Okänt"
        arter = list(f.get("arter") or [])

        vatten = []
        for v in f.get("vatten") or []:
            entry = {
                "typ": v.get("typ"),
                "namn": v.get("namn"),
                "smhi_id": v.get("smhi_id"),
                "eu_cd": v.get("eu_cd"),
                "arter": list(v.get("arter") or []),
                "kallor": list(v.get("kallor") or []),
                "senaste_fiskeaar": v.get("senaste_fiskeaar"),
            }
            vatten.append(entry)
            vnamn = (v.get("namn") or "").strip()
            if vnamn and oid is not None:
                vatten_to_refs[vnamn].append(
                    {
                        "fvo_id": oid,
                        "typ": v.get("typ"),
                        "smhi_id": v.get("smhi_id"),
                        "eu_cd": v.get("eu_cd"),
                        "arter": list(v.get("arter") or []),
                    }
                )

        item = {
            "id": oid,
            "namn": namn,
            "lan": lan,
            "kommuner": f.get("kommuner"),
            "huvudavrinningsomrade": f.get("huvudavrinningsomrade"),
            "url_fiskekartan": f.get("url_fiskekartan"),
            "url_fvof": f.get("url_fvof"),
            "kallor": list(f.get("kallor") or []),
            "arter": arter,
            "fiskekartan_arter": list((f.get("fiskekartan") or {}).get("arter") or []),
            "vatten": vatten,
        }
        fvo_out.append(item)

        if not arter:
            utan_artlista.append(
                {
                    "id": oid,
                    "namn": namn,
                    "lan": lan,
                    "kommuner": f.get("kommuner"),
                }
            )

        if oid is None:
            continue
        lan_to_ids[lan].append(oid)
        namn_acc[namn].append(oid)
        for art in arter:
            art_to_ids[art].append(oid)

    fvo_out.sort(key=lambda x: (x["namn"] or "").casefold())
    utan_artlista.sort(key=lambda x: ((x.get("lan") or ""), (x.get("namn") or "").casefold()))

    for namn, ids in namn_acc.items():
        namn_to_id[namn] = ids[0] if len(ids) == 1 else ids

    # Inkludera Gotland (tom lista) så alla 21 län syns i uppslag
    lan_uppslag = {
        lan: sorted(ids)
        for lan, ids in sorted(lan_to_ids.items(), key=lambda kv: kv[0].casefold())
    }
    for lan in OFFICIELLA_LAN:
        lan_uppslag.setdefault(lan, [])

    tackning = tackning_per_lan(fvos_in, lan_to_ids)

    index = {
        "generated_at": generated_at,
        "antal_fvo": len(fvo_out),
        "antal_arter": len(art_to_ids),
        "antal_vatten": len(vatten_to_refs),
        "antal_utan_artlista": len(utan_artlista),
        "arter": sorted(art_to_ids.keys(), key=lambda s: s.casefold()),
        "tackning_per_lan": tackning,
        "utan_artlista": utan_artlista,
        "filer": {
            "index": "data/index.json",
            "artlista": "data/processed/fvo_artlista.json",
            "tackning_csv": "data/processed/fvo_tackning_per_lan.csv",
            "tackning_tsv": "data/processed/fvo_tackning_per_lan.tsv",
            "tackning_md": "data/processed/fvo_tackning_per_lan.md",
            "utan_artlista_tsv": "data/processed/fvo_utan_artlista.tsv",
        },
        "uppslag": {
            "namn": dict(sorted(namn_to_id.items(), key=lambda kv: kv[0].casefold())),
            "lan": dict(sorted(lan_uppslag.items(), key=lambda kv: kv[0].casefold())),
            "art": {
                art: sorted(set(ids))
                for art, ids in sorted(art_to_ids.items(), key=lambda kv: kv[0].casefold())
            },
            "vatten": {
                namn: sorted(refs, key=lambda r: (r["fvo_id"], r.get("typ") or ""))
                for namn, refs in sorted(
                    vatten_to_refs.items(), key=lambda kv: kv[0].casefold()
                )
            },
        },
        "fvo": fvo_out,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "fil": str(OUT.relative_to(ROOT)),
                "mb": round(OUT.stat().st_size / 1e6, 2),
                "antal_fvo": index["antal_fvo"],
                "antal_arter": index["antal_arter"],
                "antal_vatten": index["antal_vatten"],
                "antal_utan_artlista": index["antal_utan_artlista"],
                "antal_lan_i_uppslag": len(index["uppslag"]["lan"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
