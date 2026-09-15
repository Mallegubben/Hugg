#!/usr/bin/env python3
"""
Vattennivå-ifyllnad: FVO med arter men 0 vattenposter.

Läser kuraterade 1:1-listor (verifierade källor) och skapar vattenposter.
Policy: aldrig iFiske-ikoner, NatureIT eller Artportalen som artdata.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
RAW = ROOT / "data" / "raw"


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(
        r"\b(fvof|fvo|kfo|kfof|fiskevardsomrade|forening|sameby|samfallighet)\b",
        "",
        s,
        flags=re.I,
    )
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def infer_typ(namn: str) -> str:
    n = (namn or "").casefold()
    if any(x in n for x in ("älven", "ån", "forsen", "strömmen", "bäck", "fjorden")):
        return "vattendrag"
    return "sjö"


def normalize_source(raw: str) -> str:
    s = (raw or "curated_vatten").strip().lower()
    mapping = [
        ("messlingen", "messlingenfiske"),
        ("dromfiske", "dromfiske"),
        ("vattenagarna", "vattenagarna"),
        ("naturkartan", "naturkartan"),
        ("hokensas", "hokensas"),
        ("vastsverige", "vastsverige"),
        ("ljungandalen", "ljungandalen_haverö_folder"),
        ("gallivare", "gallivare_kommun_fiskeguide"),
        ("svenljunga", "svenljunga_tranemo_fiskebroschyr_2021"),
        ("yxern", "yxern.se"),
        ("uppvidinge", "uppvidinge_kommun"),
    ]
    for needle, canon in mapping:
        if needle in s:
            return canon
    first = re.split(r"\s*/\s*", s)[0]
    return re.sub(r"[^a-z0-9_.-]+", "_", first).strip("_") or "curated_vatten"


def find_fvo_for_block(fvos: list[dict], block: dict) -> dict | None:
    namn = block["namn"]
    lan = block.get("lan")
    hits = [f for f in fvos if f.get("namn") == namn]
    if lan:
        hits = [f for f in hits if f.get("ansvarigt_lan") == lan]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # Prefer empty (0-vatten) targets for gap-fill when unique.
    zero = [f for f in hits if not f.get("vatten")]
    if len(zero) == 1:
        return zero[0]
    water_names = {
        fold(w.get("vatten_namn") or w.get("namn") or "")
        for w in (block.get("vatten") or [])
    }
    for f in hits:
        existing = {fold(v.get("namn") or "") for v in (f.get("vatten") or [])}
        if existing & water_names:
            return f
    if len(zero) > 1:
        return None
    return None


def upsert_vatten(
    fvo: dict,
    vatten_namn: str,
    arter: list[str],
    source: str,
    url: str | None,
) -> dict:
    arter = sorted({a for a in arter if a}, key=lambda s: s.casefold())
    if not arter or not vatten_namn:
        return {"action": "skip"}

    vatten = list(fvo.get("vatten") or [])
    target = fold(vatten_namn)
    match = next((v for v in vatten if fold(v.get("namn") or "") == target), None)

    if match:
        before = set(match.get("arter") or [])
        after = sorted(before | set(arter), key=lambda s: s.casefold())
        match["arter"] = after
        kallor = list(match.get("kallor") or [])
        if source not in kallor:
            kallor.append(source)
            match["kallor"] = kallor
        if url:
            refs = list(match.get("referenser") or [])
            if url not in refs:
                refs.append(url)
                match["referenser"] = refs
        fvo["vatten"] = vatten
        action = (
            "merge" if after != sorted(before, key=lambda s: s.casefold()) else "touch"
        )
        added = sorted(set(after) - before, key=lambda s: s.casefold())
    else:
        entry = {
            "typ": infer_typ(vatten_namn),
            "namn": vatten_namn,
            "smhi_id": None,
            "eu_cd": None,
            "lokaler": None,
            "arter": arter,
            "kallor": [source],
            "senaste_fiskeaar": None,
            "referenser": [url] if url else [],
        }
        vatten.append(entry)
        fvo["vatten"] = vatten
        action = "create"
        added = arter

    if url:
        tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [url]))
        fvo["lokala_tipslankar"] = tips
    fvo.setdefault("kallor", [])
    if source not in fvo["kallor"]:
        fvo["kallor"].append(source)

    before_fvo = set(fvo.get("arter") or [])
    extra_fvo = set(arter) - before_fvo
    if extra_fvo:
        after_fvo = sorted(before_fvo | set(arter), key=lambda s: s.casefold())
        fvo["arter"] = after_fvo
        prev = fvo.get("extern_enrichment") or {}
        fvo["extern_enrichment"] = {
            "tillagda_arter": sorted(
                set(prev.get("tillagda_arter") or []) | extra_fvo,
                key=lambda s: s.casefold(),
            ),
            "kallor_url:er": list(
                dict.fromkeys((prev.get("kallor_url:er") or []) + ([url] if url else []))
            ),
        }
        if isinstance(fvo.get("statistik"), dict):
            fvo["statistik"]["antal_arter"] = len(after_fvo)

    return {
        "action": action,
        "fvo": fvo["namn"],
        "lan": fvo.get("ansvarigt_lan"),
        "vatten": vatten_namn,
        "source": source,
        "tillagda_vattenarter": added,
        "tillagda_fvoarter": sorted(extra_fvo, key=lambda s: s.casefold()),
    }


def persist_raw_copies() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name in (
        "vatten_fill_round.json",
        "vatten_fill_round2.json",
        "vatten_fill_round3.json",
        "vatten_fill_round4.json",
        "vatten_fill_round5.json",
        "vatten_fill_round5_vg.json",
    ):
        src = Path("/opt/cursor/artifacts") / name
        dst = RAW / name
        if src.exists() and not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def load_enrichments() -> list[dict]:
    candidates = [
        RAW / "vatten_fill_round.json",
        RAW / "vatten_fill_round2.json",
        RAW / "vatten_fill_round3.json",
        RAW / "vatten_fill_round3_partial.json",
        RAW / "vatten_fill_round4.json",
        RAW / "vatten_fill_round5.json",
        # Skip unsanitized round5_vg.json (wrong multi-lake mappings); use sanitized.
        RAW / "vatten_fill_round5_abborresjon.json",
        RAW / "vatten_fill_round5_vg_sanitized.json",
        RAW / "vatten_fill_round6.json",
        RAW / "vatten_fill_round6b.json",
        RAW / "vatten_fill_round6c.json",
        Path("/opt/cursor/artifacts/vatten_fill_round.json"),
        Path("/opt/cursor/artifacts/vatten_fill_round2.json"),
        Path("/opt/cursor/artifacts/vatten_fill_round3.json"),
        Path("/opt/cursor/artifacts/vatten_fill_round4_merged.json"),
        Path("/opt/cursor/artifacts/vatten_fill_round5.json"),
        Path("/opt/cursor/artifacts/vatten_fill_round6.json"),
    ]
    blocks: list[dict] = []
    loaded: set[str] = set()
    for path in candidates:
        if not path.exists() or path.name in loaded:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "enrichments" not in data:
            continue
        loaded.add(path.name)
        for block in data["enrichments"]:
            if block.get("vatten"):
                blocks.append(block)
    return blocks


def coverage(fvos: list[dict]) -> dict:
    c = {
        "antal_fvo": len(fvos),
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_vattenposter": 0,
        "med_arter_men_0_vatten": 0,
        "total_vatten": 0,
    }
    for f in fvos:
        if f.get("arter"):
            c["har_nagon_art"] += 1
            if not f.get("vatten"):
                c["med_arter_men_0_vatten"] += 1
        else:
            c["saknar_arter"] += 1
        if f.get("vatten"):
            c["har_vattenposter"] += 1
        c["total_vatten"] += len(f.get("vatten") or [])
    return c


def main() -> None:
    persist_raw_copies()
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    before = coverage(fvos)

    applied: list[dict] = []
    unmatched: list[dict] = []
    for block in load_enrichments():
        fvo = find_fvo_for_block(fvos, block)
        if not fvo:
            unmatched.append({"namn": block["namn"], "lan": block.get("lan")})
            continue
        for w in block.get("vatten") or []:
            vnamn = w.get("vatten_namn") or w.get("namn")
            src = normalize_source(w.get("kalla") or "curated_vatten")
            url = w.get("url") or ((w.get("urls") or [None])[0])
            row = upsert_vatten(fvo, vnamn, list(w.get("arter") or []), src, url)
            if row.get("action") != "skip":
                applied.append(row)

    after = coverage(fvos)
    created = sum(1 for a in applied if a.get("action") == "create")
    merged = sum(1 for a in applied if a.get("action") == "merge")
    touched = sum(1 for a in applied if a.get("action") == "touch")

    catalog: set[str] = set()
    for f in fvos:
        catalog.update(f.get("arter") or [])

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    prev_cov = data["meta"].get("coverage") or {}
    data["meta"]["coverage"] = {
        **{
            k: prev_cov.get(k)
            for k in (
                "har_fiskekartan_arter",
                "har_survey_arter",
                "har_extern_enrichment",
                "har_gbif_enrichment",
                "har_ifiske_spegel_kontroll",
            )
        },
        "har_nagon_art": after["har_nagon_art"],
        "saknar_arter": after["saknar_arter"],
        "har_vattenposter": after["har_vattenposter"],
    }
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["vatten_fill_gap_pass"] = {
        "created_vatten": created,
        "merged_vatten": merged,
        "touched_vatten": touched,
        "applied_rows": len(applied),
        "unmatched": unmatched,
        "before": before,
        "after": after,
        "blocked_sources": ["natureit", "artportalen", "ifiske_artdata"],
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log = {
        "applied": applied,
        "unmatched": unmatched,
        "stats": data["meta"]["vatten_fill_gap_pass"],
    }
    (OUT / "vatten_fill_gap_pass_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(data["meta"]["vatten_fill_gap_pass"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
