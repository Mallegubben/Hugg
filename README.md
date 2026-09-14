# Hugg – FVO-artlista

**[`data/index.json`](data/index.json)** – en enda indexfil.

## Policy
- **iFiske = spegel/kontroll** (för att se luckor), **aldrig artdata**
- All artdata hämtas från **respektive FVOF**, Fiskekartan, SLU (NORS/SERS/KUL) eller GBIF

## Källor till `arter`
1. Fiskekartan (`VANL_ART` / `OVRI_ART`)
2. SLU NORS / SERS / KUL
3. GBIF (förekomster inom FVO-omslutning)
4. FVOF-hemsidor (via `URL_FVOF` och länkar som iFiske *hänvisar till*)

`ifiske_spegel_kontroll` i datasetet är bara en jämförelserapport – den skriver inte arter.

## Köra om
```bash
pip install -r requirements.txt
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_gbif.py
python scripts/enrich_gbif_retry.py
python scripts/enrich_via_fvof_using_ifiske_mirror.py
python scripts/build_index.py
```
