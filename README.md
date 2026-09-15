# Hugg – FVO-artlista

**[`data/index.json`](data/index.json)** – en enda indexfil.

## Policy
1. **Jämför** med iFiske (spegel/kontroll av vilka arter som “borde” finnas)
2. **Hämta aldrig artdata från iFiske** (inga artikoner → `arter`)
3. Använd iFiske för **tips/länkar** till respektive FVO/föreningssida (t.ex. “Gå till hemsida”), och hämta artdata därifrån
4. **Använd aldrig** NatureIT eller Artportalen (varken artdata eller som källa)
5. Övriga tillåtna källor: Fiskekartan, SLU (NORS/SERS/KUL), GBIF, FVOF/föreningssidor, lokala turism-/kommunkartor (t.ex. FiskaiBerg, Drömfiske Jämtland Härjedalen)

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
python scripts/build_index.py
```
