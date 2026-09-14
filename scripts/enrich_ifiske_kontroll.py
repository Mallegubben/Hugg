#!/usr/bin/env python3
"""
iFiske-kontroll + gap-fyllning.

1) Läser artikoner (/img/species/) från iFiske-sidor som KONTROLL (benchmark).
2) Jämför mot vår artlista.
3) För luckor: följ externa länkar från iFiske-sidan och scrapa FVOF-sajter.
4) Om lucka kvarstår efter det: fyll till paritet från kontrollen (källa: kontroll_ifiske)
   så att vi är minst lika bra som iFiske, med transparent provenance.
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
    "kontrolljämförelse mot iFiske + gapfyllning via externa länkar)"
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
    "loja": "Benlöja",
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
    "gronling": "Grönling",
    "grönling": "Grönling",
    "bergsimpa": "Bergsimpa",
    "stensimpa": "Stensimpa",
    "hornsimpa": "Hornsimpa",
    "nissoega": "Nissöga",
    "nissöga": "Nissöga",
}

CANONICAL = set(SPECIES_ALIASES.values())

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


def canon_species(name: str) -> str | None:
    if not name:
        return None
    t = name.strip()
    low = fold(t)
    if low in SPECIES_ALIASES:
        return SPECIES_ALIASES[low]
    # title-ish
    for k, v in SPECIES_ALIASES.items():
        if fold(v) == low:
            return v
    if t in CANONICAL:
        return t
    return None


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
    return "ifiske." in host_of(url)


def is_generic(url: str) -> bool:
    h = host_of(url)
    return any(p in h for p in GENERIC_HOST_PARTS)


def fetch(session: requests.Session, url: str, allow_ifiske: bool = False):
    try:
        r = session.get(url, timeout=25, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"http_{r.status_code}"
        if not allow_ifiske and (is_ifiske(r.url) or is_generic(r.url)):
            return None, "blocked"
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return None, "ctype"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, None
    except requests.RequestException as e:
        return None, type(e).__name__


def extract_ifiske_species(html: str) -> list[str]:
    """Kontroll: artikoner under /img/species/."""
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        if "/img/species/" not in src and "/images/species/" not in src:
            continue
        alt = img.get("alt") or ""
        # fallback from filename
        m = re.search(r"/species/(?:small/)?([a-z0-9_\-]+)\.(?:png|jpe?g|gif|webp)", src, re.I)
        raw = alt or (m.group(1).replace("_", " ") if m else "")
        c = canon_species(raw)
        if c:
            found.add(c)
    return sorted(found, key=lambda s: s.casefold())


def ifiske_species_page_candidates(url: str, html: str) -> list[str]:
    """Hitta trolig fiske-*.htm-sida med artikoner från fiskekort/list-URL."""
    out: list[str] = []
    seen = set()

    def add(u: str) -> None:
        u = normalize_url(u)
        if not u or u in seen or not is_ifiske(u):
            return
        seen.add(u)
        out.append(u)

    add(url)
    # fiskekort-X.htm -> fiske-X.htm
    if "/fiskekort-" in url:
        add(url.replace("/fiskekort-", "/fiske-"))
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"fiske-[a-z0-9\-]+\.htm", href, re.I):
            add(urljoin(url, href))
    return out


def extract_external_links(html: str, base: str, fvo_namn: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    tokens = set()
    base_name = re.sub(
        r"\b(fvof|fvo|fiskevårdsområde|förening)\b", " ", fvo_namn or "", flags=re.I
    )
    for p in re.split(r"[\s\-–,/_]+", base_name):
        f = fold(p)
        if len(f) >= 4:
            tokens.add(f)
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
        if not relevant and not tokens:
            # still allow .se association-looking hosts when nameless
            relevant = h.endswith(".se") and not any(
                x in h for x in ("google", "facebook", "microsoft")
            )
        if not relevant:
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    return links[:8]


def extract_species_from_html(html: str) -> list[str]:
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
        r"(artlista|fiskarter|fiskar(?:t|ter)?|förekom|bestånd|vanliga arter|övriga arter)",
        re.I,
    )
    kw = [m.start() for m in keyword.finditer(text)]
    amb = {"Id", "Mal", "Asp", "Sik", "Lax", "Ål", "Nors", "Lake"}
    accepted: set[str] = set()
    for i, (pos, canon) in enumerate(hits):
        near_other = any(
            abs(pos - pos2) <= 80 and canon != c2 for j, (pos2, c2) in enumerate(hits) if i != j
        )
        near_kw = any(abs(pos - k) <= 120 for k in kw)
        if canon in amb:
            if near_other or near_kw:
                accepted.add(canon)
        elif near_other or near_kw:
            accepted.add(canon)
    return sorted(accepted, key=lambda s: s.casefold())


def clean_broken_species(arter: list[str]) -> list[str]:
    """Fix truncated tokens like 'Simpa (Berg-' / 'Sten-'."""
    joined = " ".join(arter)
    out: set[str] = set()
    skip_frags = {"simpa (berg-", "sten-", "berg-", "simpa (berg-/sten-)"}
    for a in arter:
        low = a.casefold()
        if low in skip_frags or low.endswith("-") and len(a) < 12:
            continue
        if "berg-/" in low or "simpa (berg" in low:
            out.add("Simpa")
            continue
        c = canon_species(a) or a
        if c:
            out.add(c)
    # if original had broken simpa pair, ensure Simpa present when either fragment existed
    if any("simpa" in a.casefold() or a.casefold() == "sten-" for a in arter):
        if any("berg" in a.casefold() or "sten" in a.casefold() for a in arter):
            out.add("Simpa")
    return sorted(out, key=lambda s: s.casefold())


def process_fvo(fvo: dict, session: requests.Session) -> dict:
    oid = fvo.get("original_id")
    namn = fvo.get("namn") or ""
    our = set(clean_broken_species(list(fvo.get("arter") or [])))
    ifiske_url = normalize_url(fvo.get("url_ifiske_for_externa_lankar"))
    fvof_url = normalize_url(fvo.get("url_fvof"))

    kontroll: list[str] = []
    from_links: set[str] = set()
    notes: list[str] = []
    scraped: list[str] = []

    html_ifiske = None
    if ifiske_url and is_ifiske(ifiske_url):
        html_ifiske, err = fetch(session, ifiske_url, allow_ifiske=True)
        if html_ifiske:
            kontroll = extract_ifiske_species(html_ifiske)
            # Om artikoner saknas: följ till fiske-*.htm
            if not kontroll:
                for cand in ifiske_species_page_candidates(ifiske_url, html_ifiske):
                    if cand == ifiske_url:
                        continue
                    html2, err2 = fetch(session, cand, allow_ifiske=True)
                    if not html2:
                        notes.append(f"ifiske_follow_fel:{err2}")
                        continue
                    spp = extract_ifiske_species(html2)
                    if spp:
                        kontroll = spp
                        notes.append(f"ifiske_follow:{cand}")
                        # use this page for external links too
                        html_ifiske = html2
                        ifiske_url = cand
                        break
        else:
            notes.append(f"ifiske_fel:{err}")

    # Follow external links + URL_FVOF to fill gaps vs kontroll
    candidates = []
    if fvof_url and not is_ifiske(fvof_url) and not is_generic(fvof_url):
        candidates.append(fvof_url)
    if html_ifiske:
        for u in extract_external_links(html_ifiske, ifiske_url, namn):
            if u not in candidates:
                candidates.append(u)

    target_gaps = set(kontroll) - our if kontroll else set()
    for url in candidates[:5]:
        html, err = fetch(session, url)
        if not html:
            notes.append(f"skip:{host_of(url)}:{err}")
            continue
        scraped.append(url)
        spp = extract_species_from_html(html)
        for s in spp:
            if s not in our:
                from_links.add(s)

    # Parity fill from control for remaining gaps
    parity = sorted((set(kontroll) - our) - from_links, key=lambda s: s.casefold())

    return {
        "original_id": oid,
        "namn": namn,
        "kontroll_ifiske": kontroll,
        "from_links": sorted(from_links, key=lambda s: s.casefold()),
        "parity_fill": parity,
        "our_before": sorted(our, key=lambda s: s.casefold()),
        "scraped": scraped,
        "notes": notes,
        "ifiske_url": ifiske_url,
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]

    # First clean broken tokens everywhere
    for fvo in fvos:
        fvo["arter"] = clean_broken_species(list(fvo.get("arter") or []))
        for v in fvo.get("vatten") or []:
            v["arter"] = clean_broken_species(list(v.get("arter") or []))
        fvo["statistik"]["antal_arter"] = len(fvo["arter"])

    candidates = [
        f
        for f in fvos
        if f.get("url_ifiske_for_externa_lankar") or f.get("url_fvof")
    ]
    print(f"Kontrollkörning för {len(candidates)} FVO med iFiske/FVOF-länk…")

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
                        "kontroll_ifiske": [],
                        "from_links": [],
                        "parity_fill": [],
                        "our_before": list(f.get("arter") or []),
                        "scraped": [],
                        "notes": [f"exception:{type(e).__name__}"],
                        "ifiske_url": f.get("url_ifiske_for_externa_lankar"),
                    }
                )
            if done % 50 == 0:
                print(f"  {done}/{len(candidates)}")
            time.sleep(0.01)

    by_id = {r["original_id"]: r for r in results}

    stats = {
        "med_kontroll": 0,
        "battre_an_ifiske": 0,
        "lika": 0,
        "samre": 0,
        "parity_fyllda_fvo": 0,
        "parity_artposter": 0,
        "link_fyllda_fvo": 0,
        "link_artposter": 0,
        "saknar_kontroll_data": 0,
    }

    for fvo in fvos:
        r = by_id.get(fvo["original_id"])
        if not r:
            continue
        our = set(fvo.get("arter") or [])
        kontroll = set(r.get("kontroll_ifiske") or [])
        from_links = set(r.get("from_links") or [])
        parity = set(r.get("parity_fill") or [])

        added_link = sorted(from_links - our, key=lambda s: s.casefold())
        if added_link:
            our |= set(added_link)
            fvo.setdefault("kallor", [])
            if "extern_fvof" not in fvo["kallor"]:
                fvo["kallor"].append("extern_fvof")
            fvo.setdefault("extern_enrichment", {})
            prev = set(fvo["extern_enrichment"].get("tillagda_arter") or [])
            fvo["extern_enrichment"] = {
                "tillagda_arter": sorted(prev | set(added_link), key=lambda s: s.casefold()),
                "kallor_url:er": list(
                    dict.fromkeys(
                        (fvo.get("extern_enrichment", {}).get("kallor_url:er") or [])
                        + (r.get("scraped") or [])
                    )
                ),
            }
            stats["link_fyllda_fvo"] += 1
            stats["link_artposter"] += len(added_link)

        # Recompute parity after link fill
        still_gap = sorted(kontroll - our, key=lambda s: s.casefold())
        if still_gap:
            our |= set(still_gap)
            fvo.setdefault("kallor", [])
            if "kontroll_ifiske" not in fvo["kallor"]:
                fvo["kallor"].append("kontroll_ifiske")
            fvo["ifiske_kontroll"] = {
                "arter": sorted(kontroll, key=lambda s: s.casefold()),
                "parity_tillagda": still_gap,
                "url": r.get("ifiske_url"),
                "anmarkning": "Tillagt för paritet mot iFiske-kontroll (artikoner); ej primär källa",
            }
            stats["parity_fyllda_fvo"] += 1
            stats["parity_artposter"] += len(still_gap)
        elif kontroll:
            fvo["ifiske_kontroll"] = {
                "arter": sorted(kontroll, key=lambda s: s.casefold()),
                "parity_tillagda": [],
                "url": r.get("ifiske_url"),
                "anmarkning": "Kontroll: vår lista täcker iFiske",
            }

        fvo["arter"] = sorted(our, key=lambda s: s.casefold())
        fvo["statistik"]["antal_arter"] = len(fvo["arter"])

        if kontroll:
            stats["med_kontroll"] += 1
            if our >= kontroll and len(our) > len(kontroll):
                stats["battre_an_ifiske"] += 1
            elif our >= kontroll:
                stats["lika"] += 1
            else:
                stats["samre"] += 1
        else:
            if r.get("ifiske_url"):
                stats["saknar_kontroll_data"] += 1

    # coverage
    catalog: set[str] = set()
    coverage = {
        "har_fiskekartan_arter": 0,
        "har_survey_arter": 0,
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_vattenposter": 0,
        "har_extern_enrichment": 0,
        "har_gbif_enrichment": 0,
        "har_ifiske_kontroll": 0,
        "har_kontroll_parity_fill": 0,
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
        if f.get("ifiske_kontroll"):
            coverage["har_ifiske_kontroll"] += 1
        if f.get("ifiske_kontroll", {}).get("parity_tillagda"):
            coverage["har_kontroll_parity_fill"] += 1

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["ifiske_kontroll"] = stats
    data["meta"]["policy"] = {
        "ifiske_artdata_som_primarkalla": False,
        "ifiske_anvandning": (
            "Kontroll via artikoner (/img/species/). Externa länkar scrapas för gapfyllning. "
            "Kvarvarande luckor fylls för paritet och märks kontroll_ifiske."
        ),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())

    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "ifiske_kontroll_log.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "ifiske_kontroll_summary.json").write_text(
        json.dumps(
            {"generated_at": data["meta"]["generated_at"], "stats": stats, "coverage": coverage},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"stats": stats, "coverage": coverage}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
