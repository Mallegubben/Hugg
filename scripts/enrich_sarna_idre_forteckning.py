#!/usr/bin/env python3
"""Berika Särna-Idre fvof från FVOF:s fiskevattenförteckning (FVO-nivå)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
RAW_PDF = ROOT / "data" / "raw" / "sarna_idre_fiskevattenforteckning.pdf"

CODE_MAP = {
    "A": "Abborre", "G": "Gädda", "Ö": "Öring", "H": "Harr", "R": "Röding",
    "S": "Sik", "L": "Lake", "M": "Mört", "E": "Elritsa", "I": "Id",
    "BR": "Bäckröding", "B.R": "Bäckröding", "B.R.": "Bäckröding",
    "RB": "Regnbåge", "SL": "Siklöja",
}


def extract_codes(blob: str) -> set[str]:
    found: set[str] = set()
    cleaned = re.sub(r"\b[A-I]\s*\d+[a-z]?\b", " ", blob or "")
    for code in ("B.R.", "B.R", "BR", "RB", "SL"):
        if re.search(rf"(?<![A-Za-zÅÄÖåäö.]){re.escape(code)}(?![A-Za-zÅÄÖåäö.])", cleaned):
            found.add(CODE_MAP[code])
            cleaned = re.sub(rf"(?<![A-Za-zÅÄÖåäö.]){re.escape(code)}(?![A-Za-zÅÄÖåäö.])", " ", cleaned)
    for code, species in CODE_MAP.items():
        if len(code) == 1 and re.search(
            rf"(?<![A-Za-zÅÄÖåäö.]){re.escape(code)}(?![A-Za-zÅÄÖåäö.0-9])", cleaned
        ):
            found.add(species)
    return found


def main() -> None:
    if not RAW_PDF.exists():
        raise SystemExit(f"Saknar {RAW_PDF}")
    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(RAW_PDF)).pages)
    all_spp = extract_codes(text)
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvo = next(f for f in data["fvo"] if f["namn"] == "Särna-Idre fvof")
    before = set(fvo.get("arter") or [])
    after = sorted(before | all_spp, key=lambda s: s.casefold())
    added = sorted(set(after) - before, key=lambda s: s.casefold())
    fvo["arter"] = after
    fvo.setdefault("kallor", [])
    if "sarna_idre_fiskevattenforteckning" not in fvo["kallor"]:
        fvo["kallor"].append("sarna_idre_fiskevattenforteckning")
    fvo["url_fvof"] = fvo.get("url_fvof") or "https://sarnaidrefvo.se/"
    fvo["lokala_tipslankar"] = list(
        dict.fromkeys(
            (fvo.get("lokala_tipslankar") or [])
            + [
                "https://sarnaidrefvo.se/",
                "https://www.ifiske.se/pdf/3378/sarnaidre_fiskevattenforteckning.pdf",
            ]
        )
    )
    prev = fvo.get("extern_enrichment") or {}
    fvo["extern_enrichment"] = {
        "tillagda_arter": sorted(set(prev.get("tillagda_arter") or []) | set(added), key=lambda s: s.casefold()),
        "kallor_url:er": list(
            dict.fromkeys(
                (prev.get("kallor_url:er") or [])
                + [
                    "https://sarnaidrefvo.se/",
                    "https://www.ifiske.se/pdf/3378/sarnaidre_fiskevattenforteckning.pdf",
                ]
            )
        ),
        "sarna_idre_pass": {
            "fvo_tillagda": added,
            "arter_i_forteckning": sorted(all_spp, key=lambda s: s.casefold()),
            "note": "FVO-nivå. Vattennivå kräver manuell zonparsning (blandade rader).",
        },
    }
    if isinstance(fvo.get("statistik"), dict):
        fvo["statistik"]["antal_arter"] = len(after)
    catalog: set[str] = set()
    for f in data["fvo"]:
        catalog.update(f.get("arter") or [])
    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["sarna_idre_pass"] = {
        "fvo_tillagda": added,
        "arter_i_forteckning": sorted(all_spp, key=lambda s: s.casefold()),
        "kalla": "Särna-Idre FVOF fiskevattenförteckning (ej iFiske-ikoner; ej NatureIT/Artportalen)",
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    data["meta"].setdefault("policy", {})
    data["meta"]["policy"].update({"ifiske_artdata": False, "natureit": False, "artportalen": False})
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "sarna_idre_pass_log.json").write_text(
        json.dumps(
            {"arter_forteckning": sorted(all_spp, key=lambda s: s.casefold()), "fvo_tillagda": added},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"added": added, "forteckning": sorted(all_spp, key=lambda s: s.casefold())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
