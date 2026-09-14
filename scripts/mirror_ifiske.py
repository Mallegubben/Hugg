#!/usr/bin/env python3
"""
Spegla iFiske-artlistor så våra FVO-arter inte avviker från iFiske.

- Primär `arter` = iFiske artikoner när kontroll finns (exakt spegling).
- `arter_utokad` behåller unionen av alla egna källor (survey/GBIF/Fiskekartan).
- Förbättrad URL-upplösning: /o/, /a/, /list/, fiskekort→fiske, trunkerade länkar.
- Extra fokus på Jämtland, men spegling körs för hela Sverige.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
ARTLISTA = OUT / "fvo_artlista.json"
UA = "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; ifiske-spegling)"

SPECIES_ALIASES = {
    "gadda": "Gädda",
    "gädda": "Gädda",
    "mort": "Mört",
    "mört": "Mört",
    "abborre": "Abborre",
    "oring": "Öring",
    "öring": "Öring",
    "gos": "Gös",
    "gös": "Gös",
    "lake": "Lake",
    "braxen": "Braxen",
    "sutare": "Sutare",
    "al": "Ål",
    "ål": "Ål",
    "harr": "Harr",
    "sik": "Sik",
    "sikloja": "Siklöja",
    "siklöja": "Siklöja",
    "roding": "Röding",
    "röding": "Röding",
    "backroding": "Bäckröding",
    "bäckröding": "Bäckröding",
    "regnbage": "Regnbåge",
    "regnbåge": "Regnbåge",
    "lax": "Lax",
    "sarv": "Sarv",
    "id": "Id",
    "bjorkna": "Björkna",
    "björkna": "Björkna",
    "benloja": "Benlöja",
    "benlöja": "Benlöja",
    "ruda": "Ruda",
    "nors": "Nors",
    "asp": "Asp",
    "farna": "Färna",
    "färna": "Färna",
    "karp": "Karp",
    "mal": "Mal",
    "elritsa": "Elritsa",
    "havsoring": "Havsöring",
    "havsöring": "Havsöring",
    "kanadaroding": "Kanadaröding",
    "kanadaröding": "Kanadaröding",
    "signalkrafta": "Signalkräfta",
    "signalkräfta": "Signalkräfta",
    "flodkrafta": "Flodkräfta",
    "flodkräfta": "Flodkräfta",
    "gers": "Gärs",
    "gärs": "Gärs",
    "vimma": "Vimma",
    "stam": "Stäm",
    "stäm": "Stäm",
    "faren": "Faren",
    "bergsimpa": "Bergsimpa",
    "stensimpa": "Stensimpa",
    "hornsimpa": "Hornsimpa",
}


def canon(name: str | None) -> str | None:
    if not name:
        return None
    t = name.strip()
    key = (
        t.lower()
        .replace("å", "a")
        .replace("ä", "a")
        .replace("ö", "o")
        .replace(" ", "")
    )
    # simple fold map via aliases keys already ascii-ish
    low = t.casefold()
    for k, v in SPECIES_ALIASES.items():
        if k == low or k == key:
            return v
    if t[:1].islower():
        t = t[:1].upper() + t[1:]
    return SPECIES_ALIASES.get(low) or (t if t in SPECIES_ALIASES.values() else t if any(t == v for v in SPECIES_ALIASES.values()) else SPECIES_ALIASES.get(low))


def canon_species(name: str | None) -> str | None:
    if not name:
        return None
    low = name.strip().casefold()
    if low in SPECIES_ALIASES:
        return SPECIES_ALIASES[low]
    # filename style
    low2 = low.replace("_", " ")
    if low2 in SPECIES_ALIASES:
        return SPECIES_ALIASES[low2]
    for k, v in SPECIES_ALIASES.items():
        if v.casefold() == low:
            return v
    return None


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = str(url).strip()
    if not u or u.lower() in {"null", "none", "-", "ifiske", "www.ifiske.se", "ifiske.se"}:
        return None
    if u.lower() in {"http://ifiske", "https://ifiske", "http://www.ifiske.se", "https://www.ifiske.se"}:
        return None
    if not u.startswith("http"):
        u = "https://" + u
    return u.rstrip()


def is_ifiske(url: str) -> bool:
    try:
        return "ifiske." in urlparse(url).netloc.lower()
    except Exception:
        return False


def extract_species(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        if "/species/" not in src:
            continue
        alt = img.get("alt") or ""
        m = re.search(r"/species/(?:small/)?([a-z0-9_\-]+)\.", src, re.I)
        raw = alt or (m.group(1).replace("_", " ") if m else "")
        c = canon_species(raw)
        if c:
            found.add(c)
    return sorted(found, key=lambda s: s.casefold())


def fetch(session: requests.Session, url: str):
    try:
        r = session.get(url, timeout=25, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.url, f"http_{r.status_code}"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, r.url, None
    except requests.RequestException as e:
        return None, url, type(e).__name__


def candidate_pages(start_url: str, html: str, final_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        u = normalize_url(u)
        if not u or not is_ifiske(u) or u in seen:
            return
        seen.add(u)
        out.append(u)

    add(final_url or start_url)
    add(start_url)
    for base in (final_url, start_url):
        if base and "/fiskekort-" in base:
            add(base.replace("/fiskekort-", "/fiske-"))
    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"(?:fiske|fiskekort)-[a-z0-9\-]+\.htm", href or "", re.I):
            add(urljoin(final_url or start_url, href))
    return out


def resolve_ifiske_species(session: requests.Session, url: str) -> tuple[list[str], str | None, list[str]]:
    notes: list[str] = []
    html, final, err = fetch(session, url)
    if not html:
        return [], None, [f"fel:{err}"]
    # Try current + follow candidates until species found
    for cand in candidate_pages(url, html, final):
        if cand == final and html:
            spp = extract_species(html)
            page_html, page_url = html, final
        else:
            page_html, page_url, err2 = fetch(session, cand)
            if not page_html:
                notes.append(f"follow_fel:{err2}")
                continue
            spp = extract_species(page_html)
        if spp:
            if cand != url:
                notes.append(f"resolved:{page_url}")
            return spp, page_url, notes
    notes.append("inga_artikoner")
    return [], final, notes


def best_ifiske_url(fvo: dict, rest_by_id: dict) -> str | None:
    candidates = []
    for key in ("url_ifiske_for_externa_lankar", "url_fiskekort"):
        u = normalize_url(fvo.get(key))
        if u and is_ifiske(u):
            candidates.append(u)
    rest = rest_by_id.get(fvo.get("original_id")) or {}
    u = normalize_url(rest.get("URL_FKORT"))
    if u and is_ifiske(u):
        candidates.append(u)
    # dedupe preserve order
    out = []
    seen = set()
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[0] if out else None


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    rest = json.loads((RAW / "fiskekartan_fvof_rest.json").read_text(encoding="utf-8"))
    rest_by_id = {r["ORIGINALID"]: r for r in rest}

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html"})

    # Prioritize Jämtland first in processing order for logging clarity
    ordered = sorted(
        fvos,
        key=lambda f: (0 if f.get("ansvarigt_lan") == "Jämtland" else 1, f.get("namn") or ""),
    )

    def work(fvo: dict) -> dict:
        url = best_ifiske_url(fvo, rest_by_id)
        if not url:
            return {
                "original_id": fvo.get("original_id"),
                "namn": fvo.get("namn"),
                "lan": fvo.get("ansvarigt_lan"),
                "species": [],
                "resolved_url": None,
                "notes": ["ingen_ifiske_url"],
            }
        spp, resolved, notes = resolve_ifiske_species(session, url)
        return {
            "original_id": fvo.get("original_id"),
            "namn": fvo.get("namn"),
            "lan": fvo.get("ansvarigt_lan"),
            "species": spp,
            "resolved_url": resolved,
            "notes": notes,
            "start_url": url,
        }

    print(f"Speglar iFiske för {len(ordered)} FVO (Jämtland först)…")
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, f) for f in ordered]
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 50 == 0:
                ok = sum(1 for r in results if r["species"])
                print(f"  {done}/{len(ordered)} med_artikoner={ok}")
            time.sleep(0.01)

    by_id = {r["original_id"]: r for r in results}

    stats = {
        "speglade": 0,
        "oforandrade_utan_ifiske": 0,
        "jamtland_totalt": 0,
        "jamtland_speglade": 0,
        "jamtland_utan_ifiske": 0,
        "nationellt_med_ifiske": 0,
    }

    for fvo in fvos:
        oid = fvo.get("original_id")
        r = by_id.get(oid) or {}
        spp = list(r.get("species") or [])
        lan = fvo.get("ansvarigt_lan")
        if lan == "Jämtland":
            stats["jamtland_totalt"] += 1

        # Preserve extended list from all prior sources
        utokad = sorted(set(fvo.get("arter_utokad") or fvo.get("arter") or []), key=lambda s: s.casefold())
        # clean broken tokens if any
        utokad = [a for a in utokad if not a.endswith("-") and "Berg-" not in a]

        if spp:
            stats["speglade"] += 1
            stats["nationellt_med_ifiske"] += 1
            if lan == "Jämtland":
                stats["jamtland_speglade"] += 1
            fvo["arter_utokad"] = utokad
            fvo["arter"] = spp  # exact mirror
            fvo["kallor"] = ["ifiske_spegling"] + [
                k for k in (fvo.get("kallor") or []) if k not in ("ifiske_spegling", "kontroll_ifiske")
            ]
            fvo["ifiske_spegling"] = {
                "arter": spp,
                "url": r.get("resolved_url") or r.get("start_url"),
                "start_url": r.get("start_url"),
                "notes": r.get("notes") or [],
            }
            # keep kontroll block aligned
            fvo["ifiske_kontroll"] = {
                "arter": spp,
                "parity_tillagda": [],
                "url": r.get("resolved_url") or r.get("start_url"),
                "anmarkning": "Primär artlista speglad mot iFiske artikoner",
            }
        else:
            stats["oforandrade_utan_ifiske"] += 1
            if lan == "Jämtland":
                stats["jamtland_utan_ifiske"] += 1
            fvo["arter_utokad"] = utokad
            # keep existing arter but ensure cleaned
            fvo["arter"] = sorted(set(fvo.get("arter") or utokad), key=lambda s: s.casefold())
            fvo["arter"] = [a for a in fvo["arter"] if not a.endswith("-") and "Berg-" not in a]

        fvo["statistik"]["antal_arter"] = len(fvo["arter"])
        fvo["statistik"]["antal_arter_utokad"] = len(fvo.get("arter_utokad") or [])

    # coverage
    coverage = {
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_ifiske_spegling": 0,
        "jamtland_har_spegling": 0,
        "jamtland_totalt": stats["jamtland_totalt"],
    }
    catalog: set[str] = set()
    for f in fvos:
        catalog.update(f.get("arter") or [])
        if f.get("arter"):
            coverage["har_nagon_art"] += 1
        else:
            coverage["saknar_arter"] += 1
        if f.get("ifiske_spegling"):
            coverage["har_ifiske_spegling"] += 1
            if f.get("ansvarigt_lan") == "Jämtland":
                coverage["jamtland_har_spegling"] += 1

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = {**(data.get("meta", {}).get("coverage") or {}), **coverage}
    data["meta"]["ifiske_spegling"] = stats
    data["meta"]["policy"] = {
        "ifiske_spegling": True,
        "beskrivning": (
            "När iFiske har artikoner speglas `arter` exakt mot dem så listan inte avviker. "
            "Utökad lista från SLU/GBIF/Fiskekartan finns i `arter_utokad` och per vatten i `vatten[]`."
        ),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    data["meta"]["antal_unika_arter"] = len(catalog)

    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "ifiske_spegling_log.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "ifiske_spegling_summary.json").write_text(
        json.dumps({"stats": stats, "coverage": coverage}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Jämtland report
    j = [f for f in fvos if f.get("ansvarigt_lan") == "Jämtland"]
    j_report = []
    for f in j:
        j_report.append(
            {
                "namn": f["namn"],
                "antal_arter": len(f.get("arter") or []),
                "arter": f.get("arter") or [],
                "speglad": bool(f.get("ifiske_spegling")),
                "url": (f.get("ifiske_spegling") or {}).get("url"),
                "antal_arter_utokad": len(f.get("arter_utokad") or []),
            }
        )
    (OUT / "jamtland_spegling.json").write_text(
        json.dumps(j_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"stats": stats, "coverage": coverage}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
