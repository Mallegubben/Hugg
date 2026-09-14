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


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    fvos_in = data["fvo"]
    generated_at = datetime.now(timezone.utc).isoformat()

    art_to_ids: dict[str, list[int]] = defaultdict(list)
    lan_to_ids: dict[str, list[int]] = defaultdict(list)
    namn_to_id: dict[str, int | list[int]] = {}
    namn_acc: dict[str, list[int]] = defaultdict(list)
    vatten_to_refs: dict[str, list[dict]] = defaultdict(list)

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
            "arter_utokad": list(f.get("arter_utokad") or arter),
            "fiskekartan_arter": list((f.get("fiskekartan") or {}).get("arter") or []),
            "ifiske_speglad": bool(f.get("ifiske_spegling")),
            "vatten": vatten,
        }
        fvo_out.append(item)

        if oid is None:
            continue
        lan_to_ids[lan].append(oid)
        namn_acc[namn].append(oid)
        for art in arter:
            art_to_ids[art].append(oid)

    fvo_out.sort(key=lambda x: (x["namn"] or "").casefold())

    for namn, ids in namn_acc.items():
        namn_to_id[namn] = ids[0] if len(ids) == 1 else ids

    index = {
        "generated_at": generated_at,
        "antal_fvo": len(fvo_out),
        "antal_arter": len(art_to_ids),
        "antal_vatten": len(vatten_to_refs),
        "arter": sorted(art_to_ids.keys(), key=lambda s: s.casefold()),
        "uppslag": {
            "namn": dict(sorted(namn_to_id.items(), key=lambda kv: kv[0].casefold())),
            "lan": {
                lan: sorted(ids)
                for lan, ids in sorted(lan_to_ids.items(), key=lambda kv: kv[0].casefold())
            },
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
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
