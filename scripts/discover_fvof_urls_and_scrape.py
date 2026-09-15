#!/usr/bin/env python3
"""
Gissa FVOF-domäner från FVO-namn och scrapa träffar.

Används för FVO med spegelluckor som saknar tipslänk/URL_FVOF.
iFiske artdata används inte.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
UA = "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; url-guess-fvof)"

# Återanvänd art-extraktion från follow-skriptet
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "follow_ifiske", ROOT / "scripts" / "follow_ifiske_links_to_fvof.py"
)
_follow = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(_follow)


def fold_name(namn: str) -> str:
    s = unicodedata.normalize("NFKD", namn or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\b(fvof|fvo|fiskevardsomrade|fiskevårdsområde|förening)\b", "", s, flags=re.I)
    s = re.sub(r"[^a-zA-Z0-9]+", "", s.lower())
    return s


def guess_urls(namn: str) -> list[str]:
    base = fold_name(namn)
    if len(base) < 4:
        return []
    variants = [base]
    # korta varianter utan sjö/älv-suffix
    for suf in ("sjons", "sjon", "alvens", "alven", "aans", "aan"):
        if base.endswith(suf) and len(base) - len(suf) >= 4:
            variants.append(base[: -len(suf)])
    out: list[str] = []
    for b in variants:
        for tmpl in (
            "https://www.{b}fvo.se/",
            "https://www.{b}fvof.se/",
            "https://{b}fvo.se/",
            "https://{b}fvof.se/",
            "https://www.{b}-fvo.se/",
            "https://www.{b}-fvof.se/",
            "https://www.{b}fiske.se/",
            "https://www.{b}.se/",
        ):
            out.append(tmpl.format(b=b))
    # dedupe
    seen = set()
    uniq = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:16]


def probe(session: requests.Session, url: str) -> str | None:
    try:
        r = session.get(url, timeout=10, allow_redirects=True)
        if r.status_code >= 400:
            return None
        host = urlparse(r.url).netloc.lower()
        if any(
            x in host
            for x in ("ifiske.", "natureit.", "artportalen.", "facebook.", "google.", "parked")
        ):
            return None
        text = (r.text or "")[:8000].casefold()
        if any(x in text for x in ("domain parking", "köp domän", "buy this domain", "sedo")):
            return None
        # kräver någon fiske-signal
        if not any(k in text for k in ("fisk", "fvof", "fvo", "öring", "abborre", "gädda", "fiskevård")):
            # tillåt om host innehåller fvo
            if "fvo" not in host:
                return None
        return r.url
    except requests.RequestException:
        return None


def process_fvo(fvo: dict, session: requests.Session) -> dict:
    namn = fvo.get("namn") or ""
    our = set(fvo.get("arter") or [])
    gaps = set((fvo.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof") or [])
    if not gaps and our:
        return {"original_id": fvo.get("original_id"), "namn": namn, "tillagda": [], "notes": ["skip"]}

    # hoppa om vi redan har tip/url (annan pass sköter dem)
    if fvo.get("ifiske_tipslankar"):
        return {"original_id": fvo.get("original_id"), "namn": namn, "tillagda": [], "notes": ["has_tips"]}
    fvof = (fvo.get("url_fvof") or "").lower()
    if fvof and "ifiske" not in fvof and "vattenagarna" not in fvof:
        return {"original_id": fvo.get("original_id"), "namn": namn, "tillagda": [], "notes": ["has_fvof"]}

    found_url = None
    for u in guess_urls(namn):
        hit = probe(session, u)
        if hit:
            found_url = hit
            break

    if not found_url:
        return {
            "original_id": fvo.get("original_id"),
            "namn": namn,
            "tillagda": [],
            "notes": ["no_guess_hit"],
            "guessed": guess_urls(namn)[:4],
        }

    spp, scraped, notes = _follow.scrape_site(session, found_url)
    added = sorted(set(spp) - our, key=lambda s: s.casefold())
    return {
        "original_id": fvo.get("original_id"),
        "namn": namn,
        "lan": fvo.get("ansvarigt_lan"),
        "tillagda": added,
        "traffade_luckor": sorted(set(added) & gaps, key=lambda s: s.casefold()),
        "found_url": found_url,
        "scraped": scraped,
        "notes": notes,
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    todo = [
        f
        for f in fvos
        if (f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof")
        or not f.get("arter")
    ]
    print(f"URL-gissning för upp till {len(todo)} FVO…")

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html"})

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(process_fvo, f, session): f for f in todo}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                results.append(fut.result())
            except Exception as e:
                f = futs[fut]
                results.append(
                    {
                        "original_id": f.get("original_id"),
                        "namn": f.get("namn"),
                        "tillagda": [],
                        "notes": [f"exception:{type(e).__name__}"],
                    }
                )
            if done % 40 == 0:
                print(f"  {done}/{len(todo)}")
            time.sleep(0.01)

    by_id = {r["original_id"]: r for r in results}
    enriched = 0
    added_total = 0
    gaps_closed = 0
    urls_found = 0
    for fvo in fvos:
        r = by_id.get(fvo["original_id"])
        if not r:
            continue
        if r.get("found_url"):
            urls_found += 1
            tips = list(dict.fromkeys((fvo.get("ifiske_tipslankar") or []) + [r["found_url"]]))
            fvo["ifiske_tipslankar"] = tips  # tipsliknande upptäckt (ej från iFiske-art)
            if not fvo.get("url_fvof"):
                fvo["url_fvof"] = r["found_url"]
        if not r.get("tillagda"):
            continue
        before = set(fvo.get("arter") or [])
        after = sorted(before | set(r["tillagda"]), key=lambda s: s.casefold())
        if len(after) == len(before):
            continue
        fvo["arter"] = after
        fvo.setdefault("kallor", [])
        if "extern_fvof" not in fvo["kallor"]:
            fvo["kallor"].append("extern_fvof")
        prev = fvo.get("extern_enrichment") or {}
        fvo["extern_enrichment"] = {
            "tillagda_arter": sorted(
                set(prev.get("tillagda_arter") or []) | set(r["tillagda"]),
                key=lambda s: s.casefold(),
            ),
            "kallor_url:er": list(
                dict.fromkeys((prev.get("kallor_url:er") or []) + (r.get("scraped") or []))
            ),
        }
        fvo["statistik"]["antal_arter"] = len(after)
        enriched += 1
        added_total += len(set(r["tillagda"]) - before)
        gaps_closed += len(r.get("traffade_luckor") or [])
        ctrl = fvo.get("ifiske_spegel_kontroll")
        if ctrl and ctrl.get("spegel_arter"):
            ctrl["luckor_ej_funna_hos_fvof"] = sorted(
                set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold()
            )

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
    data["meta"]["url_guess_pass"] = {
        "urls_found": urls_found,
        "enriched_fvo": enriched,
        "added_species_entries": added_total,
        "mirror_gaps_closed": gaps_closed,
        "spegel_stats": spegel,
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "url_guess_pass_log.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "urls_found": urls_found,
                "enriched_fvo": enriched,
                "added": added_total,
                "gaps_closed": gaps_closed,
                "spegel_stats": spegel,
                "coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
