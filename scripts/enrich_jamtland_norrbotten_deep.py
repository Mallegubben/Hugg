#!/usr/bin/env python3
"""
Djupberikning Jämtland + Norrbotten från lokala källor.

Tillåtet: FiskaiBerg, Drömfiske, FVOF-sajter, Ottsjö, Gäddede/Frostviken-turism,
Glommersbygden, Sammakko, Gällivare FVO (gfvo.se), m.fl.

Förbjudet: NatureIT, Artportalen, iFiske som artdata.
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
UA = "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; jamtland-norrbotten-deep)"

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
    "havsöring": "Havsöring",
    "signalkräfta": "Signalkräfta",
    "flodkräfta": "Flodkräfta",
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
    "vattenagarna.se",  # generisk portal, ofta utan artlista
)

# Kuraterade högtrohetskällor (namn → arter + url + source)
CURATED = [
    {
        "namn": "Ottsjö fvof",
        "lan": "Jämtland",
        "source": "ottsjo_se",
        "url": "https://www.ottsjo.se/aktiviteter/fiske/",
        "arter": ["Röding", "Öring", "Harr", "Lake"],
        "note": "Ottsjö turism: Ottsjön/Offsjön/Storån",
    },
    {
        "namn": "Gäddede fvof",
        "lan": "Jämtland",
        "source": "gaddede_camping_frostviken",
        "url": "https://www.gaddedecamping.com/aktiviteter/fiske/?lang=sv",
        "arter": ["Gädda", "Öring", "Röding", "Harr", "Sik", "Lake"],
        "note": "Frostviken/Gäddede – stäm av mot Strömsunds fiskebroschyr (ej regional övertolkning)",
    },
    # Frostviken: använd endast broschyrens vattenlistor via enrich_stromsund_norrbotten_careful.py
    # (ej generisk regional artpalett – den orsakade falska Abborre/Gädda på öring/röding-FVO).
    {
        "namn": "Södra Sandträsk fvof",
        "lan": "Norrbotten",
        "source": "glommersbygden",
        "url": "https://glommersbygden.se/fiske/",
        "arter": ["Abborre", "Gädda"],
        "note": "Endast sektionen Södra Sandträsk (gädda, abborre)",
    },
    {
        "namn": "Sammakko-Lillbergets fvof",
        "lan": "Norrbotten",
        "source": "sammakko_info",
        "url": "https://sammakko.info/fiskkart.html",
        "arter": ["Abborre", "Gädda", "Öring", "Harr", "Sik", "Regnbåge", "Lax"],
    },
    {
        "namn": "Gällivare fvof",
        "lan": "Norrbotten",
        "source": "gfvo_se",
        "url": "https://gfvo.se/fiskeomraden/",
        "arter": ["Abborre", "Gädda", "Öring", "Röding", "Harr", "Sik", "Lax", "Regnbåge", "Ruda"],
    },
]


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(
        r"\b(fvof|fvo|kfo|kfof|fiskevardsomrade|forening|sameby)\b",
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
        elif near_o or near_k or len(found) <= 18:
            acc.add(c)
    return sorted(acc, key=lambda s: s.casefold())


def fetch(session: requests.Session, url: str) -> tuple[str | None, str]:
    try:
        if not url.startswith("http"):
            url = "https://" + url.lstrip("/")
        r = session.get(url, timeout=25, allow_redirects=True)
        if r.status_code >= 400:
            return None, r.url
        host = urlparse(r.url).netloc.lower()
        if any(b in host for b in BLOCK_HOSTS):
            return None, r.url
        r.encoding = r.apparent_encoding or r.encoding
        return r.text, r.url
    except requests.RequestException:
        return None, url


def normalize_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    if not u or u.lower() in {"none", "null", "-"}:
        return None
    if not u.startswith("http"):
        u = "https://" + u
    host = urlparse(u).netloc.lower()
    if any(b in host for b in BLOCK_HOSTS):
        return None
    if "facebook." in host:
        return None
    return u


def match_fvo(namn: str, fvos: list[dict], lan: str | None = None) -> dict | None:
    target = fold(namn)
    cands = fvos
    if lan:
        cands = [f for f in fvos if f.get("ansvarigt_lan") == lan] or fvos
    exact = [f for f in cands if fold(f.get("namn") or "") == target]
    if len(exact) == 1:
        return exact[0]
    partial = []
    for f in cands:
        fn = fold(f.get("namn") or "")
        if not fn:
            continue
        if target in fn or fn in target:
            shorter, longer = sorted([target, fn], key=len)
            if len(shorter) >= 5 and len(shorter) / len(longer) >= 0.72:
                partial.append((abs(len(fn) - len(target)), f))
    partial.sort(key=lambda x: x[0])
    if partial and (len(partial) == 1 or partial[0][0] < partial[1][0]):
        return partial[0][1]
    return None


def apply_species(fvo: dict, spp: list[str], source: str, url: str | None) -> dict | None:
    before = set(fvo.get("arter") or [])
    found = set(spp)
    extra = found - before
    if url:
        tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [url]))
        fvo["lokala_tipslankar"] = tips
        if not fvo.get("url_fvof") and url and "dromfiske" not in url and "fiskaiberg" not in url:
            if "camping" not in url and "glommers" not in url:
                # only set fvof for real association sites
                if any(k in url for k in (".se", ".nu", ".info", ".com")) and "ifiske" not in url:
                    pass  # keep existing policy: set for non-portal
            if not fvo.get("url_fvof") and re.search(r"fvof|fvo|fiske", url, re.I):
                if "camping" not in url and "glommers" not in url and "gaddede" not in url:
                    fvo["url_fvof"] = url
    if not extra:
        ctrl = fvo.get("ifiske_spegel_kontroll")
        if ctrl and ctrl.get("spegel_arter"):
            ctrl["luckor_ej_funna_hos_fvof"] = sorted(
                set(ctrl["spegel_arter"]) - (before | found), key=lambda s: s.casefold()
            )
        return None
    after = sorted(before | found, key=lambda s: s.casefold())
    fvo["arter"] = after
    fvo.setdefault("kallor", [])
    if source not in fvo["kallor"]:
        fvo["kallor"].append(source)
    prev = fvo.get("extern_enrichment") or {}
    fvo["extern_enrichment"] = {
        "tillagda_arter": sorted(
            set(prev.get("tillagda_arter") or []) | extra, key=lambda s: s.casefold()
        ),
        "kallor_url:er": list(
            dict.fromkeys((prev.get("kallor_url:er") or []) + ([url] if url else []))
        ),
    }
    if isinstance(fvo.get("statistik"), dict):
        fvo["statistik"]["antal_arter"] = len(after)
    closed = 0
    ctrl = fvo.get("ifiske_spegel_kontroll")
    if ctrl and ctrl.get("spegel_arter"):
        prev_gaps = set(ctrl.get("luckor_ej_funna_hos_fvof") or [])
        still = sorted(set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold())
        closed = len(prev_gaps - set(still))
        ctrl["luckor_ej_funna_hos_fvof"] = still
    return {
        "fvo": fvo["namn"],
        "lan": fvo.get("ansvarigt_lan"),
        "source": source,
        "url": url,
        "tillagda": sorted(extra, key=lambda s: s.casefold()),
        "gaps_closed": closed,
    }


def scrape_fiskaiberg(session: requests.Session) -> list[dict]:
    index = "https://www.fiskaiberg.se/fiska/fiskekort-fvo/"
    html, final = fetch(session, index)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    pages = []
    for a in soup.find_all("a", href=True):
        href = urljoin(final, a["href"])
        if "/fiska/fiskekort-fvo/" in href and href.rstrip("/") != index.rstrip("/"):
            namn = a.get_text(" ", strip=True)
            if namn and "återförsälj" not in namn.casefold():
                pages.append((namn, href))
    out, seen = [], set()
    for namn, url in pages:
        if url in seen:
            continue
        seen.add(url)
        html2, _ = fetch(session, url)
        if not html2:
            continue
        spp = extract_species(BeautifulSoup(html2, "lxml").get_text("\n", strip=True))
        out.append({"namn": namn, "url": url, "arter": spp, "source": "fiskaiberg"})
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
    out = []
    for url in urls:
        html2, final2 = fetch(session, url)
        if not html2:
            continue
        soup2 = BeautifulSoup(html2, "lxml")
        spp = extract_species(soup2.get_text("\n", strip=True))
        h1 = soup2.find("h1")
        namn = h1.get_text(" ", strip=True) if h1 else url.rstrip("/").split("/")[-1]
        out.append({"namn": namn, "url": final2, "arter": spp, "source": "dromfiske"})
        # outbound FVOF
        for a in soup2.find_all("a", href=True):
            href = urljoin(final2, a["href"])
            host = urlparse(href).netloc.lower()
            if not href.startswith("http") or any(
                b in host for b in BLOCK_HOSTS + ("dromfiske.com",)
            ):
                continue
            if not any(k in host + href for k in ("fvo", "fiske", "hotagen", "langa", "nedre")):
                continue
            html3, final3 = fetch(session, href)
            if not html3:
                continue
            spp3 = extract_species(BeautifulSoup(html3, "lxml").get_text("\n", strip=True))
            if spp3:
                tip = host.removeprefix("www.").split(".")[0]
                slug = urlparse(final3).path.rstrip("/").split("/")[-1]
                out.append(
                    {
                        "namn": slug or tip,
                        "url": final3,
                        "arter": spp3,
                        "source": "fvof_via_dromfiske",
                    }
                )
            time.sleep(0.05)
        time.sleep(0.05)
    return out


def scrape_url_fvof_for_lan(session: requests.Session, fvos: list[dict], lan: str) -> list[dict]:
    out = []
    for f in fvos:
        if f.get("ansvarigt_lan") != lan:
            continue
        url = normalize_url(f.get("url_fvof"))
        if not url:
            continue
        html, final = fetch(session, url)
        if not html:
            continue
        spp = extract_species(BeautifulSoup(html, "lxml").get_text("\n", strip=True))
        if spp:
            out.append(
                {
                    "namn": f["namn"],
                    "url": final,
                    "arter": spp,
                    "source": "fvof_site",
                    "fvo_id": f["original_id"],
                }
            )
        time.sleep(0.06)
    return out


def scrape_glommers_extra(session: requests.Session) -> list[dict]:
    """Fler sektioner i Glommersbygden som kan matcha Norrbotten-FVO."""
    url = "https://glommersbygden.se/fiske/"
    html, final = fetch(session, url)
    if not html:
        return []
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    out = []
    # Södra Sandträsk
    m = re.search(r"Södra Sandträsk.{0,250}", text, re.I | re.S)
    if m:
        spp = extract_species(m.group(0))
        if spp:
            out.append(
                {
                    "namn": "Södra Sandträsk fvof",
                    "url": final,
                    "arter": spp,
                    "source": "glommersbygden",
                }
            )
    return out


def lan_stats(fvos: list[dict], lan: str) -> dict:
    rows = [f for f in fvos if f.get("ansvarigt_lan") == lan]
    empty = sum(1 for f in rows if not f.get("arter"))
    thin = sum(1 for f in rows if 0 < len(f.get("arter") or []) <= 3)
    gaps = 0
    gap_count = 0
    for f in rows:
        luckor = (f.get("ifiske_spegel_kontroll") or {}).get("luckor_ej_funna_hos_fvof") or []
        if luckor:
            gaps += 1
            gap_count += len(luckor)
    return {
        "antal": len(rows),
        "tomma": empty,
        "tunna_le3": thin,
        "med_spegelluckor": gaps,
        "kvar_luckor": gap_count,
    }


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    before_j = lan_stats(fvos, "Jämtland")
    before_n = lan_stats(fvos, "Norrbotten")

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "text/html"})

    applied = []
    records: list[dict] = []

    print("1) Kuraterade källor…")
    for rec in CURATED:
        records.append(rec)

    print("2) FiskaiBerg…")
    berg = scrape_fiskaiberg(session)
    print(f"   {len(berg)} sidor")
    records.extend(berg)

    print("3) Drömfiske + outbound…")
    drom = scrape_dromfiske(session)
    print(f"   {len(drom)} poster")
    records.extend(drom)

    print("4) Befintliga url_fvof Jämtland…")
    j_sites = scrape_url_fvof_for_lan(session, fvos, "Jämtland")
    print(f"   {len(j_sites)} med arter")
    records.extend(j_sites)

    print("5) Befintliga url_fvof Norrbotten…")
    n_sites = scrape_url_fvof_for_lan(session, fvos, "Norrbotten")
    print(f"   {len(n_sites)} med arter")
    records.extend(n_sites)

    print("6) Glommers extra…")
    records.extend(scrape_glommers_extra(session))

    # Hotagen-sidor
    for url, namn in [
        ("https://hotagen.com/a-gunnarvattnets-fvo/", "Gunnarvattnets fvof"),
        ("https://hotagen.com/b-valsjons-fvof/", "Valsjöns fvof"),
        ("https://hotagen.com/c-rorvattnet-skogsjo-fvo/", "Rörvattnet-Skogsjö fvof"),
    ]:
        html, final = fetch(session, url)
        if html:
            spp = extract_species(BeautifulSoup(html, "lxml").get_text("\n", strip=True))
            records.append(
                {"namn": namn, "url": final, "arter": spp, "source": "hotagen_com", "lan": "Jämtland"}
            )

    # Ottsjö scrape (utöver curated, för tips)
    html, final = fetch(session, "https://www.ottsjo.se/aktiviteter/fiske/")
    if html:
        spp = extract_species(BeautifulSoup(html, "lxml").get_text("\n", strip=True))
        records.append(
            {"namn": "Ottsjö fvof", "url": final, "arter": spp, "source": "ottsjo_se", "lan": "Jämtland"}
        )

    # Apply
    for rec in records:
        lan = rec.get("lan")
        if rec.get("fvo_id"):
            fvo = next((f for f in fvos if f.get("original_id") == rec["fvo_id"]), None)
        else:
            fvo = match_fvo(rec["namn"], fvos, lan=lan)
            if not fvo:
                fvo = match_fvo(rec["namn"], fvos)
        if not fvo:
            continue
        # begränsa Frostviken-regional till Jämtland
        if rec.get("source") == "frostviken_regional" and fvo.get("ansvarigt_lan") != "Jämtland":
            continue
        res = apply_species(fvo, rec.get("arter") or [], rec.get("source") or "local", rec.get("url"))
        if res:
            applied.append(res)

    # meta refresh (partial)
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

    after_j = lan_stats(fvos, "Jämtland")
    after_n = lan_stats(fvos, "Norrbotten")
    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["jamtland_norrbotten_deep_pass"] = {
        "applied": len(applied),
        "added_species_entries": sum(len(a["tillagda"]) for a in applied),
        "mirror_gaps_closed": sum(a["gaps_closed"] for a in applied),
        "jamtland_before": before_j,
        "jamtland_after": after_j,
        "norrbotten_before": before_n,
        "norrbotten_after": after_n,
        "blocked_sources": ["natureit", "artportalen", "ifiske_artdata"],
    }
    data["meta"].setdefault("policy", {})
    data["meta"]["policy"].update(
        {
            "ifiske_artdata": False,
            "natureit": False,
            "artportalen": False,
            "beskrivning": (
                "iFiske endast spegel/länktips. NatureIT och Artportalen används inte. "
                "Artdata från Fiskekartan, SLU, GBIF, FVOF, FiskaiBerg, Drömfiske, "
                "Ottsjö, Frostviken/Gäddede, Sammakko, Glommersbygden, gfvo.se."
            ),
        }
    )
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "jamtland_norrbotten_deep_pass_log.json").write_text(
        json.dumps(
            {
                "applied": applied,
                "stats": data["meta"]["jamtland_norrbotten_deep_pass"],
                "records_count": len(records),
            },
            ensure_ascii=False,
            indent=2,
        ),
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
                "applied_count": len(applied),
                "added": sum(len(a["tillagda"]) for a in applied),
                "gaps_closed": sum(a["gaps_closed"] for a in applied),
                "jamtland": {"before": before_j, "after": after_j},
                "norrbotten": {"before": before_n, "after": after_n},
                "applied_sample": applied[:25],
                "remaining_empty_norr": [
                    f["namn"]
                    for f in fvos
                    if f.get("ansvarigt_lan") == "Norrbotten" and not f.get("arter")
                ],
                "remaining_thin_jamt": [
                    f["namn"]
                    for f in fvos
                    if f.get("ansvarigt_lan") == "Jämtland" and 0 < len(f.get("arter") or []) <= 3
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
