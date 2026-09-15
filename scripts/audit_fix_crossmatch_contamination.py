#!/usr/bin/env python3
"""
Rätta felmatchad extern berikning (cross-match-kontaminering).

Problem: samma FVOF-sida (t.ex. bollnasfvf.se, malungs-fiske.se) har matchats
mot många orelaterade FVO och lagt in arter.

Policy:
- Behåll arter från Fiskekartan, SLU-vatten, GBIF (baseline).
- Ta bort arter som endast kom via den felaktiga extern-URL:en.
- Rensa felaktiga URL:er ur extern_enrichment / tips.
- Bollnäs-sajten får bara gälla Bollnäs*-FVO i Gävleborg.
- Malung-sajten får bara gälla Malungs* i Dalarna.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"

# host -> allowed FVO name prefixes (fold-insensitive via startswith after fold)
HOST_ALLOWLIST = {
    "bollnasfvf.se": {
        "lan": "Gävleborg",
        "name_prefixes": ["bollnas"],
    },
    "malungs-fiske.se": {
        "lan": "Dalarna",
        "name_prefixes": ["malung"],
    },
}


def fold(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(
        r"\b(fvof|fvo|kfo|fiskevardsomrade|forening|sameby)\b",
        "",
        s,
        flags=re.I,
    )
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        h = urlparse(url if "://" in url else "http://" + url).netloc.lower()
    except Exception:
        return None
    return h.removeprefix("www.") or None


def baseline_keep(fvo: dict) -> set[str]:
    keep: set[str] = set()
    fk = fvo.get("fiskekartan") or {}
    keep.update(fk.get("arter") or [])
    keep.update(fk.get("vanliga_arter") or [])
    keep.update(fk.get("ovriga_arter") or [])
    for v in fvo.get("vatten") or []:
        keep.update(v.get("arter") or [])
    ge = fvo.get("gbif_enrichment") or {}
    keep.update(ge.get("arter_i_omslutning") or [])
    # species present before extern tillagda
    extern = set((fvo.get("extern_enrichment") or {}).get("tillagda_arter") or [])
    keep |= set(fvo.get("arter") or []) - extern
    # also keep from known-good källor markers in vatten already covered
    return keep


def is_allowed_for_host(fvo: dict, host: str) -> bool:
    rule = HOST_ALLOWLIST.get(host)
    if not rule:
        return True
    if fvo.get("ansvarigt_lan") != rule["lan"]:
        return False
    fn = fold(fvo.get("namn") or "")
    return any(fn.startswith(p) for p in rule["name_prefixes"])


def clean_fvo(fvo: dict) -> dict | None:
    ext = fvo.get("extern_enrichment") or {}
    urls = list(ext.get("kallor_url:er") or [])
    bad_urls = []
    for u in urls:
        h = host_of(u)
        if h in HOST_ALLOWLIST and not is_allowed_for_host(fvo, h):
            bad_urls.append(u)
    if not bad_urls:
        return None

    keep = baseline_keep(fvo)
    before = list(fvo.get("arter") or [])
    # Species that were added via extern and are NOT in baseline → remove
    tillagda = set(ext.get("tillagda_arter") or [])
    # If ALL tillagda came while contaminated URLs were present, remove those
    # not in baseline. Conservative: remove tillagda - keep.
    removed = sorted((tillagda - keep) & set(before), key=lambda s: s.casefold())
    # Also: if arter consists only of tillagda from contamination and nothing
    # in keep, we may empty the FVO — that's correct.
    after = sorted([a for a in before if a in keep or a not in tillagda], key=lambda s: s.casefold())
    # Ensure we drop removed
    after = sorted(set(after) - set(removed), key=lambda s: s.casefold())
    # Keep baseline always
    after = sorted(set(after) | keep, key=lambda s: s.casefold())

    fvo["arter"] = after
    if isinstance(fvo.get("statistik"), dict):
        fvo["statistik"]["antal_arter"] = len(after)

    new_urls = [u for u in urls if u not in bad_urls]
    new_tillagda = sorted((tillagda & set(after)) - keep, key=lambda s: s.casefold())
    # Actually tillagda should be species still in arter that came from extern and not baseline
    new_tillagda = sorted(set(after) - keep, key=lambda s: s.casefold())

    rattning = {
        "borttagna": removed,
        "borttagna_url:er": bad_urls,
        "orsak": "crossmatch_kontaminering_fel_fvof_sida",
    }
    if new_urls or new_tillagda:
        fvo["extern_enrichment"] = {
            "tillagda_arter": new_tillagda,
            "kallor_url:er": new_urls,
            "rattning": rattning,
        }
    else:
        fvo["extern_enrichment"] = {"rattning": rattning}

    # clean tips
    tips = fvo.get("lokala_tipslankar") or []
    fvo["lokala_tipslankar"] = [t for t in tips if t not in bad_urls]
    if not fvo["lokala_tipslankar"]:
        fvo.pop("lokala_tipslankar", None)

    # clean kallor tag if no remaining legitimate extern
    kallor = list(fvo.get("kallor") or [])
    if "extern_fvof" in kallor and not new_urls and not new_tillagda:
        # only remove extern_fvof if no other extern evidence
        if not any(
            k.startswith(("stromsund", "gallivare", "glommers", "sammakko", "fiskaiberg", "dromfiske"))
            for k in kallor
        ):
            pass  # keep history; mark via rattning

    ctrl = fvo.get("ifiske_spegel_kontroll")
    if ctrl and ctrl.get("spegel_arter"):
        ctrl["luckor_ej_funna_hos_fvof"] = sorted(
            set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold()
        )

    return {
        "fvo": fvo["namn"],
        "lan": fvo.get("ansvarigt_lan"),
        "borttagna": removed,
        "bad_urls": bad_urls,
        "arter_efter": after,
        "blev_tom": not after,
    }


def lan_stats(fvos: list[dict], lan: str) -> dict:
    rows = [f for f in fvos if f.get("ansvarigt_lan") == lan]
    return {
        "antal": len(rows),
        "tomma": sum(1 for f in rows if not f.get("arter")),
        "tunna_le3": sum(1 for f in rows if 0 < len(f.get("arter") or []) <= 3),
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    before_empty = sum(1 for f in fvos if not f.get("arter"))
    before_jh = lan_stats(fvos, "Jämtland")
    before_nb = lan_stats(fvos, "Norrbotten")

    applied = []
    for f in fvos:
        res = clean_fvo(f)
        if res:
            applied.append(res)

    # refresh coverage
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

    after_empty = coverage["saknar_arter"]
    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    data["meta"]["crossmatch_decontam_pass"] = {
        "fvo_rattade": len(applied),
        "artposter_borttagna": sum(len(a["borttagna"]) for a in applied),
        "blev_tomma": sum(1 for a in applied if a["blev_tom"]),
        "tomma_fore": before_empty,
        "tomma_efter": after_empty,
        "jamtland_before": before_jh,
        "jamtland_after": lan_stats(fvos, "Jämtland"),
        "norrbotten_before": before_nb,
        "norrbotten_after": lan_stats(fvos, "Norrbotten"),
        "hosts": list(HOST_ALLOWLIST.keys()),
    }
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "crossmatch_decontam_log.json").write_text(
        json.dumps({"applied": applied, "stats": data["meta"]["crossmatch_decontam_pass"]}, ensure_ascii=False, indent=2),
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

    print(
        json.dumps(
            {
                "stats": data["meta"]["crossmatch_decontam_pass"],
                "sample": applied[:20],
                "jh_affected": [a for a in applied if a["lan"] == "Jämtland"],
                "became_empty": [a for a in applied if a["blev_tom"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
