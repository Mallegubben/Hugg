# Hugg – FVO-artlista

En enda indexfil med alla fiskevårdsområden, arter och vatten:

**[`data/index.json`](data/index.json)**

Byggd med [Fiskekartan](https://fiskekartan.se/) som ram. iFiske används inte som artdata.

## Täckning

| Mått | Värde |
|------|------:|
| FVO totalt | 1907 |
| FVO med artlista | 1859 |
| FVO utan känd art | 48 |
| Med SLU-survey (NORS/SERS/KUL) | 1565 |
| Berikade via GBIF | 461 |
| Berikade via FVOF-sajter | 43 |
| Vattenposter | 9028 |

## Källor

1. Fiskekartan (`VANL_ART` / `OVRI_ART`)
2. SLU NORS (sjöprovfiske), SERS (elfiske), KUL (kustprovfiske)
3. GBIF-förekomster inom FVO-omslutning
4. Externa FVOF-sajter (via `URL_FVOF` och länkar som iFiske hänvisar till)

## Köra om

```bash
pip install -r requirements.txt
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_gbif.py
python scripts/enrich_gbif_retry.py
python scripts/enrich_from_external_links.py
python scripts/build_index.py
```
