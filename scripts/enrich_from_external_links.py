#!/usr/bin/env python3
"""
Berika FVO-artlistor från externa källor.

Regler:
- Använd aldrig arttext från iFiske-sidor.
- Om Fiskekartan pekar på iFiske (URL_FKORT), hämta sidan ENBART för att
  plocka ut externa länkar (FVOF-hemsidor m.m.), och scrapa sedan dessa.
- Scrapa också URL_FVOF från Fiskekartan.
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
CACHE = ROOT / "data" / "raw" / "external_pages"
UA = (
    "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; research; "
    "artförekomst för fritidsfiske)"
)

# Same normalizer as build script (kept local to avoid import path issues)
SPECIES_ALIASES = {
    "gers": "Gärs",
    "gärs": "Gärs",
    "öring": "Öring",
    "regnbåge": "Regnbåge",
    "regnbågslax": "Regnbåge",
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
    "signalkräfta": "Signalkräfta",
    "flodkräfta": "Flodkräfta",
    "småspigg": "Småspigg",
    "storspigg": "Storspigg",
    "hornsimpa": "Hornsimpa",
    "nissöga": "Nissöga",
    "vimma": "Vimma",
    "stäm": "Stäm",
}

CANONICAL_SPECIES = set(SPECIES_ALIASES.values()) | {
    "Nejonöga",
    "Simpa",
    "Kräfta",
    "Groplöja",
    "Skärkniv",
    "Tånglake",
}

IFISKE_HOSTS = {"ifiske.se", "www.ifiske.se"}


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u or u.lower() in {"null", "none", "-"}:
        return None
    if not u.startswith("http"):
        u = "https://" + u
    return u.rstrip()


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_ifiske(url: str) -> bool:
    h = host_of(url)
    return h in IFISKE_HOSTS or h.endswith(".ifiske.se")


def fetch(url: str, session: requests.Session, timeout: int = 25) -> tuple[str | None, str | None]:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"http_{r.status_code}"
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            return None, f"skip_ctype:{ctype}"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, None
    except requests.RequestException as e:
        return None, type(e).__name__


def extract_external_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        abs_url = urljoin(base_url, href)
        if not abs_url.startswith("http"):
            continue
        if is_ifiske(abs_url):
            continue
        # Skip social / payment / generic noise
        h = host_of(abs_url)
        if any(
            x in h
            for x in (
                "facebook.com",
                "instagram.com",
                "youtube.com",
                "twitter.com",
                "x.com",
                "google.",
                "swish",
                "paypal",
                "bankid",
                "2glux.com",
                "joomla",
                "wordpress.org",
                "wikipedia.org",
                "tiktok.com",
                "linkedin.com",
                "apple.com",
                "play.google",
                "schemas.",
                "w3.org",
            )
        ):
            continue
        path = urlparse(abs_url).path.lower()
        if any(x in path for x in ("/login", "/cart", "/checkout", "/cdn-cgi")):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    # Prefer association / fishing-looking hosts
    def score(u: str) -> tuple:
        h = host_of(u)
        s = 0
        if any(k in h for k in ("fvof", "fvo", "fiske", "sportfiske", "flugfiske")):
            s -= 10
        if h.endswith(".se"):
            s -= 1
        return (s, u)

    return sorted(links, key=score)


def extract_species_from_html(html: str) -> list[str]:
    """Hitta kända fiskartnamn i sidtext (heuristik)."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    # Build regex of known species (longest first)
    names = sorted(CANONICAL_SPECIES | set(SPECIES_ALIASES.keys()), key=len, reverse=True)
    found: list[str] = []
    seen: set[str] = set()
    for name in names:
        # word-ish boundaries for Swedish
        pat = re.compile(rf"(?<![A-Za-zÅÄÖåäö]){re.escape(name)}(?![A-Za-zÅÄÖåäö])", re.I)
        if pat.search(text):
            canon = SPECIES_ALIASES.get(name.lower(), name if name[:1].isupper() else name.title())
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
    return sorted(found, key=lambda s: s.casefold())


def process_fvo(fvo: dict, session: requests.Session) -> dict:
    oid = fvo["original_id"]
    existing = set(fvo.get("arter") or [])
    added: set[str] = set()
    scraped_urls: list[str] = []
    notes: list[str] = []

    candidate_urls: list[str] = []
    fvof = normalize_url(fvo.get("url_fvof"))
    if fvof and not is_ifiske(fvof):
        candidate_urls.append(fvof)

    ifiske = normalize_url(fvo.get("url_ifiske_for_externa_lankar"))
    if ifiske and is_ifiske(ifiske):
        html, err = fetch(ifiske, session)
        if html:
            # Endast externa länkar – ingen artparse av iFiske-HTML
            external = extract_external_links(html, ifiske)
            notes.append(f"ifiske_externa_lankar:{len(external)}")
            for u in external[:8]:
                if u not in candidate_urls:
                    candidate_urls.append(u)
        elif err:
            notes.append(f"ifiske_fel:{err}")

    for url in candidate_urls[:6]:
        html, err = fetch(url, session)
        if not html:
            notes.append(f"skip:{host_of(url)}:{err}")
            continue
        scraped_urls.append(url)
        spp = extract_species_from_html(html)
        for s in spp:
            if s not in existing:
                added.add(s)

    return {
        "original_id": oid,
        "namn": fvo.get("namn"),
        "tillagda_arter": sorted(added, key=lambda s: s.casefold()),
        "scrapade_url:er": scraped_urls,
        "notes": notes,
    }


def main() -> None:
    src = OUT / "fvo_artlista.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    fvos = data["fvo"]

    # Prioritera FVO utan arter, sedan de med få arter
    todo = sorted(
        fvos,
        key=lambda f: (
            0 if not f.get("arter") else 1,
            f.get("statistik", {}).get("antal_arter", 0),
            (f.get("namn") or ""),
        ),
    )

    # Begränsa initial körning till de som har externa URL:er att följa
    candidates = [
        f
        for f in todo
        if f.get("url_fvof") or f.get("url_ifiske_for_externa_lankar")
    ]
    print(f"Kandidater med externa/iFiske-länkar: {len(candidates)}")

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})

    results = []
    # Parallel but polite
    workers = 8
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_fvo, f, session): f for f in candidates}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                f = futs[fut]
                res = {
                    "original_id": f["original_id"],
                    "namn": f.get("namn"),
                    "tillagda_arter": [],
                    "scrapade_url:er": [],
                    "notes": [f"exception:{type(e).__name__}"],
                }
            results.append(res)
            if done % 50 == 0:
                print(f"  {done}/{len(candidates)}")
            time.sleep(0.01)

    by_id = {r["original_id"]: r for r in results}
    enriched_count = 0
    added_total = 0
    for fvo in fvos:
        r = by_id.get(fvo["original_id"])
        if not r or not r["tillagda_arter"]:
            continue
        before = set(fvo.get("arter") or [])
        after = sorted(before | set(r["tillagda_arter"]), key=lambda s: s.casefold())
        if len(after) > len(before):
            fvo["arter"] = after
            fvo.setdefault("kallor", [])
            if "extern_fvof" not in fvo["kallor"]:
                fvo["kallor"].append("extern_fvof")
            fvo["extern_enrichment"] = {
                "tillagda_arter": r["tillagda_arter"],
                "kallor_url:er": r["scrapade_url:er"],
            }
            fvo["statistik"]["antal_arter"] = len(after)
            enriched_count += 1
            added_total += len(set(r["tillagda_arter"]) - before)

    # Recompute catalog + coverage
    catalog: set[str] = set()
    coverage = {
        "har_fiskekartan_arter": 0,
        "har_survey_arter": 0,
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_vattenposter": 0,
        "har_extern_enrichment": 0,
    }
    for f in fvos:
        catalog.update(f.get("arter") or [])
        if f.get("fiskekartan", {}).get("arter"):
            coverage["har_fiskekartan_arter"] += 1
        if any(k in (f.get("kallor") or []) for k in ("slu_provfiske",)):
            coverage["har_survey_arter"] += 1
        if f.get("arter"):
            coverage["har_nagon_art"] += 1
        else:
            coverage["saknar_arter"] += 1
        if f.get("vatten"):
            coverage["har_vattenposter"] += 1
        if f.get("extern_enrichment"):
            coverage["har_extern_enrichment"] += 1

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["extern_enrichment"] = {
        "fvo_uppdaterade": enriched_count,
        "artposter_tillagda": added_total,
        "kandidater": len(candidates),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())

    src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Refresh index + csv lightly via rewrite index
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
        for f in fvos
    ]
    (OUT / "fvo_artlista_index.json").write_text(
        json.dumps(
            {
                "generated_at": data["meta"]["generated_at"],
                "antal_fvo": len(index),
                "fvo": index,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (OUT / "extern_enrichment_log.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "enriched_fvo": enriched_count,
                "added_species_entries": added_total,
                "coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
