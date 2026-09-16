#!/usr/bin/env python3
"""
Vattennivå-berikning (careful): lägg till arter per vatten inom FVO.

Källor:
- Strömsunds fiskebroschyr 2026 (parsad vatten×art, med junk-filter)
- Björna.nu / Örnsköldsvik naturguide (vattenvisa listor)
- Gällivare kommun fiskeguide (Hakkas, Sammakko)
- Glommersbygden (Södra Sandträsk, Sandselet–Gallejaur, Järvträsk)
- Hotagen.com (endast explicit namngivna vatten i prose)
- Drömfiske / FiskaiBerg / Ljungandalen / Lekeberg (kuraterade 1:1)

Policy: aldrig iFiske-ikoner, NatureIT eller Artportalen som artdata.
SLU-poster (NORS/SERS/KUL) behålls; arter unioneras vid namnmatch.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
ARTLISTA = OUT / "fvo_artlista.json"
STROMSUND_VATTEN = RAW / "stromsund_vatten_arter_parsed.json"

BROCHURE_URL = (
    "https://www.stromsund.se/download/18.540f27c719d93a15328128c2/"
    "1777021924241/Fiskebroschyr%202026%20(interaktiv).pdf"
)
GALLIVARE_URL = (
    "https://gallivare.se/uppleva-och-gora/idrott-motion-och-friluftsliv/"
    "friluftsliv-och-motion/fiske/fiskeguiden/har-kan-du-fiska/angesan---linaalven"
)

BROCHURE_TO_FVO = {
    "Jormvattnets fiskevårdsområde": ("Jormvattnets fvof", "Jämtland"),
    "Lilla Blåsjöns fiskevårdsområde": ("Lilla Blåsjöns fvof", "Jämtland"),
    "Kvarnbergsvattnets fiskevårdsområde": ("Kvarnbergsvattnets fvof", "Jämtland"),
    "Björkvattnets fiskevårdsområde": ("Björkvattnets fvof", "Jämtland"),
    "Gäddede fiskevårdsområde": ("Gäddede fvof", "Jämtland"),
    "Gussvattnet-Storvattnets fiskevårdsområde": (
        "Gussvattnet-Storvattnets fvof",
        "Jämtland",
    ),
    "Håkafot-Häggnäsets fiskevårdsområde": ("Håkafot-Häggnäsets fvof", "Jämtland"),
    "Fågelbergets fiskevårdsområde": ("Fågelbergets fvof", "Jämtland"),
    "Risede fiskevårdsområde": ("Risede fvof", "Jämtland"),
    "Lidsjöbergs fiskevårdsområde": ("Lidsjöbergs fvof", "Jämtland"),
    "Övre Vattudalens fiskevårdsområde": ("Övre Vattudalens fvof", "Jämtland"),
    "Tåsjö fiskevårdsområde": ("Tåsjöns fvof", "Jämtland"),
    "Hotings fiskevårdsområde, inkl Lojsinit": ("Hotings fvof", "Jämtland"),
    "Bellvik-Rörström fiskevårdsområde": ("Bellvik-Rörströms fvof", "Jämtland"),
    "Rörströmsälvens fiskevårdsområde": ("Rörströmsälvens fvof", "Jämtland"),
    "Rossöns fiskevårdsområde": ("Rossöns fvof", "Jämtland"),
    "Vängelälven fiskevårdsområde": ("Vängelälvens fvof", "Jämtland"),
    "Övre Öjåns fiskevårdsområde": ("Övre Öjåns fvof", "Jämtland"),
    "Dragans fiskevårdsområde": ("Dragans fvof", "Jämtland"),
    "Flåsjöns fiskevårdsområde": ("Flåsjöns fvof", "Jämtland"),
    "Järilvattnets fiskevårdsområde": ("Järilvattnets fvof", "Jämtland"),
    "Russfjärdens fiskevårdsområde": ("Russfjärdens fvof", "Jämtland"),
    "Malmsjöns fiskevårdsområde": ("Malmsjöns fvof", "Jämtland"),
    "Faxebygdens fiskevårdsområde": ("Faxebygdens fvof", "Jämtland"),
    "Görviks fiskevårdsområde": ("Görviks fvof", "Jämtland"),
    "Ede-Grenås fiskevårdsområde": ("Ede-Grenås fvof", "Jämtland"),
    "Solbergs-Vikens fiskevårdsområde": ("Solberg-Vikens fvof", "Jämtland"),
    # saknas i Fiskekartan → hoppa över
    "Stora Blåsjöns samfällighet": None,
    "Bonäset-Risnäset-Äspnäs samfällighet": None,
    "Öhns fiskevårdsområde": None,
}

def _fold_token(t: str) -> str:
    s = unicodedata.normalize("NFKD", t or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


# Jämför alltid med foldad form (ä→a, ö→o) så "Gädda" matchar.
SPECIES_TOKENS = {
    _fold_token(s)
    for s in (
        "Gädda",
        "Harr",
        "Öring",
        "Röding",
        "Abborre",
        "Sik",
        "Lake",
        "Mört",
        "Gös",
        "Nors",
        "Id",
        "Lax",
        "Regnbåge",
        "Sutare",
        "Sarv",
        "Siklöja",
        "Elritsa",
        "Braxen",
        "Bäckröding",
        "Kanadaröding",
        "Ål",
    )
}

SOURCE_URLS = {
    "stromsund_fiskebroschyr_2026": BROCHURE_URL,
    "bjorna_nu": "https://bjorna.nu/jakt-och-fiske/",
    "naturguiden_ornskoldsvik": (
        "https://naturguiden.ornskoldsvik.se/aktivitetssidor/"
        "bjornafvo.4.7e6e27ac17a56e44c494854.html"
    ),
    "gallivare_kommun_fiskeguide": GALLIVARE_URL,
    "glommersbygden": "https://glommersbygden.se/fiske/",
    "hotagen.com": "https://hotagen.com/",
    "dromfiske": "https://dromfiske.com/akersjons-fvo/",
    "fiskaiberg": "https://www.fiskaiberg.se/fiska/fiskekort-fvo/dammans-fvo/",
    "ljungandalen_fiskeguide": "https://abcdocz.com/doc/2899316/sn%C3%B6bergs-fvo",
    "lekeberg_kommun": (
        "https://lekeberg.se/upplevaochgora/fiske.4.711bc06b14820d116bc45f0.html"
    ),
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(
        r"\b(fvof|fvo|kfo|kfof|fiskevardsomrade|forening|sameby|samfallighet)\b",
        "",
        s,
        flags=re.I,
    )
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def infer_typ(namn: str) -> str:
    n = (namn or "").casefold()
    if any(x in n for x in ("älven", "ån", "forsen", "strömmen", "bäck")):
        return "vattendrag"
    return "sjö"


def clean_stromsund_water_name(raw: str) -> str | None:
    """Returnera rent vattennamn eller None om junk."""
    name = (raw or "").strip()
    if not name:
        return None
    if re.search(r"(?i)minimått|tillgängligt\s+fiske", name):
        # försök salvaga prefix före artlista/meta
        m = re.match(
            r"^(.+?)\s+(?:"
            + "|".join(
                re.escape(s)
                for s in (
                    "Harr",
                    "Öring",
                    "Röding",
                    "Abborre",
                    "Gädda",
                    "Sik",
                    "Lake",
                    "Mört",
                    "Gös",
                )
            )
            + r")\b",
            name,
        )
        if m and re.search(
            r"(?i)(sjön|sjöns|ån|älven|forsen|vattnet|tjärn|tjärnen|selet)$",
            m.group(1).strip(),
        ):
            name = m.group(1).strip()
        else:
            return None
    if re.search(r"(?i)^övriga\s+vatten$", name):
        return None
    toks = re.findall(r"[A-Za-zÅÄÖåäö\-]+", name)
    if not toks:
        return None
    if all(_fold_token(t) in SPECIES_TOKENS for t in toks):
        return None
    return name


def find_fvo(fvos: list[dict], namn: str, lan: str | None = None) -> dict | None:
    hits = [f for f in fvos if f.get("namn") == namn]
    if lan:
        hits = [f for f in hits if f.get("ansvarigt_lan") == lan]
    if len(hits) == 1:
        return hits[0]
    if not hits and lan:
        # exact name without lan filter as fallback only if unique
        hits = [f for f in fvos if f.get("namn") == namn]
        if len(hits) == 1:
            return hits[0]
    return hits[0] if len(hits) == 1 else None


def upsert_vatten(
    fvo: dict,
    vatten_namn: str,
    arter: list[str],
    source: str,
    url: str | None = None,
) -> dict:
    """Lägg till eller unionera vattenpost. Returnerar audit-rad."""
    arter = sorted({a for a in arter if a}, key=lambda s: s.casefold())
    if not arter or not vatten_namn:
        return {"action": "skip", "reason": "empty"}

    vatten = list(fvo.get("vatten") or [])
    target_fold = fold(vatten_namn)
    match = None
    for v in vatten:
        if fold(v.get("namn") or "") == target_fold:
            match = v
            break

    url = url or SOURCE_URLS.get(source)
    if match:
        before = set(match.get("arter") or [])
        after = sorted(before | set(arter), key=lambda s: s.casefold())
        match["arter"] = after
        kallor = list(match.get("kallor") or [])
        if source not in kallor:
            kallor.append(source)
            match["kallor"] = kallor
        if url:
            refs = list(match.get("referenser") or [])
            if url not in refs:
                refs.append(url)
                match["referenser"] = refs
        fvo["vatten"] = vatten
        action = "merge" if after != sorted(before, key=lambda s: s.casefold()) else "touch"
        added = sorted(set(after) - before, key=lambda s: s.casefold())
    else:
        entry = {
            "typ": infer_typ(vatten_namn),
            "namn": vatten_namn,
            "smhi_id": None,
            "eu_cd": None,
            "lokaler": None,
            "arter": arter,
            "kallor": [source],
            "senaste_fiskeaar": None,
            "referenser": [url] if url else [],
        }
        vatten.append(entry)
        fvo["vatten"] = vatten
        action = "create"
        added = arter

    # tipslänk + FVO-källa
    if url:
        tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [url]))
        fvo["lokala_tipslankar"] = tips
    fvo.setdefault("kallor", [])
    if source not in fvo["kallor"]:
        fvo["kallor"].append(source)

    # unionera upp till FVO-arter
    before_fvo = set(fvo.get("arter") or [])
    extra_fvo = set(arter) - before_fvo
    if extra_fvo:
        after_fvo = sorted(before_fvo | set(arter), key=lambda s: s.casefold())
        fvo["arter"] = after_fvo
        prev = fvo.get("extern_enrichment") or {}
        fvo["extern_enrichment"] = {
            "tillagda_arter": sorted(
                set(prev.get("tillagda_arter") or []) | extra_fvo,
                key=lambda s: s.casefold(),
            ),
            "kallor_url:er": list(
                dict.fromkeys((prev.get("kallor_url:er") or []) + ([url] if url else []))
            ),
        }
        if isinstance(fvo.get("statistik"), dict):
            fvo["statistik"]["antal_arter"] = len(after_fvo)

    return {
        "action": action,
        "fvo": fvo["namn"],
        "lan": fvo.get("ansvarigt_lan"),
        "vatten": vatten_namn,
        "source": source,
        "tillagda_vattenarter": added,
        "tillagda_fvoarter": sorted(extra_fvo, key=lambda s: s.casefold()),
    }


# --- Kuraterade vattenlistor (verifierade 1:1) ---

CURATED: list[dict] = [
    # Björna – naturguiden + bjorna.nu
    {
        "namn": "Björna fvof",
        "lan": "Västernorrland",
        "vatten": [
            {"namn": "Hundsjön", "arter": ["Abborre", "Gädda", "Mört"], "kalla": "bjorna_nu"},
            {"namn": "Stattjärn", "arter": ["Abborre", "Gädda", "Mört"], "kalla": "bjorna_nu"},
            {"namn": "Gidetjärn", "arter": ["Abborre", "Gädda", "Mört"], "kalla": "bjorna_nu"},
            {
                "namn": "Mattarbodtjärn",
                "arter": ["Abborre", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Stortjärn",
                "arter": ["Abborre", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Löran",
                "arter": ["Abborre", "Mört", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Inner-mörttjärn",
                "arter": ["Abborre", "Mört", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Ytter-mörttjärn",
                "arter": ["Abborre", "Mört", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Stor-Fisktjärn",
                "arter": ["Abborre", "Mört", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Lill-Fisktjärn",
                "arter": ["Abborre", "Mört", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Stor-Hötjärn",
                "arter": ["Abborre", "Mört", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {"namn": "Kravattnet", "arter": ["Röding", "Öring"], "kalla": "bjorna_nu"},
            {
                "namn": "Rödtjärn",
                "arter": ["Bäckröding", "Röding", "Öring"],
                "kalla": "naturguiden_ornskoldsvik",
            },
            {
                "namn": "Västersjön",
                "arter": ["Bäckröding", "Röding", "Öring"],
                "kalla": "bjorna_nu",
            },
            {
                "namn": "Gideälven (nedströms Björna kraftverk)",
                "arter": ["Abborre", "Gädda", "Gös", "Harr", "Lake", "Öring"],
                "kalla": "bjorna_nu",
                "note": "Lake från bjorna.nu; övriga från båda källor",
            },
            {"namn": "Björna-Lillån", "arter": ["Harr", "Öring"], "kalla": "bjorna_nu"},
        ],
    },
    {
        "namn": "Björnsjö-Ledings fvof",
        "lan": "Västernorrland",
        "vatten": [
            {
                "namn": "Trehörningen",
                "arter": ["Abborre", "Siklöja", "Öring"],
                "kalla": "bjorna_nu",
            }
        ],
    },
    {
        "namn": "Hakkas fvof",
        "lan": "Norrbotten",
        "vatten": [
            {
                "namn": "Sangersjön",
                "arter": ["Abborre", "Harr"],
                "kalla": "gallivare_kommun_fiskeguide",
            },
            {
                "namn": "Skrövån (Hakkas-sträcka)",
                "arter": ["Harr", "Öring"],
                "kalla": "gallivare_kommun_fiskeguide",
            },
        ],
    },
    {
        "namn": "Sammakko-Lillbergets fvof",
        "lan": "Norrbotten",
        "vatten": [
            {
                "namn": "Sammakkosjön",
                "arter": ["Abborre", "Gädda", "Harr", "Regnbåge", "Röding", "Öring"],
                "kalla": "gallivare_kommun_fiskeguide",
            },
            {
                "namn": "Linaälven (Sammako-Lillbergets sträcka)",
                "arter": ["Gädda", "Harr", "Lax", "Öring"],
                "kalla": "gallivare_kommun_fiskeguide",
            },
        ],
    },
    {
        "namn": "Södra Sandträsk fvof",
        "lan": "Norrbotten",
        "vatten": [
            {
                "namn": "Södra Sandträsk",
                "arter": ["Abborre", "Gädda"],
                "kalla": "glommersbygden",
            }
        ],
    },
    {
        "namn": "Sandselet - Gallejaur fvof",
        "lan": "Norrbotten",
        "vatten": [
            {
                "namn": "Gallejaur",
                "arter": ["Abborre", "Gädda", "Harr", "Sik", "Öring"],
                "kalla": "glommersbygden",
            },
            {
                "namn": "Sandselet",
                "arter": ["Abborre", "Gädda", "Öring"],
                "kalla": "glommersbygden",
            },
        ],
    },
    {
        "namn": "Järvträsk fvof",
        "lan": "Norrbotten",
        "vatten": [
            {"namn": "Järvträsk", "arter": ["Abborre", "Gädda"], "kalla": "glommersbygden"}
        ],
    },
    # Hotagen – endast explicit prose (inte kartgissning)
    {
        "namn": "Gunnarvattnets fvof",
        "lan": "Jämtland",
        "vatten": [
            {
                "namn": "Gunnarvattnet",
                "arter": ["Röding", "Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/a-gunnarvattnets-fvo",
            },
            {
                "namn": "Storplutten",
                "arter": ["Röding"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/a-gunnarvattnets-fvo",
            },
            {
                "namn": "Lilltjärn",
                "arter": ["Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/a-gunnarvattnets-fvo",
            },
        ],
    },
    {
        "namn": "Rörvattnet-Skogsjö fvof",
        "lan": "Jämtland",
        "vatten": [
            {
                "namn": "Stortjärn",
                "arter": ["Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/c-rorvattnet-skogsjo-fvo",
            },
            {
                "namn": "Rundtjärn",
                "arter": ["Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/c-rorvattnet-skogsjo-fvo",
            },
            {
                "namn": "Skogsjön",
                "arter": ["Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/c-rorvattnet-skogsjo-fvo",
            },
            {
                "namn": "Tallsjön",
                "arter": ["Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/c-rorvattnet-skogsjo-fvo",
            },
            {
                "namn": "Bustadtjärn",
                "arter": ["Öring"],
                "kalla": "hotagen.com",
                "url": "https://hotagen.com/c-rorvattnet-skogsjo-fvo",
            },
        ],
    },
    {
        "namn": "Åkersjöns fvof",
        "lan": "Jämtland",
        "vatten": [
            {"namn": "Åkersjön", "arter": ["Röding", "Öring"], "kalla": "dromfiske"},
            {
                "namn": "Åkerån",
                "arter": ["Harr", "Öring"],
                "kalla": "dromfiske",
                "note": "öring explicit; harr från tidigare Drömfiske-kuratering",
            },
        ],
    },
    {
        "namn": "Dammåns fvof",
        "lan": "Jämtland",
        "vatten": [
            {
                "namn": "Häggsåssjön",
                "arter": ["Röding", "Öring"],
                "kalla": "fiskaiberg",
            },
            {
                "namn": "Dammån",
                "arter": ["Harr", "Röding", "Öring"],
                "kalla": "fiskaiberg",
            },
        ],
    },
    {
        "namn": "Snöbergs fvof",
        "lan": "Västernorrland",
        "vatten": [
            {
                "namn": "Stora Grimsjön",
                "arter": ["Abborre", "Regnbåge", "Röding", "Öring"],
                "kalla": "ljungandalen_fiskeguide",
            },
            {
                "namn": "Lilla Grimsjön",
                "arter": ["Abborre", "Regnbåge", "Röding", "Öring"],
                "kalla": "ljungandalen_fiskeguide",
            },
            {
                "namn": "Bytjärn",
                "arter": ["Abborre", "Regnbåge", "Röding", "Öring"],
                "kalla": "ljungandalen_fiskeguide",
            },
        ],
    },
    {
        "namn": "Stora och lilla Grundsjöns fvof",
        "lan": "Västernorrland",
        "vatten": [
            {
                "namn": "Stora Grundsjön",
                "arter": ["Abborre", "Gädda", "Sik", "Öring"],
                "kalla": "ljungandalen_fiskeguide",
                "url": "https://abcdocz.com/doc/4127883/stora-och-lilla-grundsj%C3%B6ns-fvo",
            },
            {
                "namn": "Lilla Grundsjön",
                "arter": ["Abborre", "Gädda", "Sik", "Öring"],
                "kalla": "ljungandalen_fiskeguide",
                "url": "https://abcdocz.com/doc/4127883/stora-och-lilla-grundsj%C3%B6ns-fvo",
            },
        ],
    },
    {
        "namn": "Hemsjöns fvof",
        "lan": "Örebro",
        "vatten": [
            {
                "namn": "Stora Hemsjön",
                "arter": ["Abborre", "Gädda", "Gös"],
                "kalla": "lekeberg_kommun",
            },
            {
                "namn": "Lilla Hemsjön",
                "arter": ["Abborre", "Gädda", "Gös"],
                "kalla": "lekeberg_kommun",
            },
        ],
    },
]


def apply_stromsund(fvos: list[dict]) -> tuple[list[dict], dict]:
    parsed = json.loads(STROMSUND_VATTEN.read_text(encoding="utf-8"))
    applied: list[dict] = []
    stats = {
        "sections": 0,
        "mapped": 0,
        "unmapped": 0,
        "junk_dropped": 0,
        "salvaged": 0,
        "waters_applied": 0,
    }
    for section, obj in parsed.items():
        stats["sections"] += 1
        mapping = BROCHURE_TO_FVO.get(section)
        if mapping is None and section in BROCHURE_TO_FVO:
            stats["unmapped"] += 1
            continue
        if mapping is None:
            # okänd sektion
            stats["unmapped"] += 1
            continue
        fvo_namn, lan = mapping
        fvo = find_fvo(fvos, fvo_namn, lan)
        if not fvo:
            stats["unmapped"] += 1
            applied.append(
                {
                    "action": "unmatched_fvo",
                    "section": section,
                    "expected": fvo_namn,
                }
            )
            continue
        stats["mapped"] += 1
        waters = obj.get("vatten") if isinstance(obj, dict) else obj
        for w in waters or []:
            raw_name = w.get("vatten") or w.get("namn") or ""
            clean = clean_stromsund_water_name(raw_name)
            if clean is None:
                stats["junk_dropped"] += 1
                continue
            if clean != raw_name.strip():
                stats["salvaged"] += 1
            arter = list(w.get("arter") or [])
            if not arter:
                continue
            row = upsert_vatten(
                fvo,
                clean,
                arter,
                "stromsund_fiskebroschyr_2026",
                BROCHURE_URL,
            )
            if row.get("action") != "skip":
                stats["waters_applied"] += 1
                applied.append(row)
    return applied, stats


def apply_curated(fvos: list[dict]) -> list[dict]:
    applied: list[dict] = []
    for block in CURATED:
        fvo = find_fvo(fvos, block["namn"], block.get("lan"))
        if not fvo:
            applied.append(
                {
                    "action": "unmatched_fvo",
                    "expected": block["namn"],
                    "lan": block.get("lan"),
                }
            )
            continue
        for w in block.get("vatten") or []:
            row = upsert_vatten(
                fvo,
                w["namn"],
                list(w.get("arter") or []),
                w.get("kalla") or "curated",
                w.get("url"),
            )
            if row.get("action") != "skip":
                applied.append(row)
    return applied


def coverage(fvos: list[dict]) -> dict:
    c = {
        "antal_fvo": len(fvos),
        "har_nagon_art": 0,
        "saknar_arter": 0,
        "har_vattenposter": 0,
        "med_arter_men_0_vatten": 0,
        "total_vatten": 0,
        "vatten_med_brochure_eller_extern": 0,
    }
    for f in fvos:
        if f.get("arter"):
            c["har_nagon_art"] += 1
            if not f.get("vatten"):
                c["med_arter_men_0_vatten"] += 1
        else:
            c["saknar_arter"] += 1
        if f.get("vatten"):
            c["har_vattenposter"] += 1
        for v in f.get("vatten") or []:
            c["total_vatten"] += 1
            kallor = {k.casefold() for k in (v.get("kallor") or [])}
            if kallor - {"nors", "sers", "kul", "slu_provfiske"}:
                c["vatten_med_brochure_eller_extern"] += 1
    return c


def empty_list(fvos: list[dict]) -> list[dict]:
    out = []
    for f in fvos:
        if f.get("arter"):
            continue
        out.append(
            {
                "namn": f.get("namn"),
                "lan": f.get("ansvarigt_lan"),
                "kommuner": f.get("kommuner"),
            }
        )
    return sorted(out, key=lambda x: ((x["lan"] or ""), (x["namn"] or "")))


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    before = coverage(fvos)

    applied_s, strom_stats = apply_stromsund(fvos)
    applied_c = apply_curated(fvos)
    applied = applied_s + applied_c

    after = coverage(fvos)
    empties = empty_list(fvos)

    catalog: set[str] = set()
    for f in fvos:
        catalog.update(f.get("arter") or [])

    created = sum(1 for a in applied if a.get("action") == "create")
    merged = sum(1 for a in applied if a.get("action") == "merge")
    fvo_spp = sum(len(a.get("tillagda_fvoarter") or []) for a in applied)

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = {
        "har_fiskekartan_arter": data["meta"].get("coverage", {}).get(
            "har_fiskekartan_arter"
        ),
        "har_survey_arter": data["meta"].get("coverage", {}).get("har_survey_arter"),
        "har_nagon_art": after["har_nagon_art"],
        "saknar_arter": after["saknar_arter"],
        "har_vattenposter": after["har_vattenposter"],
        "har_extern_enrichment": sum(1 for f in fvos if f.get("extern_enrichment")),
        "har_gbif_enrichment": sum(1 for f in fvos if f.get("gbif_enrichment")),
        "har_ifiske_spegel_kontroll": sum(
            1 for f in fvos if f.get("ifiske_spegel_kontroll")
        ),
    }
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["vatten_level_careful_pass"] = {
        "created_vatten": created,
        "merged_vatten": merged,
        "tillagda_fvo_artposter": fvo_spp,
        "stromsund": strom_stats,
        "curated_blocks": len(CURATED),
        "before": before,
        "after": after,
        "empty_fvo_count": len(empties),
        "blocked_sources": ["natureit", "artportalen", "ifiske_artdata"],
    }
    pol = data["meta"].setdefault("policy", {})
    pol.update(
        {
            "ifiske_artdata": False,
            "natureit": False,
            "artportalen": False,
            "beskrivning": (
                "iFiske endast spegel/länktips. NatureIT och Artportalen används inte. "
                "Artdata från Fiskekartan, SLU, GBIF, FVOF, FiskaiBerg, Drömfiske, "
                "Strömsunds fiskebroschyr (vattennivå), Björna/Övik naturguide, "
                "Gällivare kommun, Glommersbygden, Hotagen.com, Ljungandalen, Lekeberg."
            ),
        }
    )
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log = {
        "applied": applied,
        "stats": data["meta"]["vatten_level_careful_pass"],
        "empty_fvo": empties,
    }
    (OUT / "vatten_level_careful_pass_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "created_vatten": created,
                "merged_vatten": merged,
                "tillagda_fvo_artposter": fvo_spp,
                "stromsund": strom_stats,
                "before": before,
                "after": after,
                "empty_fvo": len(empties),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
