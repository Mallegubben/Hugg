#!/usr/bin/env python3
"""Ladda ner rådata som behövs för build_fvo_artlista.py."""

from __future__ import annotations

import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

FVO_ZIP = "https://ext-dokument.lansstyrelsen.se/gemensamt/geodata/ShapeExport/lstext.fiskekartan_fvof.zip"
NORS = "https://dvfisk.slu.se/api/V1/nors/data-aggregerad/rapport"
SERS = "https://dvfisk.slu.se/api/V1/sers/data-aggregerad/rapport"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Hämtar {url}")
    urllib.request.urlretrieve(url, dest)
    print(f"  -> {dest} ({dest.stat().st_size} bytes)")


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    zip_path = RAW / "fiskekartan_fvof.zip"
    download(FVO_ZIP, zip_path)
    out_dir = RAW / "fiskekartan_fvof"
    out_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)

    download(NORS, RAW / "nors_data_aggregerad.json")
    download(SERS, RAW / "sers_data_aggregerad.json")
    print("Klart.")


if __name__ == "__main__":
    main()
