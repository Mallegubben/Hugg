#!/usr/bin/env python3
"""
iFiske som SPEGEL/kontroll – aldrig som artdata.

1) Läs artikoner på iFiske enbart för att se vilka arter som "borde" finnas.
2) För luckor: följ externa länkar från iFiske-sidan + URL_FVOF och scrapa
   FVOF-/föreningssidor (tillåtna källor).
3) Skriv ALDRIG iFiske-arter in i `arter`. Bara till meta/kontrollrapport.
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
UA = (
    "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; "
    "ifiske-spegel-kontroll; artdata endast från FVOF/andra källor)"
)

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
    "gronling": "Grönling",
    "grönling": "Grönling",
}

CANONICAL = set(SPECIES_ALIASES.values())
AMBIGUOUS = {"Id", "Mal", "Asp", "Sik", "Lax", "Ål", "Nors", "Lake"}
GENERIC_HOST_PARTS = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "google.",
    "2glux.com",
    "wikipedia.org",
    "fiskeratt.se",
    "fiskekartan.se",
    "havochvatten.se",
    "lansstyrelsen.se",
    "vattenagarna.se",
    "sportfiskarna.se",
    "ifiske.",
    "paypal",
    "swish",
)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def canon_species(name: str | None) -> str | None:
    if not name:
        return None
    low = name.strip().casefold()
    if low in SPECIES_ALIASES:
        return SPECIES_ALIASES[low]
    for k, v in SPECIES_ALIASES.items():
        if v.casefold() == low:
            return v
    return None


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = str(url).strip()
    if not u or u.lower() in {
        "null",
        "none",
        "-",
        "ifiske",
        "www.ifiske.se",
        "ifiske.se",
        "http://ifiske",
        "https://ifiske",
        "http://www.ifiske.se",
        "https://www.ifiske.se",
    }:
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
    return "ifiske." in host_of(url)


def is_generic(url: str) -> bool:
    h = host_of(url)
    return any(p in h for p in GENERIC_HOST_PARTS)


def fetch(session: requests.Session, url: str, *, allow_ifiske: bool = False):
    try:
        r = session.get(url, timeout=25, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.url, f"http_{r.status_code}"
        if not allow_ifiske and (is_ifiske(r.url) or is_generic(r.url)):
            return None, r.url, "blocked"
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return None, r.url, "ctype"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, r.url, None
    except requests.RequestException as e:
        return None, url, type(e).__name__


def read_ifiske_mirror_species(html: str) -> list[str]:
    """SPEGEL: artikoner – används bara för kontroll, aldrig skrivs till arter."""
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


def ifiske_follow_candidates(url: str, html: str, final_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        u = normalize_url(u)
        if not u or not is_ifiske(u) or u in seen:
            return
        seen.add(u)
        out.append(u)

    add(final_url)
    add(url)
    if final_url and "/fiskekort-" in final_url:
        add(final_url.replace("/fiskekort-", "/fiske-"))
    if url and "/fiskekort-" in url:
        add(url.replace("/fiskekort-", "/fiske-"))
    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"(?:fiske|fiskekort)-[a-z0-9\-]+\.htm", href or "", re.I):
            add(urljoin(final_url or url, href))
    return out


def extract_external_fvof_links(html: str, base: str, fvo_namn: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    tokens = set()
    base_name = re.sub(
        r"\b(fvof|fvo|fiskevårdsområde|förening)\b", " ", fvo_namn or "", flags=re.I
    )
    for p in re.split(r"[\s\-–,/_]+", base_name):
        t = fold(p)
        if len(t) >= 4:
            tokens.add(t)
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        abs_url = urljoin(base, href)
        if not abs_url.startswith("http") or is_ifiske(abs_url) or is_generic(abs_url):
            continue
        h = host_of(abs_url)
        host_f = fold(h)
        relevant = any(k in h for k in ("fvof", "fvo", "fiske")) or any(
            t in host_f for t in tokens
        )
        if not relevant:
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    return links[:8]


def extract_species_from_fvof_html(html: str) -> list[str]:
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    names = sorted(CANONICAL | set(SPECIES_ALIASES.keys()), key=len, reverse=True)
    hits: list[tuple[int, str]] = []
    for name in names:
        pat = re.compile(rf"(?<![A-Za-zÅÄÖåäö]){re.escape(name)}(?![A-Za-zÅÄÖåäö])", re.I)
        for m in pat.finditer(text):
            c = canon_species(name)
            if c:
                hits.append((m.start(), c))
    if not hits:
        return []
    keyword = re.compile(
        r"(artlista|fiskarter|fiskar(?:t|ter)?|förekom|bestånd|vanliga arter|övriga arter|fiskevårds)",
        re.I,
    )
    kw = [m.start() for m in keyword.finditer(text)]
    accepted: set[str] = set()
    for i, (pos, canon) in enumerate(hits):
        near_other = any(
            abs(pos - pos2) <= 80 and canon != c2
            for j, (pos2, c2) in enumerate(hits)
            if i != j
        )
        near_kw = any(abs(pos - k) <= 120 for k in kw)
        if canon in AMBIGUOUS:
            if near_other or near_kw:
                accepted.add(canon)
        elif near_other or near_kw:
            accepted.add(canon)
    return sorted(accepted, key=lambda s: s.casefold())


def process_fvo(fvo: dict, session: requests.Session) -> dict:
    namn = fvo.get("namn") or ""
    our = set(fvo.get("arter") or [])
    ifiske_url = normalize_url(fvo.get("url_ifiske_for_externa_lankar") or fvo.get("url_fiskekort"))
    fvof_url = normalize_url(fvo.get("url_fvof"))

    mirror: list[str] = []
    from_fvof: set[str] = set()
    scraped: list[str] = []
    notes: list[str] = []
    html_ifiske = None
    resolved_ifiske = None

    if ifiske_url and is_ifiske(ifiske_url):
        html, final, err = fetch(session, ifiske_url, allow_ifiske=True)
        if html:
            html_ifiske, resolved_ifiske = html, final
            mirror = read_ifiske_mirror_species(html)
            if not mirror:
                for cand in ifiske_follow_candidates(ifiske_url, html, final):
                    if cand in (ifiske_url, final):
                        continue
                    html2, final2, err2 = fetch(session, cand, allow_ifiske=True)
                    if not html2:
                        notes.append(f"ifiske_follow_fel:{err2}")
                        continue
                    spp = read_ifiske_mirror_species(html2)
                    if spp:
                        mirror = spp
                        html_ifiske, resolved_ifiske = html2, final2
                        notes.append(f"ifiske_spegel_sida:{final2}")
                        break
        else:
            notes.append(f"ifiske_fel:{err}")

    # Tillåtna källor: URL_FVOF + externa länkar från iFiske-sidan
    candidates: list[str] = []
    if fvof_url and not is_ifiske(fvof_url) and not is_generic(fvof_url):
        candidates.append(fvof_url)
    if html_ifiske and resolved_ifiske:
        for u in extract_external_fvof_links(html_ifiske, resolved_ifiske, namn):
            if u not in candidates:
                candidates.append(u)

    for url in candidates[:6]:
        html, final, err = fetch(session, url, allow_ifiske=False)
        if not html:
            notes.append(f"skip:{host_of(url)}:{err}")
            continue
        scraped.append(final or url)
        for s in extract_species_from_fvof_html(html):
            if s not in our:
                from_fvof.add(s)

    gaps = sorted(set(mirror) - our, key=lambda s: s.casefold()) if mirror else []
    filled = sorted(set(from_fvof) & set(gaps), key=lambda s: s.casefold()) if gaps else sorted(from_fvof, key=lambda s: s.casefold())
    still_gap = sorted(set(gaps) - from_fvof, key=lambda s: s.casefold()) if gaps else []

    return {
        "original_id": fvo.get("original_id"),
        "namn": namn,
        "lan": fvo.get("ansvarigt_lan"),
        "ifiske_spegel": mirror,  # endast kontroll
        "from_fvof": sorted(from_fvof, key=lambda s: s.casefold()),
        "gaps_mot_spegel": gaps,
        "filled_from_fvof": filled,
        "still_gap": still_gap,
        "scraped": scraped,
        "notes": notes,
        "ifiske_url": resolved_ifiske or ifiske_url,
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    candidates = [
        f
        for f in fvos
        if f.get("url_ifiske_for_externa_lankar")
        or f.get("url_fiskekort")
        or f.get("url_fvof")
    ]
    # Jämtland först
    candidates.sort(
        key=lambda f: (0 if f.get("ansvarigt_lan") == "Jämtland" else 1, f.get("namn") or "")
    )
    print(f"Spegelkontroll + FVOF-fyllning för {len(candidates)} FVO…")

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(process_fvo, f, session): f for f in candidates}
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
                        "lan": f.get("ansvarigt_lan"),
                        "ifiske_spegel": [],
                        "from_fvof": [],
                        "gaps_mot_spegel": [],
                        "filled_from_fvof": [],
                        "still_gap": [],
                        "scraped": [],
                        "notes": [f"exception:{type(e).__name__}"],
                    }
                )
            if done % 50 == 0:
                print(f"  {done}/{len(candidates)}")
            time.sleep(0.01)

    by_id = {r["original_id"]: r for r in results}
    stats = {
        "med_spegel": 0,
        "battre_an_spegel": 0,
        "lika_spegel": 0,
        "samre_spegel": 0,  # luckor kvar efter FVOF-fyllning
        "fvof_fyllda_fvo": 0,
        "fvof_artposter": 0,
        "kvarvarande_luckor_mot_spegel": 0,
    }

    for fvo in fvos:
        r = by_id.get(fvo["original_id"])
        if not r:
            continue
        our = set(fvo.get("arter") or [])
        from_fvof = set(r.get("from_fvof") or [])
        mirror = set(r.get("ifiske_spegel") or [])

        added = sorted(from_fvof - our, key=lambda s: s.casefold())
        if added:
            our |= set(added)
            fvo["arter"] = sorted(our, key=lambda s: s.casefold())
            fvo.setdefault("kallor", [])
            if "extern_fvof" not in fvo["kallor"]:
                fvo["kallor"].append("extern_fvof")
            fvo["extern_enrichment"] = {
                "tillagda_arter": added,
                "kallor_url:er": r.get("scraped") or [],
            }
            fvo["statistik"]["antal_arter"] = len(fvo["arter"])
            stats["fvof_fyllda_fvo"] += 1
            stats["fvof_artposter"] += len(added)

        # Spegel lagras endast som kontrollmeta – INTE som artdata
        if mirror:
            still = sorted(mirror - set(fvo.get("arter") or []), key=lambda s: s.casefold())
            fvo["ifiske_spegel_kontroll"] = {
                "spegel_arter": sorted(mirror, key=lambda s: s.casefold()),
                "luckor_ej_funna_hos_fvof": still,
                "url": r.get("ifiske_url"),
                "anmarkning": (
                    "Endast spegel/kontroll. Artdata kommer inte från iFiske – "
                    "luckor ska fyllas via FVOF eller andra tillåtna källor."
                ),
            }
            stats["med_spegel"] += 1
            our2 = set(fvo.get("arter") or [])
            if our2 >= mirror and len(our2) > len(mirror):
                stats["battre_an_spegel"] += 1
            elif our2 >= mirror:
                stats["lika_spegel"] += 1
            else:
                stats["samre_spegel"] += 1
                stats["kvarvarande_luckor_mot_spegel"] += len(still)

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

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["ifiske_spegel"] = stats
    data["meta"]["policy"] = {
        "ifiske_artdata": False,
        "ifiske_spegel": True,
        "beskrivning": (
            "iFiske används endast som spegel/kontroll (artikoner). "
            "All artdata hämtas från Fiskekartan, SLU, GBIF och respektive FVOF-sida."
        ),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "ifiske_spegel_kontroll.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "ifiske_spegel_summary.json").write_text(
        json.dumps({"stats": stats, "coverage": coverage}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"stats": stats, "coverage": coverage}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
