#!/usr/bin/env python3
"""
Noggrann berikning Jämtland (Strömsund fiskebroschyr) + Norrbotten (Gällivare kommun).

- Parsar vattenvisa artlistor i Strömsunds kommuns fiskebroschyr 2026/2027
  och tar union per FVO (ej regional övertolkning).
- Rättar tidigare frostviken_regional / felmatchad bollnas-kontaminering där
  broschyren är auktoritativ (fjäll-FVO med endast öring/röding).
- Hakkas + Sammakko från Gällivare kommuns fiskeguide (tillåten källa).
- Kallön/Nurrholm/Bredträsk: ingen tillåten publik artlista → endast tipslänk
  till iFiske där den redan finns (ingen artdata från iFiske).

Förbjudet: NatureIT, Artportalen, iFiske som artdata.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
ARTLISTA = OUT / "fvo_artlista.json"
BROCHURE_TXT = RAW / "stromsund_fiskebroschyr_2026.txt"
BROCHURE_JSON = RAW / "stromsund_fvo_arter_parsed.json"
BROCHURE_URL = (
    "https://www.stromsund.se/download/18.540f27c719d93a15328128c2/"
    "1777021924241/Fiskebroschyr%202026%20(interaktiv).pdf"
)
GALLIVARE_ANGES = (
    "https://gallivare.se/uppleva-och-gora/idrott-motion-och-friluftsliv/"
    "friluftsliv-och-motion/fiske/fiskeguiden/har-kan-du-fiska/angesan---linaalven"
)

SPECIES_CANON = [
    "Abborre",
    "Gädda",
    "Harr",
    "Sik",
    "Röding",
    "Öring",
    "Lake",
    "Braxen",
    "Mört",
    "Nors",
    "Id",
    "Lax",
    "Regnbåge",
    "Sutare",
    "Sarv",
    "Gös",
    "Siklöja",
    "Elritsa",
    "Kanadaröding",
    "Bäckröding",
]
SPECIES_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in SPECIES_CANON) + r")\b",
    re.I,
)

# Exakt mappning broschyrrubrik → Fiskekartan-namn (inga lösa partialer)
BROCHURE_TO_FVO = {
    "Stora Blåsjöns samfällighet": None,  # saknas som FVOF i Fiskekartan
    "Jormvattnets fiskevårdsområde": "Jormvattnets fvof",
    "Lilla Blåsjöns fiskevårdsområde": "Lilla Blåsjöns fvof",
    "Kvarnbergsvattnets fiskevårdsområde": "Kvarnbergsvattnets fvof",
    "Björkvattnets fiskevårdsområde": "Björkvattnets fvof",
    "Gäddede fiskevårdsområde": "Gäddede fvof",
    "Gussvattnet-Storvattnets fiskevårdsområde": "Gussvattnet-Storvattnets fvof",
    "Håkafot-Häggnäsets fiskevårdsområde": "Håkafot-Häggnäsets fvof",
    "Fågelbergets fiskevårdsområde": "Fågelbergets fvof",
    "Risede fiskevårdsområde": "Risede fvof",
    "Lidsjöbergs fiskevårdsområde": "Lidsjöbergs fvof",
    "Övre Vattudalens fiskevårdsområde": "Övre Vattudalens fvof",
    "Tåsjö fiskevårdsområde": "Tåsjöns fvof",
    "Hotings fiskevårdsområde, inkl Lojsinit": "Hotings fvof",
    "Bellvik-Rörström fiskevårdsområde": "Bellvik-Rörströms fvof",
    "Rörströmsälvens fiskevårdsområde": "Rörströmsälvens fvof",
    "Rossöns fiskevårdsområde": "Rossöns fvof",
    "Vängelälven fiskevårdsområde": "Vängelälvens fvof",
    "Övre Öjåns fiskevårdsområde": "Övre Öjåns fvof",
    "Dragans fiskevårdsområde": "Dragans fvof",
    "Bonäset-Risnäset-Äspnäs samfällighet": None,  # saknas i Fiskekartan
    "Flåsjöns fiskevårdsområde": "Flåsjöns fvof",
    "Järilvattnets fiskevårdsområde": "Järilvattnets fvof",
    "Öhns fiskevårdsområde": None,
    "Russfjärdens fiskevårdsområde": "Russfjärdens fvof",
    "Malmsjöns fiskevårdsområde": "Malmsjöns fvof",
    "Faxebygdens fiskevårdsområde": "Faxebygdens fvof",
    "Görviks fiskevårdsområde": "Görviks fvof",
    "Ede-Grenås fiskevårdsområde": "Ede-Grenås fvof",
    "Solbergs-Vikens fiskevårdsområde": "Solberg-Vikens fvof",
}

# FVO där broschyrens vattenlista är auktoritativ och tidigare
# frostviken_regional/felmatchning ska rensas bort (behåll SLU/GBIF/fk).
AUTHORITATIVE_BROCHURE_CORRECT = {
    "Jormvattnets fvof",
    "Björkvattnets fvof",
    "Kvarnbergsvattnets fvof",
    "Lilla Blåsjöns fvof",
    "Risede fvof",
    "Fågelbergets fvof",
    "Gäddede fvof",  # camping-sida hade Abborre; broschyrens vattenlista har det ej
}

# Källor som räknas som "behåll" vid rättning
KEEP_SOURCE_HINTS = (
    "fiskekartan",
    "slu",
    "nors",
    "sers",
    "kul",
    "gbif",
)

GALLIVARE_CURATED = [
    {
        "namn": "Hakkas fvof",
        "lan": "Norrbotten",
        "arter": ["Abborre", "Harr", "Öring"],
        "url": GALLIVARE_ANGES,
        "source": "gallivare_kommun_fiskeguide",
        "note": "Sangersjön: harr+abborre; Skrövån (Hakkas-sträcka): öring+harr",
    },
    {
        "namn": "Sammakko-Lillbergets fvof",
        "lan": "Norrbotten",
        "arter": ["Abborre", "Gädda", "Harr", "Lax", "Regnbåge", "Röding", "Öring"],
        "url": GALLIVARE_ANGES,
        "source": "gallivare_kommun_fiskeguide",
        "note": "Sammakkosjön + Linaälven (Sammako-Lillbergets)",
    },
]


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


def canon_species(text: str) -> set[str]:
    found: set[str] = set()
    for m in SPECIES_RE.findall(text or ""):
        for c in SPECIES_CANON:
            if m.casefold() == c.casefold():
                found.add(c)
                break
    return found


def parse_brochure(text: str) -> dict[str, list[str]]:
    toc = re.findall(
        r"^([A-ZÅÄÖa-zåäö0-9ÄÖÅ\- ,]+(?:fiskevårdsområde|samfällighet|"
        r"fiskevårdsområdesförening)[^\.]*)\s*\.{2,}\s*(\d+)",
        text,
        re.M,
    )
    pages: dict[int, str] = {}
    parts = re.split(r"\n===== PAGE (\d+) =====\n", text)
    it = iter(parts[1:])
    for num, content in zip(it, it):
        pages[int(num)] = content

    toc_sorted = sorted([(int(p), n.strip()) for n, p in toc], key=lambda x: x[0])
    # drop TOC map page
    toc_sorted = [(p, n) for p, n in toc_sorted if not n.lower().startswith("karta över")]

    results: dict[str, list[str]] = {}
    for i, (start, name) in enumerate(toc_sorted):
        end = toc_sorted[i + 1][0] if i + 1 < len(toc_sorted) else max(pages) + 1
        chunk = "\n".join(pages.get(p, "") for p in range(start, end))
        table_spp: set[str] = set()
        for m in re.finditer(r"(?m)^\d+\s+.+$", chunk):
            table_spp |= canon_species(m.group(0))
        if not table_spp:
            # fallback: header chips only (conservative)
            table_spp = canon_species(chunk[:500])
        results[name] = sorted(table_spp)
    return results


def baseline_keep(fvo: dict) -> set[str]:
    """Arter som kommer från Fiskekartan / SLU-vatten / GBIF – behåll vid rättning."""
    keep: set[str] = set()
    fk = fvo.get("fiskekartan") or {}
    keep.update(fk.get("arter") or [])
    keep.update(fk.get("vanliga_arter") or [])
    keep.update(fk.get("ovriga_arter") or [])
    for v in fvo.get("vatten") or []:
        keep.update(v.get("arter") or [])
    ge = fvo.get("gbif_enrichment") or {}
    keep.update(ge.get("arter_i_omslutning") or [])
    # Elritsa/simpor etc from surveys often only in arter via slu
    kallor = set(fvo.get("kallor") or [])
    if any(k.startswith(KEEP_SOURCE_HINTS) or k in KEEP_SOURCE_HINTS for k in kallor):
        # also keep species not in extern tillagda (pre-existing)
        extern = set((fvo.get("extern_enrichment") or {}).get("tillagda_arter") or [])
        keep |= set(fvo.get("arter") or []) - extern
    return keep


def apply_add(fvo: dict, spp: list[str], source: str, url: str | None) -> dict | None:
    before = set(fvo.get("arter") or [])
    extra = set(spp) - before
    if url:
        tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [url]))
        fvo["lokala_tipslankar"] = tips
    if not extra:
        return None
    after = sorted(before | set(spp), key=lambda s: s.casefold())
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
        "action": "add",
        "source": source,
        "tillagda": sorted(extra, key=lambda s: s.casefold()),
        "gaps_closed": closed,
        "url": url,
    }


def apply_correct(fvo: dict, brochure_spp: set[str], source: str) -> dict | None:
    """Ta bort arter som lagts till via felaktig regional/scraping men ej finns i broschyr/baseline."""
    keep = baseline_keep(fvo) | brochure_spp
    before = list(fvo.get("arter") or [])
    removed = [a for a in before if a not in keep]
    if not removed:
        return None
    after = sorted([a for a in before if a in keep], key=lambda s: s.casefold())
    # always keep brochure union
    after = sorted(set(after) | brochure_spp, key=lambda s: s.casefold())
    fvo["arter"] = after
    prev = fvo.get("extern_enrichment") or {}
    tillagda = set(prev.get("tillagda_arter") or []) - set(removed)
    # mark correction
    urls = list(prev.get("kallor_url:er") or [])
    # drop known bad bollnas urls
    urls = [u for u in urls if "bollnas" not in (u or "").lower()]
    if BROCHURE_URL not in urls:
        urls.append(BROCHURE_URL)
    fvo["extern_enrichment"] = {
        "tillagda_arter": sorted(tillagda | (set(after) - baseline_keep(fvo)), key=lambda s: s.casefold()),
        "kallor_url:er": list(dict.fromkeys(urls)),
        "rattning": {
            "borttagna": sorted(removed, key=lambda s: s.casefold()),
            "orsak": "stromsund_broschyr_auktoritativ_mot_regional_overtolkning",
        },
    }
    # clean kallor: remove frostviken_regional if present; add brochure source
    kallor = [k for k in (fvo.get("kallor") or []) if k != "frostviken_regional"]
    if source not in kallor:
        kallor.append(source)
    # remove tip to camping as sole regional if we now have brochure
    tips = [t for t in (fvo.get("lokala_tipslankar") or [])]
    if BROCHURE_URL not in tips:
        tips.append(BROCHURE_URL)
    fvo["lokala_tipslankar"] = tips
    fvo["kallor"] = kallor
    if isinstance(fvo.get("statistik"), dict):
        fvo["statistik"]["antal_arter"] = len(after)
    ctrl = fvo.get("ifiske_spegel_kontroll")
    if ctrl and ctrl.get("spegel_arter"):
        ctrl["luckor_ej_funna_hos_fvof"] = sorted(
            set(ctrl["spegel_arter"]) - set(after), key=lambda s: s.casefold()
        )
    return {
        "fvo": fvo["namn"],
        "action": "correct",
        "source": source,
        "borttagna": sorted(removed, key=lambda s: s.casefold()),
        "kvar": after,
        "brochure": sorted(brochure_spp),
    }


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


def by_name(fvos: list[dict], namn: str, lan: str | None = None) -> dict | None:
    cands = fvos
    if lan:
        cands = [f for f in fvos if f.get("ansvarigt_lan") == lan]
    exact = [f for f in cands if f.get("namn") == namn]
    if len(exact) == 1:
        return exact[0]
    folded = fold(namn)
    soft = [f for f in cands if fold(f.get("namn") or "") == folded]
    if len(soft) == 1:
        return soft[0]
    return None


def main() -> None:
    data = json.loads(ARTLISTA.read_text(encoding="utf-8"))
    fvos = data["fvo"]
    before_j = lan_stats(fvos, "Jämtland")
    before_n = lan_stats(fvos, "Norrbotten")

    if not BROCHURE_TXT.exists():
        raise SystemExit(f"Saknar {BROCHURE_TXT} – kör PDF-extraktion först")

    brochure = parse_brochure(BROCHURE_TXT.read_text(encoding="utf-8"))
    BROCHURE_JSON.write_text(json.dumps(brochure, ensure_ascii=False, indent=2), encoding="utf-8")

    applied: list[dict] = []
    unmatched: list[str] = []
    source = "stromsund_fiskebroschyr_2026"

    print("1) Strömsund-broschyr – lägg till + rätta…")
    for brochure_name, spp in brochure.items():
        fvo_name = BROCHURE_TO_FVO.get(brochure_name)
        if brochure_name not in BROCHURE_TO_FVO:
            # unknown TOC entry
            continue
        if not fvo_name:
            unmatched.append(brochure_name)
            continue
        fvo = by_name(fvos, fvo_name, lan="Jämtland")
        if not fvo:
            unmatched.append(f"{brochure_name} → {fvo_name} (saknas)")
            continue
        spp_set = set(spp)
        if fvo_name in AUTHORITATIVE_BROCHURE_CORRECT:
            res = apply_correct(fvo, spp_set, source)
            if res:
                applied.append(res)
        res_add = apply_add(fvo, spp, source, BROCHURE_URL)
        if res_add:
            applied.append(res_add)

    print("2) Gällivare kommun fiskeguide…")
    for rec in GALLIVARE_CURATED:
        fvo = by_name(fvos, rec["namn"], lan=rec["lan"])
        if not fvo:
            unmatched.append(rec["namn"])
            continue
        res = apply_add(fvo, rec["arter"], rec["source"], rec["url"])
        if res:
            res["note"] = rec.get("note")
            applied.append(res)
        else:
            # still save tip
            tips = list(dict.fromkeys((fvo.get("lokala_tipslankar") or []) + [rec["url"]]))
            fvo["lokala_tipslankar"] = tips

    # Dokumentera tomma Norrbotten utan tillåten källa
    empty_nb_notes = []
    for f in fvos:
        if f.get("ansvarigt_lan") != "Norrbotten" or f.get("arter"):
            continue
        note = {
            "namn": f["namn"],
            "kommuner": f.get("kommuner"),
            "status": "ingen_tillaten_publik_artlista",
            "url_fiskekort": f.get("url_fiskekort"),
        }
        tip = f.get("url_fiskekort")
        if tip and "ifiske" in tip.lower():
            note["tips"] = "iFiske endast tipslänk – artdata ej importerad"
            if not tip.startswith("http"):
                tip = "https://" + tip
            f["lokala_tipslankar"] = list(
                dict.fromkeys((f.get("lokala_tipslankar") or []) + [tip])
            )
        # Nurrholm: publik tipssida finns på iFiske men artdata importeras inte
        if f["namn"] == "Nurrholms fvof":
            tip = "https://www.ifiske.se/fiske-uddjaure-uljajaure-m-fl-vatten.htm"
            note["tips"] = "iFiske endast tipslänk – artdata ej importerad"
            f["lokala_tipslankar"] = list(
                dict.fromkeys((f.get("lokala_tipslankar") or []) + [tip])
            )
        empty_nb_notes.append(note)

    # Wikipedia (tillåten): Stor-Mjölkvattnet – öring och röding (redan i lista; spara källa/tips)
    mjolk = by_name(fvos, "Stor Mjölkvattnets fvof", lan="Jämtland")
    if mjolk:
        wiki = "https://sv.wikipedia.org/wiki/Stor-Mj%C3%B6lkvattnet"
        res = apply_add(mjolk, ["Öring", "Röding"], "wikipedia", wiki)
        if res:
            applied.append(res)
        else:
            tips = list(dict.fromkeys((mjolk.get("lokala_tipslankar") or []) + [wiki]))
            mjolk["lokala_tipslankar"] = tips
            if "wikipedia" not in (mjolk.get("kallor") or []):
                # tip only; no new species
                pass

    # meta
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
    adds = [a for a in applied if a.get("action") == "add"]
    corrects = [a for a in applied if a.get("action") == "correct"]

    data["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["coverage"] = coverage
    data["meta"]["antal_unika_arter"] = len(catalog)
    data["meta"]["stromsund_norrbotten_careful_pass"] = {
        "applied_add": len(adds),
        "applied_correct": len(corrects),
        "added_species_entries": sum(len(a.get("tillagda") or []) for a in adds),
        "removed_species_entries": sum(len(a.get("borttagna") or []) for a in corrects),
        "mirror_gaps_closed": sum(a.get("gaps_closed") or 0 for a in adds),
        "jamtland_before": before_j,
        "jamtland_after": after_j,
        "norrbotten_before": before_n,
        "norrbotten_after": after_n,
        "unmatched_brochure": unmatched,
        "empty_norrbotten_notes": empty_nb_notes,
        "brochure_url": BROCHURE_URL,
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
                "Ottsjö, Strömsunds fiskebroschyr, Gällivare kommun fiskeguide, "
                "Sammakko, Glommersbygden, gfvo.se."
            ),
        }
    )
    data["arter_katalog"] = sorted(catalog, key=lambda s: s.casefold())
    ARTLISTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log = {
        "applied": applied,
        "stats": data["meta"]["stromsund_norrbotten_careful_pass"],
        "brochure_parsed": brochure,
    }
    (OUT / "stromsund_norrbotten_careful_pass_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
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
                "adds": len(adds),
                "corrects": len(corrects),
                "added": sum(len(a.get("tillagda") or []) for a in adds),
                "removed": sum(len(a.get("borttagna") or []) for a in corrects),
                "jamtland": {"before": before_j, "after": after_j},
                "norrbotten": {"before": before_n, "after": after_n},
                "applied": applied,
                "unmatched": unmatched,
                "empty_nb": empty_nb_notes,
                "remaining_thin_jh": [
                    f["namn"]
                    for f in fvos
                    if f.get("ansvarigt_lan") == "Jämtland"
                    and 0 < len(f.get("arter") or []) <= 3
                ],
                "remaining_empty_nb": [
                    f["namn"]
                    for f in fvos
                    if f.get("ansvarigt_lan") == "Norrbotten" and not f.get("arter")
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
