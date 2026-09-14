# Hugg – FVO-artlista

**[`data/index.json`](data/index.json)** – en enda indexfil.

## iFiske-spegling
När iFiske har artikoner är `arter` **exakt speglad** mot dem (ingen avvikelse).
Utökad data (SLU/GBIF/Fiskekartan) ligger i `arter_utokad` och `vatten[]`.

| | |
|--|--:|
| Nationellt speglade FVO | 907 |
| Jämtland speglade | 122 / 157 |
| FVO med någon art | 1863 |

## Köra om
```bash
pip install -r requirements.txt
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_gbif.py
python scripts/enrich_gbif_retry.py
python scripts/mirror_ifiske.py
python scripts/match_jamtland_ifiske.py
python scripts/build_index.py
```
