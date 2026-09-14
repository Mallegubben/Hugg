#!/usr/bin/env python3
"""
Följ tips från iFiske → hämta artdata från FVOF/andra sajter.

- iFiske används ALDRIG som artdata (inga artikoner skrivs in).
- iFiske används för att hitta externa länkar ("hemsida", föreningssidor).
- Fiskekartans URL_FVOF scrapas alltid (utom rena iFiske-URL:er).
- vattenagarna.se tillåts när sidan är specifik (?o=...).
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
UA = "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; fvof-via-ifiske-lankar)"

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

BLOCK_HOSTS = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "google.",
    "2glux.com",
    "wikipedia.org",
    "fiskeratt.se",
    "fiskekartan.se",
    "havochvatten.se",
    "lansstyrelsen.se",
    "ifiske.",
    "paypal",
    "swish",
    "apple.com",
    "play.google",
)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def canon(name: str | None) -> str | None:
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
    if not u or u.lower() in {"null", "none", "-", "ifiske"}:
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


def is_blocked(url: str) -> bool:
    h = host_of(url)
    if any(p in h for p in BLOCK_HOSTS):
        return True
    # vattenagarna root only – specific ?o= pages are OK
    if "vattenagarna.se" in h:
        qs = parse_qs(urlparse(url).query)
        if "o" not in qs and "/?o=" not in url and "o=" not in url:
            # allow path pages that look specific
            path = urlparse(url).path.strip("/")
            if not path or path.count("/") == 0 and len(path) < 3:
                return True
    return False


def fetch(session: requests.Session, url: str, *, allow_ifiske: bool = False):
    try:
        r = session.get(url, timeout=25, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.url, f"http_{r.status_code}"
        if is_ifiske(r.url) and not allow_ifiske:
            return None, r.url, "ifiske_blocked"
        if is_blocked(r.url) and not (allow_ifiske and is_ifiske(r.url)):
            return None, r.url, "blocked_host"
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return None, r.url, "ctype"
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, r.url, None
    except requests.RequestException as e:
        return None, url, type(e).__name__


def extract_species_from_lists(text: str) -> set[str]:
    """Hitta arter i listor: 'abborre, braxen, gös, gädda och karp'."""
    accepted: set[str] = set()
    names = sorted(CANONICAL | set(SPECIES_ALIASES.keys()), key=len, reverse=True)
    # Meningar/stycken som nämner flera arter nära varandra
    for chunk in re.split(r"[\n.!?]|(?<=\s)/(?=\s)", text):
        chunk = chunk.strip()
        if len(chunk) < 8:
            continue
        found_here: list[str] = []
        for name in names:
            if re.search(rf"(?<![A-Za-zÅÄÖåäö]){re.escape(name)}(?![A-Za-zÅÄÖåäö])", chunk, re.I):
                c = canon(name)
                if c and c not in found_here:
                    found_here.append(c)
        if len(found_here) >= 2:
            for c in found_here:
                if c in AMBIGUOUS and len(found_here) < 3:
                    continue
                accepted.add(c)
    return accepted


def extract_species(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    accepted = extract_species_from_lists(text)

    names = sorted(CANONICAL | set(SPECIES_ALIASES.keys()), key=len, reverse=True)
    hits: list[tuple[int, str]] = []
    for name in names:
        pat = re.compile(rf"(?<![A-Za-zÅÄÖåäö]){re.escape(name)}(?![A-Za-zÅÄÖåäö])", re.I)
        for m in pat.finditer(text):
            c = canon(name)
            if c:
                hits.append((m.start(), c))
    if hits:
        keyword = re.compile(
            r"(artlista|fiskarter|fiskar(?:t|ter)?|förekom|bestånd|vanliga arter|"
            r"övriga arter|fiskevårds|fiskekort|finns (?:bland annat|även)|gott om)",
            re.I,
        )
        kw = [m.start() for m in keyword.finditer(text)]
        for i, (pos, c) in enumerate(hits):
            near_other = any(
                abs(pos - p2) <= 100 and c != c2 for j, (p2, c2) in enumerate(hits) if i != j
            )
            near_kw = any(abs(pos - k) <= 160 for k in kw)
            if c in AMBIGUOUS:
                if near_other or near_kw:
                    accepted.add(c)
            elif near_other or near_kw or len(hits) <= 12:
                accepted.add(c)
    return sorted(accepted, key=lambda s: s.casefold())


def frame_urls(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tag in soup.find_all(["frame", "iframe"]):
        src = tag.get("src")
        if not src:
            continue
        abs_url = urljoin(base, src)
        if abs_url.startswith("http"):
            out.append(abs_url)
    return out


def discover_links_from_ifiske(html: str, base: str, fvo_namn: str) -> list[str]:
    """Tips från iFiske: hemsida/föreningslänkar – aldrig artikoner."""
    soup = BeautifulSoup(html, "lxml")
    tokens = set()
    base_name = re.sub(r"\b(fvof|fvo|förening)\b", " ", fvo_namn or "", flags=re.I)
    for p in re.split(r"[\s\-–,/_]+", base_name):
        t = fold(p)
        if len(t) >= 4:
            tokens.add(t)

    prioritized: list[str] = []
    normal: list[str] = []
    seen: set[str] = set()

    def add(url: str, *, priority: bool = False) -> None:
        u = normalize_url(url)
        if not u or not u.startswith("http") or is_ifiske(u) or is_blocked(u) or u in seen:
            return
        seen.add(u)
        (prioritized if priority else normal).append(u)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = (a.get_text(" ", strip=True) or "").casefold()
        abs_url = urljoin(base, href)
        h = host_of(abs_url)
        host_f = fold(h)
        hemsida = any(k in text for k in ("hemsida", "gå till", "webbplats", "besök vår"))
        relevant = (
            hemsida
            or any(k in h for k in ("fvof", "fvo", "fiske", "natureit", "blogspot"))
            or any(t in host_f for t in tokens)
            or any(k in text for k in ("förening", "fiskevårds", "föreningens"))
            or ("vattenagarna.se" in h and "o=" in abs_url)
        )
        if relevant:
            add(abs_url, priority=hemsida)

    # Rena URL:er i brödtext (ibland utan <a>)
    plain = soup.get_text(" ")
    for m in re.finditer(
        r"(?:https?://|www\.)[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?:/[^\s<>\"']*)?",
        plain,
        re.I,
    ):
        raw = m.group(0).rstrip(".,);]")
        add(raw, priority=False)

    return (prioritized + normal)[:14]


def same_site_art_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_host = host_of(base)
    out = []
    for a in soup.find_all("a", href=True):
        abs_url = urljoin(base, a["href"])
        if host_of(abs_url) != base_host:
            continue
        # Hoppa över generiska vattenagarna-portalsidor utan ?o=
        if "vattenagarna.se" in base_host and is_blocked(abs_url):
            continue
        text = (a.get_text(" ", strip=True) + " " + a["href"]).casefold()
        if any(
            k in text
            for k in (
                "fisk",
                "art",
                "sjö",
                "vatten",
                "fångst",
                "regler",
                "omrade",
                "område",
                "sjo",
            )
        ):
            out.append(abs_url)
    seen = set()
    uniq = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:10]


def scrape_site(session: requests.Session, url: str) -> tuple[set[str], list[str], list[str]]:
    found: set[str] = set()
    scraped: list[str] = []
    notes: list[str] = []
    html, final, err = fetch(session, url)
    if not html:
        return found, scraped, [f"skip:{host_of(url)}:{err}"]
    base = final or url
    scraped.append(base)
    found |= set(extract_species(html))

    # Frames (vanligt på äldre FVOF-sajter)
    for fr in frame_urls(html, base)[:4]:
        html_f, final_f, err_f = fetch(session, fr)
        if not html_f:
            notes.append(f"frame_fel:{err_f}")
            continue
        scraped.append(final_f or fr)
        found |= set(extract_species(html_f))

    for sub in same_site_art_links(html, base)[:8]:
        if sub in scraped:
            continue
        html2, final2, err2 = fetch(session, sub)
        if not html2:
            notes.append(f"sub_fel:{err2}")
            continue
        scraped.append(final2 or sub)
        found |= set(extract_species(html2))
        # En nivå till för frames på undersidor
        for fr in frame_urls(html2, final2 or sub)[:2]:
            if fr in scraped:
                continue
            html3, final3, err3 = fetch(session, fr)
            if html3:
                scraped.append(final3 or fr)
                found |= set(extract_species(html3))
            else:
                notes.append(f"frame_fel:{err3}")
    return found, scraped, notes


def process_fvo(fvo: dict, session: requests.Session) -> dict:
    namn = fvo.get("namn") or ""
    our = set(fvo.get("arter") or [])
    gaps = set((fvo.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof") or [])
    need = bool(gaps) or len(our) < 3 or not our

    candidates: list[str] = []
    fvof = normalize_url(fvo.get("url_fvof"))
    if fvof and not is_ifiske(fvof) and not is_blocked(fvof):
        candidates.append(fvof)
    elif fvof and "vattenagarna.se" in (fvof or "") and "o=" in (fvof or ""):
        candidates.append(fvof)

    # Tidigare funna tipslänkar
    for u in fvo.get("ifiske_tipslankar") or []:
        nu = normalize_url(u)
        if nu and nu not in candidates and not is_ifiske(nu) and not is_blocked(nu):
            candidates.append(nu)

    ifiske = normalize_url(
        fvo.get("url_ifiske_for_externa_lankar")
        or (fvo.get("ifiske_spegel_kontroll") or {}).get("url")
        or fvo.get("url_fiskekort")
    )
    tip_links: list[str] = []
    if ifiske and is_ifiske(ifiske):
        html, final, err = fetch(session, ifiske, allow_ifiske=True)
        if html:
            pages = [(html, final)]
            if "/fiskekort-" in (final or ""):
                alt = (final or "").replace("/fiskekort-", "/fiske-")
                html2, final2, _ = fetch(session, alt, allow_ifiske=True)
                if html2:
                    pages.append((html2, final2))
            if "/fiske-" not in (final or ""):
                for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
                    if re.search(r"fiske-[a-z0-9\-]+\.htm", a["href"] or "", re.I):
                        cu = urljoin(final or ifiske, a["href"])
                        html3, final3, _ = fetch(session, cu, allow_ifiske=True)
                        if html3:
                            pages.append((html3, final3))
                            break
            for page_html, page_url in pages:
                for u in discover_links_from_ifiske(page_html, page_url, namn):
                    if u not in tip_links:
                        tip_links.append(u)
                    if u not in candidates:
                        candidates.append(u)
        else:
            return {
                "original_id": fvo.get("original_id"),
                "namn": namn,
                "tillagda": [],
                "scraped": [],
                "notes": [f"ifiske_tip_fel:{err}"],
                "candidates": candidates[:10],
                "tipslankar": tip_links,
            }

    if not need:
        return {
            "original_id": fvo.get("original_id"),
            "namn": namn,
            "tillagda": [],
            "scraped": [],
            "notes": ["skip_no_need"],
            "candidates": [],
            "tipslankar": tip_links,
        }

    if not candidates:
        return {
            "original_id": fvo.get("original_id"),
            "namn": namn,
            "tillagda": [],
            "scraped": [],
            "notes": ["no_tip_links"],
            "candidates": [],
            "tipslankar": tip_links,
        }

    added: set[str] = set()
    scraped_all: list[str] = []
    notes: list[str] = []
    for url in candidates[:10]:
        spp, scraped, n = scrape_site(session, url)
        scraped_all.extend(scraped)
        notes.extend(n)
        for s in spp:
            if s not in our:
                added.add(s)

    return {
        "original_id": fvo.get("original_id"),
        "namn": namn,
        "lan": fvo.get("ansvarigt_lan"),
        "tillagda": sorted(added, key=lambda s: s.casefold()),
        "traffade_luckor": sorted(added & gaps, key=lambda s: s.casefold()) if gaps else [],
        "scraped": list(dict.fromkeys(scraped_all)),
        "notes": notes,
        "candidates": candidates[:10],
        "tipslankar": tip_links,
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]

    # Fokus: spegelluckor + tomma artlistor (iFiske tips → FVOF)
    todo = [
        f
        for f in fvos
        if (
            (f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof")
            or not f.get("arter")
            or len(f.get("arter") or []) < 3
        )
        and (
            f.get("url_fvof")
            or f.get("url_ifiske_for_externa_lankar")
            or f.get("url_fiskekort")
            or (f.get("ifiske_spegel_kontroll") or {}).get("url")
            or f.get("ifiske_tipslankar")
        )
    ]
    todo = sorted(
        todo,
        key=lambda f: (
            0 if (f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof") else 1,
            0 if not f.get("arter") else 1,
            len(f.get("arter") or []),
            f.get("namn") or "",
        ),
    )
    print(f"Följer iFiske-tipslänkar → FVOF för {len(todo)} FVO…")

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html"})

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
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
                        "scraped": [],
                        "notes": [f"exception:{type(e).__name__}"],
                        "tipslankar": [],
                    }
                )
            if done % 40 == 0:
                print(f"  {done}/{len(todo)}")
            time.sleep(0.02)

    by_id = {r["original_id"]: r for r in results}
    enriched = 0
    added_total = 0
    gaps_closed = 0
    tips_saved = 0
    for fvo in fvos:
        r = by_id.get(fvo["original_id"])
        if not r:
            continue
        tips = list(dict.fromkeys((fvo.get("ifiske_tipslankar") or []) + (r.get("tipslankar") or [])))
        if tips:
            fvo["ifiske_tipslankar"] = tips
            tips_saved += 1

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

        # update mirror gap list if present
        ctrl = fvo.get("ifiske_spegel_kontroll")
        if ctrl and ctrl.get("spegel_arter"):
            still = sorted(
                set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold()
            )
            ctrl["luckor_ej_funna_hos_fvof"] = still

    # coverage
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
    catalog: set[str] = set()
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

    # remirror comparison stats
    spegel_stats = {"battre": 0, "lika": 0, "samre": 0, "med_spegel": 0, "kvar_luckor": 0}
    for f in fvos:
        ctrl = f.get("ifiske_spegel_kontroll") or {}
        mirror = set(ctrl.get("spegel_arter") or [])
        if not mirror:
            continue
        spegel_stats["med_spegel"] += 1
        our = set(f.get("arter") or [])
        if our >= mirror and len(our) > len(mirror):
            spegel_stats["battre"] += 1
        elif our >= mirror:
            spegel_stats["lika"] += 1
        else:
            spegel_stats["samre"] += 1
            spegel_stats["kvar_luckor"] += len(mirror - our)

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["fvof_link_pass"] = {
        "enriched_fvo": enriched,
        "added_species_entries": added_total,
        "mirror_gaps_closed": gaps_closed,
        "tipslankar_sparade_fvo": tips_saved,
        "spegel_stats": spegel_stats,
    }
    data["meta"]["policy"] = {
        "ifiske_artdata": False,
        "ifiske_anvandning": (
            "Jämförelse/spegel + tips till FVOF-länkar (t.ex. \"Gå till hemsida\"). "
            "Artdata hämtas från FVOF/Fiskekartan/SLU/GBIF – aldrig från iFiske."
        ),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "fvof_link_pass_log.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # refresh gap csv
    import csv

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
                "enriched_fvo": enriched,
                "added": added_total,
                "gaps_closed": gaps_closed,
                "tipslankar_sparade_fvo": tips_saved,
                "remaining_gap_fvo": len(gaps),
                "spegel_stats": spegel_stats,
                "coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
