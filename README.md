# Hugg – FVO-artlista

Komplett artunderlag för Sveriges fiskevårdsområden (FVO), med
[Fiskekartan](https://fiskekartan.se/) som ram.

## Resultat

| Mått | Värde |
|------|------:|
| FVO totalt | 1907 |
| FVO med artlista | 1529 |
| FVO utan känd art | 378 |
| FVO med Fiskekartan-arter | 994 |
| FVO med SLU-survey | 1194 |
| FVO berikade via FVOF-sajter | 52 |
| Vattenposter (sjö/vattendrag med survey) | 6291 |
| Unika arter i katalogen | 85 |

## Dataset

| Fil | Innehåll |
|-----|----------|
| `data/processed/fvo_artlista.json` | Full artlista per FVO + vattenposter (NORS/SERS) |
| `data/processed/fvo_komplett_index.json` | Komplett index: FVO + vatten + uppslag (art/län/namn/vatten) |
| `data/processed/fvo_artlista_index.json` | Kompakt index för appar |
| `data/processed/fvo_artlista.csv` | Tabellöversikt |
| `data/processed/fvo_tackning_per_lan.csv` | Täckning per län |
| `data/processed/summary.json` | Aggregerad statistik |

Varje FVO innehåller:

- `arter` – union av kända arter för området
- `fiskekartan` – artfält direkt från Fiskekartan (`VANL_ART` / `OVRI_ART`)
- `vatten[]` – sjöar/vattendrag med survey-arter (SLU NORS/SERS), när punkten faller inom FVO-geometrin
- länkar till Fiskekartan / FVOF-sida

## Källor

1. **Fiskekartan** – FVO-katalog + artattribut
2. **SLU NORS** – sjöprovfiske (sjö-nivå)
3. **SLU SERS** – elfiske (vattendrag-nivå)
4. **Externa FVOF-sajter** – via `URL_FVOF` och externa länkar som iFiske *hänvisar till*

**iFiske används aldrig som artdata.**

## Köra om

```bash
pip install -r requirements.txt
python scripts/fetch_raw_data.py
python scripts/build_fvo_artlista.py
python scripts/enrich_from_external_links.py
python scripts/build_komplett_index.py
```

Rågeometri (`data/raw/fiskekartan_fvof/`) är gitignorerad p.g.a. storlek.
