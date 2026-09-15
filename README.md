# Hugg – FVO-artlista

**[`data/index.json`](data/index.json)** – en enda indexfil (FVO, arter, vatten, läns­täckning, tomma FVO, filpekare).

## Policy
1. **Jämför** med iFiske (spegel/kontroll av vilka arter som “borde” finnas)
2. **Hämta aldrig artdata från iFiske** (inga artikoner → `arter`)
3. Använd iFiske för **tips/länkar** till respektive FVO/föreningssida (t.ex. “Gå till hemsida”), och hämta artdata därifrån
4. **Använd aldrig** NatureIT eller Artportalen (varken artdata eller som källa)
5. Övriga tillåtna källor: Fiskekartan, SLU (NORS/SERS/KUL), GBIF, FVOF/föreningssidor, lokala turism-/kommunkartor (t.ex. FiskaiBerg, Drömfiske Jämtland Härjedalen)

## Index (`data/index.json`)
- `fvo` – alla FVO med arter och vatten
- `uppslag.lan` / `uppslag.namn` / `uppslag.art` / `uppslag.vatten`
- `tackning_per_lan` – alla 21 län (Gotland = 0 i Fiskekartan)
- `utan_artlista` – FVO utan arter
- `filer` – pekare till CSV/TSV/MD för klipp och klistra

## Köra om
```bash
pip install -r requirements.txt
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_gbif.py
python scripts/enrich_gbif_retry.py
python scripts/enrich_via_fvof_using_ifiske_mirror.py
python scripts/follow_ifiske_links_to_fvof.py
python scripts/enrich_gbif_for_gaps.py
python scripts/enrich_gbif_gap_retry.py
python scripts/discover_fvof_urls_and_scrape.py
python scripts/enrich_jamtland_local_sources.py
python scripts/enrich_sarna_idre_forteckning.py
python scripts/enrich_jamtland_norrbotten_deep.py
python scripts/enrich_stromsund_norrbotten_careful.py
python scripts/audit_fix_crossmatch_contamination.py
python scripts/enrich_curated_post_decontam.py
python scripts/enrich_vatten_level_careful.py
python scripts/build_index.py
```

## Täckning per län
- Översikt: [`data/processed/fvo_tackning_per_lan.csv`](data/processed/fvo_tackning_per_lan.csv) (alla 21 län).
- **Gotland** saknas i Länsstyrelsernas Fiskekartan-register (0 FVOF i rådata). Övriga län finns. Lokala gotländska fiskekortsområden (t.ex. Närsån, Lojstaträsken) är oftast samfälligheter/upplåtelser utanför FVOF-registret tills LST publicerar dem.

## Kvalitet / rättning
- **Cross-match-dekontaminering:** `bollnasfvf.se` och `malungs-fiske.se` får bara berika sina egna FVO (Bollnäs*/Gävleborg resp. Malungs*/Dalarna). Arter som lagts till via fel sida tas bort om de saknas i Fiskekartan/SLU/GBIF.
- Kuraterade 1:1-tillägg efter rättning (t.ex. Rödåbygden via Laxportalen, Haverö via lokal fiskeguide, Dammån/Åkersjön via Drömfiske).
- **Sverige-runda 2:** Långaskruvs (Naturkartan/Uppvidinge), Hemsjöns (Lekeberg), Björna (bjorna.nu), Baltaks (Hökensås), Norra Simmesjön (Västsverige), Snöbergs + Stora/Lilla Grundsjö (Ljungandalen-guide).

## Jämtland / Norrbotten – lokala källor
- **Strömsunds fiskebroschyr 2026/2027** (`data/raw/stromsund_fiskebroschyr_2026.txt`): vattenvisa artlistor per FVO i Frostviken/Vattudal (PDF sparas ej i git p.g.a. storlek; textutdrag + parsad JSON ingår).
- **Gällivare kommun fiskeguide**: Hakkas (Sangersjön/Skrövån), Sammakko m.fl.
- **Ej använda som artdata:** iFiske-ikoner, NatureIT, Artportalen. Tomma FVO utan egen publik artlista (t.ex. Kallön, Nurrholm, Bredträsk) lämnas tomma hellre än att spegla iFiske.
