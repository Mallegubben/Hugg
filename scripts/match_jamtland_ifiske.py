#!/usr/bin/env python3
"""Matcha saknade Jämtland-FVO mot iFiske kommunsidor och spegla arter."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
ART = OUT / "fvo_artlista.json"
UA = "HuggFVOBot/1.0 (+https://github.com/Mallegubben/Hugg; jamtland-kommun-match)"

ALIASES = {
    "gadda": "Gädda",
    "mort": "Mört",
    "oring": "Öring",
    "gos": "Gös",
    "al": "Ål",
    "roding": "Röding",
    "backroding": "Bäckröding",
    "regnbage": "Regnbåge",
    "sikloja": "Siklöja",
    "benloja": "Benlöja",
    "bjorkna": "Björkna",
    "farna": "Färna",
    "gers": "Gärs",
    "stam": "Stäm",
    "kanadaroding": "Kanadaröding",
    "signalkrafta": "Signalkräfta",
    "flodkrafta": "Flodkräfta",
    "havsoring": "Havsöring",
    "abborre": "Abborre",
    "gädda": "Gädda",
    "mört": "Mört",
    "öring": "Öring",
    "gös": "Gös",
    "ål": "Ål",
    "röding": "Röding",
    "bäckröding": "Bäckröding",
    "regnbåge": "Regnbåge",
    "siklöja": "Siklöja",
    "benlöja": "Benlöja",
    "björkna": "Björkna",
    "färna": "Färna",
    "gärs": "Gärs",
    "stäm": "Stäm",
    "lake": "Lake",
    "braxen": "Braxen",
    "sutare": "Sutare",
    "harr": "Harr",
    "sik": "Sik",
    "lax": "Lax",
    "sarv": "Sarv",
    "id": "Id",
    "ruda": "Ruda",
    "nors": "Nors",
    "asp": "Asp",
    "karp": "Karp",
    "mal": "Mal",
    "elritsa": "Elritsa",
    "bergsimpa": "Bergsimpa",
    "stensimpa": "Stensimpa",
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(
        r"\b(fvof|fvo|fiskevardsomrade|fiskevårdsområde|forening|förening|bygdens|sjons|sjon|sjo)\b",
        " ",
        s,
    )
    return re.sub(r"[^a-z0-9]+", "", s)


def ascii_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def extract_species(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        if "/species/" not in src:
            continue
        alt = (img.get("alt") or "").strip()
        m = re.search(r"/species/(?:small/)?([a-z0-9_\-]+)\.", src)
        raw = alt or (m.group(1).replace("_", " ") if m else "")
        name = ALIASES.get(raw.casefold()) or ALIASES.get(ascii_key(raw))
        if name:
            found.add(name)
    return sorted(found, key=lambda s: s.casefold())


def main() -> None:
    data = json.loads(ART.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    kommun_pages = [
        "fiske-i-ostersund-kommun.htm",
        "fiske-i-are-kommun.htm",
        "fiske-i-stromsund-kommun.htm",
        "fiske-i-krokom-kommun.htm",
        "fiske-i-berg-kommun.htm",
        "fiske-i-bracke-kommun.htm",
        "fiske-i-ragunda-kommun.htm",
        "fiske-i-harjedalen-kommun.htm",
    ]

    catalog = []
    seen = set()
    for path in kommun_pages:
        url = "https://www.ifiske.se/" + path
        r = session.get(url, timeout=25, allow_redirects=True)
        if r.status_code != 200:
            print("skip", path, r.status_code)
            continue
        soup = BeautifulSoup(r.text, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"fiske-[a-z0-9\-]+\.htm", href or "", re.I):
                absu = urljoin(r.url, href)
                title = a.get_text(" ", strip=True)
                links.append((absu, title))
        print(path, "links", len(links))
        for absu, title in links:
            if absu in seen or "kommun.htm" in absu or "fisketeknik" in absu:
                continue
            seen.add(absu)
            rr = session.get(absu, timeout=25)
            if rr.status_code != 200:
                continue
            spp = extract_species(rr.text)
            ss = BeautifulSoup(rr.text, "lxml")
            h = ss.find(["h1", "h2"])
            htxt = (h.get_text(" ", strip=True) if h else "") or title
            catalog.append(
                {
                    "url": absu,
                    "title": htxt,
                    "fold": fold(htxt),
                    "title_fold": fold(title),
                    "species": spp,
                }
            )
            time.sleep(0.05)

    print("catalog", len(catalog), "with species", sum(1 for c in catalog if c["species"]))

    missing = [
        f
        for f in fvos
        if f.get("ansvarigt_lan") == "Jämtland" and not f.get("ifiske_spegling")
    ]
    matched = 0
    for f in missing:
        nf = fold(f["namn"])
        best = None
        best_score = 0
        for c in catalog:
            if not c["species"]:
                continue
            cf = c["fold"] or c["title_fold"]
            if not nf or not cf:
                continue
            if nf == cf:
                score = 100
            elif nf in cf or cf in nf:
                score = 85
            else:
                score = 0
                for i in range(max(0, len(nf) - 4)):
                    if nf[i : i + 5] in cf:
                        score += 2
                score = min(score, 70)
            if score > best_score:
                best_score = score
                best = c
        if best and best_score >= 85:
            spp = best["species"]
            f["arter_utokad"] = sorted(
                set(f.get("arter_utokad") or f.get("arter") or []),
                key=lambda s: s.casefold(),
            )
            f["arter"] = spp
            f["kallor"] = ["ifiske_spegling"] + [
                k for k in (f.get("kallor") or []) if k != "ifiske_spegling"
            ]
            f["ifiske_spegling"] = {
                "arter": spp,
                "url": best["url"],
                "match": best["title"],
                "score": best_score,
                "notes": ["jamtland_kommun_match"],
            }
            f["ifiske_kontroll"] = {
                "arter": spp,
                "parity_tillagda": [],
                "url": best["url"],
                "anmarkning": "Speglad via kommunsida iFiske",
            }
            f["statistik"]["antal_arter"] = len(spp)
            f["statistik"]["antal_arter_utokad"] = len(f["arter_utokad"])
            matched += 1
            print("MATCH", f["namn"], "->", best["title"], spp, best_score)
        else:
            print("NOMATCH", f["namn"], best["title"] if best else None, best_score)

    coverage = data["meta"].get("coverage") or {}
    coverage["har_ifiske_spegling"] = sum(1 for f in fvos if f.get("ifiske_spegling"))
    coverage["jamtland_har_spegling"] = sum(
        1 for f in fvos if f.get("ansvarigt_lan") == "Jämtland" and f.get("ifiske_spegling")
    )
    coverage["har_nagon_art"] = sum(1 for f in fvos if f.get("arter"))
    coverage["saknar_arter"] = sum(1 for f in fvos if not f.get("arter"))
    data["meta"]["coverage"] = coverage
    data["meta"]["jamtland_kommun_match"] = {
        "matched": matched,
        "missing_before": len(missing),
        "still_missing": len(missing) - matched,
    }
    ART.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "jamtland_ifiske_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "matched": matched,
                "missing_before": len(missing),
                "jamtland_speglade": coverage["jamtland_har_spegling"],
                "jamtland_totalt": 157,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
