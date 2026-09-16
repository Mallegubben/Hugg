#!/usr/bin/env python3
"""
Berika Jämtland/Härjedalen (och matchande FVO) från lokala källor:
- FiskaiBerg (Bergs kommun / fiskaiberg.se)
- Drömfiske Jämtland Härjedalen (dromfiske.com)
- Utgående FVOF-sajter från Drömfiske

Plus riktad Norrbotten-pass: Sammakko-Lillberget (sammakko.info).

Policy:
- NatureIT och Artportalen används INTE.
- iFiske används INTE som artdata (ingen ikonläsning).
"""

from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ARTLISTA = OUT / "fvo_artlista.json"
UA = "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; local-jamtland-norrbotten)"

SPECIES = {
    "abborre": "Abborre",
    "gädda": "Gädda",
    "öring": "Öring",
    "röding": "Röding",
    "harr": "Harr",
    "sik": "Sik",
    "lake": "Lake",
    "gös": "Gös",
    "regnbåge": "Regnbåge",
    "mört": "Mört",
    "bäckröding": "Bäckröding",
    "kanadaröding": "Kanadaröding",
    "kanadaroding": "Kanadaröding",
    "nors": "Nors",
    "id": "Id",
    "braxen": "Braxen",
    "sutare": "Sutare",
    "sarv": "Sarv",
    "ruda": "Ruda",
    "ål": "Ål",
    "lax": "Lax",
    "laxöring": "Öring",
    "siklöja": "Siklöja",
    "elritsa": "Elritsa",
    "benlöja": "Benlöja",
    "asp": "Asp",
    "färna": "Färna",
    "karp": "Karp",
    "signalkräfta": "Signalkräfta",
    "flodkräfta": "Flodkräfta",
    "havsöring": "Havsöring",
    "björkna": "Björkna",
    "gärs": "Gärs",
}
AMBIG = {"Id", "Asp", "Mal", "Sik", "Lax", "Ål", "Nors", "Lake"}

BLOCK_HOSTS = (
    "ifiske.",
    "natureit.",
    "artportalen.",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "google.",
    "youtube.com",
    "dromfiske.com",  # redan scrapad; endast utgående
)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(
        r"\b(fvof|fvo|kfo|kfof|fiskevardsomrade|fiskevardsomradesforening|"
        r"forening|sameby|statens vatten|kortfiskeomrade)\b",
        "",
        s,
        flags=re.I,
    )
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def extract_species(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for raw, can in sorted(SPECIES.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(
            rf"(?<![A-Za-zÅÄÖåäö]){re.escape(raw)}(?![A-Za-zÅÄÖåäö])", text or "", re.I
        ):
            found.append((m.start(), can))
    if not found:
        return []
    kw = [
        m.start()
        for m in re.finditer(
            r"fiskart|förekom|artlista|vanliga|bestånd|fiske|öring|röding|harr|sik",
            text or "",
            re.I,
        )
    ]
    acc: set[str] = set()
    for i, (pos, c) in enumerate(found):
        near_o = any(abs(pos - p2) <= 120 and c != c2 for j, (p2, c2) in enumerate(found) if i != j)
        near_k = any(abs(pos - k) <= 200 for k in kw)
        if c in AMBIG:
            if near_o or near_k:
                acc.add(c)
        elif near_o or near_k or len(found) <= 15:
            acc.add(c)
    return sorted(acc, key=lambda s: s.casefold())


ALIASES = {
    # FiskaiBerg / Drömfiske → Fiskekartan-namn (foldade)
    "asarna": "asarna",
    "asarn": "asarna",
    "asarnas": "asarna",
    "dammans": "dammans",
    "handsjons": "handsjons",
    "hovermoanhogans": "hovermoanhogans",
    "hoganhovermoans": "hovermoanhogans",
    "ratans": "ratans",
    "skalans": "skalans",
    "storsjonbergs": "storsjonbergs",
    "svenstaans": "svenstaans",
    "ovrehoans": "ovrehoans",
    "hackrens": "hackrens",
    "hackren": "hackrens",
    "hanvemdalens": "hanvemdalens",
    "hanvemdalen": "hanvemdalens",
    "tannasfunasdalens": "tannasfunasdalens",
    "tannasfunasdalen": "tannasfunasdalens",
    "ockesjonkvitslestrommarnas": "ockesjonkvisslestrommarnas",
    "ockesjonkvitslestrommarna": "ockesjonkvisslestrommarnas",
    "langa": "langa",
    "langafisket": "langa",
    "lillhardals": "lillhardals",
    "ljungdalens": "ljungdalens",
    "messlingens": "messlingens",
    "nedreammerans": "nedreammerans",
    "nedrelangans": "nedrelangans",
    "ovreammerans": "ovreammerans",
    "rorstromsalvens": "rorstromsalvens",
    "rorvattnetskogsjo": "rorvattnetskogsjo",
    "storsjo": "storsjo",
    "valsjons": "valsjons",
    "valsjon": "valsjons",
    "akersjons": "akersjons",
    "akersjon": "akersjons",
    "bodsjons": "bodsjons",
    "granboforsens": "granboforsens",
    "gunnarvattnets": "gunnarvattnets",
    "jormvattnets": "jormvattnets",
    "bortnans": "bortnans",
    "sammakko": "sammakkolillbergets",
    "sammakkolillberget": "sammakkolillbergets",
    "sammakkolillbergets": "sammakkolillbergets",
    "gunnarvattnets": "gunnarvattnets",
    "agunnarvattnetsfvo": "gunnarvattnets",
    "bvalsjonsfvof": "valsjons",
    "crorvattnetskogsjofvo": "rorvattnetskogsjo",
    "rorvattnetskogsjofvo": "rorvattnetskogsjo",
    "nedrelangans": "nedrelangans",
    "nedrelangan": "nedrelangans",
    "langafisket": "langa",
    "hackrenfiske": "hackrens",
    "hackrenlagret": "hackrens",
    "tannasfiskecentrum": "tannasfunasdalens",
    "tannasfunasdalens": "tannasfunasdalens",
    # KFO utan egen Fiskekartan-post – mappa till närmaste registrerade FVOF där överlapp är tydligt
    "gruckarnas": "flasjons",
    "gruckarnaskfo": "flasjons",
    "gruckarna": "flasjons",
}

# URL-host/path → foldat FVO-namn (för utgående sajter med dåliga H1)
URL_HINTS = {
    "nedrelangan.com": "nedrelangans",
    "langafisket.se": "langa",
    "langafisket.se/": "langa",
    "hackrenfiske.se": "hackrens",
    "hotagen.com/a-gunnarvattnets-fvo": "gunnarvattnets",
    "hotagen.com/b-valsjons-fvof": "valsjons",
    "hotagen.com/c-rorvattnet-skogsjo-fvo": "rorvattnetskogsjo",
    "sammakko.info": "sammakkolillbergets",
}


def match_fvo(
    namn: str,
    fvos: list[dict],
    lan_hint: str | None = None,
    url: str | None = None,
) -> dict | None:
    # URL-hint först (mest tillförlitligt för utgående FVOF-sajter)
    if url:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        path = urlparse(url).path.lower().rstrip("/")
        for key, folded in URL_HINTS.items():
            if key in f"{host}{path}" or key == host:
                exact = [f for f in fvos if fold(f.get("namn") or "") == folded]
                if lan_hint:
                    lf = fold(lan_hint)
                    exact = [f for f in exact if fold(f.get("ansvarigt_lan") or "") == lf] or exact
                if len(exact) == 1:
                    return exact[0]
        # slug från path, t.ex. /a-gunnarvattnets-fvo/
        slug = path.split("/")[-1] if path else ""
        slug_fold = fold(re.sub(r"^[a-z]-", "", slug))
        if len(slug_fold) >= 5:
            t2 = ALIASES.get(slug_fold, slug_fold)
            cands = fvos
            if lan_hint:
                lf = fold(lan_hint)
                narrowed = [f for f in fvos if fold(f.get("ansvarigt_lan") or "") == lf]
                if narrowed:
                    cands = narrowed
            exact = [f for f in cands if fold(f.get("namn") or "") == t2]
            if len(exact) == 1:
                return exact[0]

    target = fold(namn)
    if len(target) < 4:
        return None
    candidates = fvos
    if lan_hint:
        lf = fold(lan_hint)
        narrowed = [f for f in fvos if fold(f.get("ansvarigt_lan") or "") == lf]
        if narrowed:
            candidates = narrowed
    exact = [f for f in candidates if fold(f.get("namn") or "") == target]
    if len(exact) == 1:
        return exact[0]
    t2 = ALIASES.get(target, target)
    exact = [f for f in candidates if fold(f.get("namn") or "") == t2]
    if len(exact) == 1:
        return exact[0]
    partial: list[tuple[int, dict]] = []
    for f in candidates:
        fn = fold(f.get("namn") or "")
        if not fn:
            continue
        if t2 in fn or fn in t2:
            shorter, longer = sorted([t2, fn], key=len)
            if len(shorter) >= 5 and len(shorter) / len(longer) >= 0.7:
                partial.append((abs(len(fn) - len(t2)), f))
    partial.sort(key=lambda x: x[0])
    if partial and (len(partial) == 1 or partial[0][0] < partial[1][0]):
        return partial[0][1]
    return None


def fetch(session: requests.Session, url: str) -> tuple[str | None, str]:
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.url
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, r.url
    except requests.RequestException:
        return None, url


def scrape_fiskaiberg(session: requests.Session) -> list[dict]:
    index = "https://www.fiskaiberg.se/fiska/fiskekort-fvo/"
    html, final = fetch(session, index)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    pages: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(final, a["href"])
        if "/fiska/fiskekort-fvo/" in href and href.rstrip("/") != index.rstrip("/"):
            namn = a.get_text(" ", strip=True)
            if namn and "återförsälj" not in namn.casefold():
                pages.append((namn, href))
    seen: set[str] = set()
    out: list[dict] = []
    for namn, url in pages:
        if url in seen:
            continue
        seen.add(url)
        html2, _ = fetch(session, url)
        if not html2:
            continue
        spp = extract_species(BeautifulSoup(html2, "lxml").get_text("\n", strip=True))
        out.append({"source": "fiskaiberg", "namn": namn, "url": url, "arter": spp})
        time.sleep(0.05)
    return out


def scrape_dromfiske(session: requests.Session) -> list[dict]:
    index = "https://dromfiske.com/fiskevardsomraden/"
    html, final = fetch(session, index)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    urls = sorted(
        {
            urljoin(final, a["href"])
            for a in soup.find_all("a", href=True)
            if re.search(r"dromfiske\.com/[^/]+-(fvo|kfo)/?$", urljoin(final, a["href"]))
        }
    )
    out: list[dict] = []
    for url in urls:
        html2, final2 = fetch(session, url)
        if not html2:
            continue
        soup2 = BeautifulSoup(html2, "lxml")
        text = soup2.get_text("\n", strip=True)
        spp = extract_species(text)
        outbound: list[str] = []
        for a in soup2.find_all("a", href=True):
            href = urljoin(final2, a["href"])
            host = urlparse(href).netloc.lower()
            if not href.startswith("http"):
                continue
            if any(b in host for b in BLOCK_HOSTS):
                continue
            outbound.append(href)
        outbound = list(dict.fromkeys(outbound))[:12]
        h1 = soup2.find("h1")
        title = soup2.title.get_text(strip=True) if soup2.title else url.rstrip("/").split("/")[-1]
        namn = h1.get_text(" ", strip=True) if h1 else title.split("-")[0].strip()
        out.append(
            {
                "source": "dromfiske",
                "namn": namn or url.rstrip("/").split("/")[-1],
                "url": final2,
                "arter": spp,
                "outbound": outbound,
            }
        )
        time.sleep(0.05)
    return out


def scrape_outbound(session: requests.Session, urls: list[str]) -> list[dict]:
    out: list[dict] = []
    for url in urls:
        host = urlparse(url).netloc.lower()
        if any(b in host for b in BLOCK_HOSTS):
            continue
        path = urlparse(url).path.lower()
        blob = host + path
        if not any(
            k in blob
            for k in (
                "fvo",
                "fvof",
                "fiske",
                "langafisket",
                "nedrelangan",
                "hackren",
                "hotagen",
                "sammakko",
                "kvitsle",
                "ammeran",
            )
        ):
            continue
        html, final = fetch(session, url)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        spp = extract_species(soup.get_text("\n", strip=True))
        if not spp:
            continue
        tip_host = host.removeprefix("www.").split(".")[0]
        slug = urlparse(final).path.rstrip("/").split("/")[-1]
        namn = tip_host
        final_blob = urlparse(final).netloc.lower().removeprefix("www.") + urlparse(final).path.lower()
        for key, folded in URL_HINTS.items():
            if key in final_blob or key == urlparse(final).netloc.lower().removeprefix("www."):
                namn = folded
                break
        else:
            if slug and len(fold(slug)) >= 5:
                namn = re.sub(r"^[a-z]-", "", slug)
            else:
                h1 = soup.find("h1")
                title = soup.title.get_text(strip=True) if soup.title else tip_host
                namn = h1.get_text(" ", strip=True) if h1 else title.split("|")[0].split("-")[0].strip()
        out.append(
            {
                "source": "fvof_via_dromfiske",
                "namn": namn or tip_host,
                "url": final,
                "arter": spp,
            }
        )
        time.sleep(0.08)
    return out


def scrape_sammakko(session: requests.Session) -> list[dict]:
    urls = [
        "https://www.sammakko.info/",
        "https://www.sammakko.info/index.html",
        "https://sammakko.info/fiskkart.html",
        "https://www.sammakko.info/fiskef2.html",
    ]
    spp: set[str] = set()
    used: list[str] = []
    for url in urls:
        html, final = fetch(session, url)
        if not html:
            continue
        found = extract_species(BeautifulSoup(html, "lxml").get_text("\n", strip=True))
        if found:
            spp.update(found)
            used.append(final)
        time.sleep(0.05)
    if not spp:
        return []
    return [
        {
            "source": "sammakko_info",
            "namn": "Sammakko-Lillbergets fvof",
            "url": used[0] if used else "https://www.sammakko.info/",
            "arter": sorted(spp, key=lambda s: s.casefold()),
            "extra_urls": used,
        }
    ]


def scrape_glommersbygden(session: requests.Session) -> list[dict]:
    """Arvidsjaur/Glommersträsk – sektioner per vatten, t.ex. Södra Sandträsk."""
    url = "https://glommersbygden.se/fiske/"
    html, final = fetch(session, url)
    if not html:
        return []
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    out: list[dict] = []
    # Klipp ut sektionen för Södra Sandträsk
    m = re.search(
        r"(Södra Sandträsk|S\.?\s*Sandträsk)(.{0,800}?)(?=\n[A-ZÅÄÖ][^\n]{0,40}\n|Petikån|Lappträskån|Byskeälven|$)",
        text,
        flags=re.I | re.S,
    )
    if m:
        spp = extract_species(m.group(0))
        if spp:
            out.append(
                {
                    "source": "glommersbygden",
                    "namn": "Södra Sandträsk fvof",
                    "url": final,
                    "arter": spp,
                }
            )
    return out


def apply_records(fvos: list[dict], records: list[dict], lan_hint: str | None) -> dict:
    enriched = 0
    added_total = 0
    gaps_closed = 0
    tips_saved = 0
    unmatched: list[dict] = []
    applied: list[dict] = []

    for rec in records:
        url = rec.get("url")
        fvo = match_fvo(rec["namn"], fvos, lan_hint=lan_hint, url=url)
        if not fvo:
            fvo = match_fvo(rec["namn"], fvos, url=url)
        if not fvo:
            unmatched.append({"namn": rec["namn"], "source": rec.get("source"), "url": url})
            continue

        if url:
            tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [url]))
            for extra in rec.get("extra_urls") or []:
                if extra not in tips:
                    tips.append(extra)
            fvo["lokala_tipslankar"] = tips
            tips_saved += 1
            if (
                not fvo.get("url_fvof")
                and "dromfiske.com" not in url
                and "fiskaiberg" not in url
            ):
                fvo["url_fvof"] = url

        found = set(rec.get("arter") or [])
        if not found:
            continue
        before = set(fvo.get("arter") or [])
        extra = found - before
        if not extra:
            ctrl = fvo.get("ifiske_spegel_kontroll")
            if ctrl and ctrl.get("spegel_arter"):
                still = sorted(
                    set(ctrl["spegel_arter"]) - (before | found),
                    key=lambda s: s.casefold(),
                )
                ctrl["luckor_ej_funna_hos_fvof"] = still
            continue

        after = sorted(before | found, key=lambda s: s.casefold())
        fvo["arter"] = after
        fvo.setdefault("kallor", [])
        src = rec["source"]
        if src not in fvo["kallor"]:
            fvo["kallor"].append(src)
        prev = fvo.get("extern_enrichment") or {}
        urls = list(dict.fromkeys((prev.get("kallor_url:er") or []) + ([url] if url else [])))
        fvo["extern_enrichment"] = {
            "tillagda_arter": sorted(
                set(prev.get("tillagda_arter") or []) | extra, key=lambda s: s.casefold()
            ),
            "kallor_url:er": urls,
        }
        if isinstance(fvo.get("statistik"), dict):
            fvo["statistik"]["antal_arter"] = len(after)
        enriched += 1
        added_total += len(extra)
        closed_here = 0
        ctrl = fvo.get("ifiske_spegel_kontroll")
        if ctrl and ctrl.get("spegel_arter"):
            prev_gaps = set(ctrl.get("luckor_ej_funna_hos_fvof") or [])
            still = sorted(set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold())
            closed_here = len(prev_gaps - set(still))
            gaps_closed += closed_here
            ctrl["luckor_ej_funna_hos_fvof"] = still
        applied.append(
            {
                "fvo": fvo["namn"],
                "source": src,
                "url": url,
                "tillagda": sorted(extra, key=lambda s: s.casefold()),
                "gaps_closed": closed_here,
            }
        )

    return {
        "enriched_fvo": enriched,
        "added_species_entries": added_total,
        "mirror_gaps_closed": gaps_closed,
        "tips_saved": tips_saved,
        "applied": applied,
        "unmatched": unmatched,
    }


def refresh_meta(data: dict) -> None:
    fvos = data["fvo"]
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
    jamt = {"battre": 0, "lika": 0, "samre": 0, "med_spegel": 0, "kvar_luckor": 0}
    norr = {"battre": 0, "lika": 0, "samre": 0, "med_spegel": 0, "kvar_luckor": 0, "tomma": 0}

    for f in fvos:
        catalog.update(f.get("arter") or [])
        fk = f.get("fiskekartan") or {}
        if fk.get("arter") or fk.get("vanliga_arter") or fk.get("ovriga_arter"):
            coverage["har_fiskekartan_arter"] += 1
        if "slu_provfiske" in (f.get("kallor") or []) or any(
            k.startswith("slu") or k in {"nors", "sers", "kul"} for k in (f.get("kallor") or [])
        ):
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
        our = set(f.get("arter") or [])
        lan = f.get("ansvarigt_lan") or ""

        def bump(bucket: dict) -> None:
            if not mirror:
                return
            bucket["med_spegel"] += 1
            if our >= mirror and len(our) > len(mirror):
                bucket["battre"] += 1
            elif our >= mirror:
                bucket["lika"] += 1
            else:
                bucket["samre"] += 1
                bucket["kvar_luckor"] += len(mirror - our)

        bump(spegel)
        if lan == "Jämtland":
            bump(jamt)
        if lan == "Norrbotten":
            bump(norr)
            if not our:
                norr["tomma"] += 1

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["policy"] = {
        "ifiske_artdata": False,
        "natureit": False,
        "artportalen": False,
        "beskrivning": (
            "iFiske endast spegel/länktips. NatureIT och Artportalen används inte. "
            "Artdata från Fiskekartan, SLU, GBIF, FVOF-sajter, FiskaiBerg, Drömfiske JH, "
            "lokala föreningssidor (t.ex. Sammakko)."
        ),
    }
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    return {"spegel": spegel, "jamtland": jamt, "norrbotten": norr}


def write_gaps_csv(fvos: list[dict]) -> list[dict]:
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
    path = OUT / "luckor_mot_ifiske_spegel.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        if gaps:
            w = csv.DictWriter(fh, fieldnames=list(gaps[0].keys()))
            w.writeheader()
            w.writerows(gaps)
    return gaps


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html"})

    print("Scrapar FiskaiBerg…")
    berg = scrape_fiskaiberg(session)
    print(f"  {len(berg)} sidor")

    print("Scrapar Drömfiske…")
    drom = scrape_dromfiske(session)
    print(f"  {len(drom)} sidor")

    outbound_urls: list[str] = []
    for row in drom:
        outbound_urls.extend(row.get("outbound") or [])
    outbound_urls = list(dict.fromkeys(outbound_urls))
    print(f"Scrapar {len(outbound_urls)} FVOF-utlänkar…")
    outbound = scrape_outbound(session, outbound_urls)
    print(f"  träffar med arter: {len(outbound)}")

    print("Scrapar Sammakko (Norrbotten)…")
    sammakko = scrape_sammakko(session)
    print(f"  {len(sammakko)} poster")
    print("Scrapar Glommersbygden (Norrbotten)…")
    glommers = scrape_glommersbygden(session)
    print(f"  {len(glommers)} poster")

    jamt_stats = apply_records(fvos, berg + drom + outbound, lan_hint="Jämtland")
    norr_stats = apply_records(fvos, sammakko + glommers, lan_hint="Norrbotten")

    buckets = refresh_meta(data)
    data["meta"]["jamtland_local_pass"] = {
        "fiskaiberg_pages": len(berg),
        "dromfiske_pages": len(drom),
        "outbound_with_species": len(outbound),
        **{k: v for k, v in jamt_stats.items() if k not in {"applied", "unmatched"}},
        "unmatched_count": len(jamt_stats["unmatched"]),
        "spegel_stats": buckets["spegel"],
        "jamtland_spegel_stats": buckets["jamtland"],
        "blocked_sources": ["natureit", "artportalen", "ifiske_artdata"],
    }
    data["meta"]["norrbotten_local_pass"] = {
        **{k: v for k, v in norr_stats.items() if k not in {"applied", "unmatched"}},
        "unmatched_count": len(norr_stats["unmatched"]),
        "norrbotten_spegel_stats": buckets["norrbotten"],
        "blocked_sources": ["natureit", "artportalen", "ifiske_artdata"],
        "note": (
            "Tomma FVO utan egen hemsida (Hakkas, Bredträsket, Nurrholm, "
            "Södra Sandträsk, Kallön) saknar tillåtna publika artlistor; "
            "iFiske/NatureIT/Artportalen används inte."
        ),
    }

    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "jamtland_local_pass_log.json").write_text(
        json.dumps(
            {
                "berg": berg,
                "drom": drom,
                "outbound": outbound,
                "sammakko": sammakko,
                "glommers": glommers,
                "applied_jamtland": jamt_stats["applied"],
                "unmatched_jamtland": jamt_stats["unmatched"],
                "applied_norrbotten": norr_stats["applied"],
                "unmatched_norrbotten": norr_stats["unmatched"],
                "stats": {
                    "jamtland": data["meta"]["jamtland_local_pass"],
                    "norrbotten": data["meta"]["norrbotten_local_pass"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    gaps = write_gaps_csv(fvos)
    jamt_gaps = [g for g in gaps if g.get("lan") == "Jämtland"]
    norr_gaps = [g for g in gaps if g.get("lan") == "Norrbotten"]
    print(
        json.dumps(
            {
                "jamtland": {
                    "enriched_fvo": jamt_stats["enriched_fvo"],
                    "added": jamt_stats["added_species_entries"],
                    "gaps_closed": jamt_stats["mirror_gaps_closed"],
                    "unmatched": jamt_stats["unmatched"][:20],
                    "applied_sample": jamt_stats["applied"][:15],
                    "remaining_gap_fvo": len(jamt_gaps),
                    "spegel": buckets["jamtland"],
                },
                "norrbotten": {
                    "enriched_fvo": norr_stats["enriched_fvo"],
                    "added": norr_stats["added_species_entries"],
                    "gaps_closed": norr_stats["mirror_gaps_closed"],
                    "applied": norr_stats["applied"],
                    "remaining_gap_fvo": len(norr_gaps),
                    "spegel": buckets["norrbotten"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
