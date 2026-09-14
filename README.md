# Hugg – FVO-artlista

Komplett artunderlag för Sveriges fiskevårdsområden (FVO), med
[Fiskekartan](https://fiskekartan.se/) som ram.

## Dataset

| Fil | Innehåll |
|-----|----------|
| `data/processed/fvo_artlista.json` | Full artlista per FVO + vattenposter (NORS/SERS) |
| `data/processed/fvo_artlista_index.json` | Kompakt index för appar |
| `data/processed/fvo_artlista.csv` | Tabellöversikt |

Varje FVO innehåller:

- `arter` – union av kända arter för området
- `fiskekartan` – artfält direkt från Fiskekartan (`VANL_ART` / `OVRI_ART`)
- `vatten[]` – sjöar/vattendrag med survey-arter (SLU NORS/SERS), när punkten faller inom FVO-geometrin
- länkar till Fiskekartan / FVOF-sida (iFiske används **inte** som artdata)

## Källor

1. **Fiskekartan** – FVO-katalog + artattribut  
2. **SLU NORS** – sjöprovfiske (sjö-nivå)  
3. **SLU SERS** – elfiske (vattendrag-nivå)  
4. **Externa FVOF-sajter** – via `URL_FVOF` och externa länkar som iFiske *hänvisar till* (aldrig arttext från iFiske)

## Köra om

```bash
pip install geopandas shapely pyproj pandas tqdm beautifulsoup4 lxml requests
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_external_links.py
```

Rågeometri (`data/raw/fiskekartan_fvof/`) är gitignorerad p.g.a. storlek.
