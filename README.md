# Hugg – FVO-artlista

En enda indexfil med alla fiskevårdsområden, arter och vatten:

**[`data/index.json`](data/index.json)**

Byggd med [Fiskekartan](https://fiskekartan.se/) som ram. iFiske används inte som artdata.

## Innehåll i `data/index.json`

| Fält | Beskrivning |
|------|-------------|
| `arter` | Katalog över alla arter |
| `fvo` | Alla FVO med `arter` + `vatten[]` |
| `uppslag.namn` | FVO-namn → id |
| `uppslag.lan` | Län → [id] |
| `uppslag.art` | Art → [fvo-id] |
| `uppslag.vatten` | Vattennamn → träffar med fvo-id och arter |

## Köra om

```bash
pip install -r requirements.txt
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_external_links.py
python scripts/build_index.py
```

Övriga filer under `data/processed/` är mellansteg/statistik för ombyggnad.
