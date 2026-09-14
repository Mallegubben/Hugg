#!/usr/bin/env python3
"""
Berika FVO-artlistor från externa källor.

Regler:
- Använd aldrig arttext från iFiske-sidor.
- Om Fiskekartan pekar på iFiske (URL_FKORT), hämta sidan ENBART för att
  plocka ut externa länkar (FVOF-hemsidor m.m.), och scrapa sedan dessa.
- Scrapa också URL_FVOF från Fiskekartan.
- Generiska portaler (fiskerätt, länsförbund, etc.) ignoreras.
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
UA = (
    "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; research; "
    "artförekomst för fritidsfiske)"
)

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
}

# Short / ambiguous names need list-context before acceptance
AMBIGUOUS = {"Id", "Mal", "Asp", "Sik", "Lax", "Ål", "Nors", "Lake", "Kräfta"}

GENERIC_HOST_PARTS = (
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
    "wikipedia.org",
    "tiktok.com",
    "linkedin.com",
    "apple.com",
    "play.google",
    "schemas.",
    "w3.org",
    "fiskeratt.se",
    "fiskekartan.se",
    "havochvatten.se",
    "lansstyrelsen.se",
    "vattenagarna.se",
    "sportfiskarna.se",
    "fiskevattenagarna.se",
    "ifiske.",  # any ifiske TLD
    "youtube.",
    "booking.com",
    "airbnb.",
    "tripadvisor.",
)


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
    return "ifiske." in h


def is_generic_host(url: str) -> bool:
    h = host_of(url)
    return any(part in h for part in GENERIC_HOST_PARTS)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def fvo_tokens(namn: str) -> set[str]:
    base = re.sub(
        r"\b(fvof|fvo|fiskevardsomrade|fiskevårdsområde|fiskevårdsområdesförening|"
        r"förening|forening|besparingsskog|bygdens|sjöarnas|sjoarnas)\b",
        " ",
        namn,
        flags=re.I,
    )
    parts = re.split(r"[\s\-–,_/]+", base)
    out = set()
    for p in parts:
        f = fold(p)
        if len(f) >= 4:
            out.add(f)
    return out


def url_relevant_to_fvo(url: str, namn: str) -> bool:
    """Keep only FVO-ish hosts or hosts that share name tokens with the FVO."""
    h = host_of(url)
    if not h or is_generic_host(url) or is_ifiske(url):
        return False
    if any(k in h for k in ("fvof", "fvo", "flugfiske", "sportfiske")):
        return True
    # bare domain label(s)
    labels = re.split(r"[.-]", h)
    host_fold = fold(h)
    tokens = fvo_tokens(namn)
    if not tokens:
        return False
    for t in tokens:
        if t in host_fold:
            return True
        for lab in labels:
            if fold(lab) == t:
                return True
    return False


def fetch(
    url: str,
    session: requests.Session,
    timeout: int = 25,
    *,
    allow_ifiske: bool = False,
) -> tuple[str | None, str | None]:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"http_{r.status_code}"
        # Reject if redirected onto blocked hosts (unless explicitly allowed for link harvest)
        if not allow_ifiske and (is_ifiske(r.url) or is_generic_host(r.url)):
            return None, "blocked_redirect"
        if allow_ifiske and is_generic_host(r.url) and not is_ifiske(r.url):
            return None, "blocked_redirect"
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            return None, f"skip_ctype:{ctype}"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, None
    except requests.RequestException as e:
        return None, type(e).__name__


def extract_external_links(html: str, base_url: str, fvo_namn: str) -> list[str]:
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
        if not url_relevant_to_fvo(abs_url, fvo_namn):
            continue
        path = urlparse(abs_url).path.lower()
        if any(x in path for x in ("/login", "/cart", "/checkout", "/cdn-cgi")):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    return links


def extract_species_from_html(html: str) -> list[str]:
    """Kräv artlista-kontext; undvik enstaka träffar på generiska sidor."""
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    names = sorted(CANONICAL_SPECIES | set(SPECIES_ALIASES.keys()), key=len, reverse=True)
    # Find all species mentions with positions
    hits: list[tuple[int, str]] = []
    for name in names:
        pat = re.compile(rf"(?<![A-Za-zÅÄÖåäö]){re.escape(name)}(?![A-Za-zÅÄÖåäö])", re.I)
        for m in pat.finditer(text):
            canon = SPECIES_ALIASES.get(name.lower(), name if name[:1].isupper() else name.title())
            hits.append((m.start(), canon))

    if not hits:
        return []

    # Context windows: accept species if nearby another species OR near art-list keywords
    keyword = re.compile(
        r"(artlista|fiskarter|fiskarter|fiskar(?:t|ter)?|förekom(?:mer|mande)|"
        r"fiskevårds|bestånd|vanliga arter|övriga arter|fiskas|fångas)",
        re.I,
    )
    keyword_pos = [m.start() for m in keyword.finditer(text)]

    accepted: set[str] = set()
    positions = sorted(hits)
    for i, (pos, canon) in enumerate(positions):
        near_other = False
        for j, (pos2, canon2) in enumerate(positions):
            if i == j or canon == canon2:
                continue
            if abs(pos - pos2) <= 80:
                near_other = True
                break
        near_kw = any(abs(pos - kp) <= 120 for kp in keyword_pos)
        if canon in AMBIGUOUS:
            if near_other or near_kw:
                accepted.add(canon)
        else:
            if near_other or near_kw:
                accepted.add(canon)

    return sorted(accepted, key=lambda s: s.casefold())


def process_fvo(fvo: dict, session: requests.Session) -> dict:
    oid = fvo["original_id"]
    namn = fvo.get("namn") or ""
    existing = set(fvo.get("arter") or [])
    added: set[str] = set()
    scraped_urls: list[str] = []
    notes: list[str] = []

    candidate_urls: list[str] = []
    fvof = normalize_url(fvo.get("url_fvof"))
    if fvof and not is_ifiske(fvof) and not is_generic_host(fvof):
        candidate_urls.append(fvof)

    ifiske = normalize_url(fvo.get("url_ifiske_for_externa_lankar"))
    if ifiske and is_ifiske(ifiske):
        html, err = fetch(ifiske, session, allow_ifiske=True)
        if html:
            external = extract_external_links(html, ifiske, namn)
            notes.append(f"ifiske_externa_lankar:{len(external)}")
            for u in external[:6]:
                if u not in candidate_urls:
                    candidate_urls.append(u)
        elif err:
            notes.append(f"ifiske_fel:{err}")

    # Deduplicate by host, prefer homepage-ish
    by_host: dict[str, str] = {}
    for url in candidate_urls:
        h = host_of(url)
        if h not in by_host or len(url) < len(by_host[h]):
            by_host[h] = url
    candidate_urls = list(by_host.values())[:4]

    for url in candidate_urls:
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
        "namn": namn,
        "tillagda_arter": sorted(added, key=lambda s: s.casefold()),
        "scrapade_url:er": scraped_urls,
        "notes": notes,
    }


def main() -> None:
    src = OUT / "fvo_artlista.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    fvos = data["fvo"]

    candidates = [
        f
        for f in fvos
        if f.get("url_fvof") or f.get("url_ifiske_for_externa_lankar")
    ]
    print(f"Kandidater med externa/iFiske-länkar: {len(candidates)}")

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
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

    # Refresh CSV
    import csv

    csv_path = OUT / "fvo_artlista.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "original_id",
                "namn",
                "ansvarigt_lan",
                "kommuner",
                "antal_arter",
                "arter",
                "fiskekartan_arter",
                "antal_sjoar_survey",
                "antal_vattendrag_survey",
                "kallor",
                "url_fvof",
                "url_fiskekartan",
            ],
        )
        w.writeheader()
        for f in fvos:
            w.writerow(
                {
                    "original_id": f["original_id"],
                    "namn": f["namn"],
                    "ansvarigt_lan": f["ansvarigt_lan"],
                    "kommuner": f["kommuner"],
                    "antal_arter": f["statistik"]["antal_arter"],
                    "arter": "; ".join(f["arter"]),
                    "fiskekartan_arter": "; ".join(f["fiskekartan"]["arter"]),
                    "antal_sjoar_survey": f["statistik"]["antal_sjoar_survey"],
                    "antal_vattendrag_survey": f["statistik"]["antal_vattendrag_survey"],
                    "kallor": "; ".join(f["kallor"]),
                    "url_fvof": f["url_fvof"] or "",
                    "url_fiskekartan": f["url_fiskekartan"] or "",
                }
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
