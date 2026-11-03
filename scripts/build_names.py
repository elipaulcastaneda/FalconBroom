"""Rebuild fbroom/data/names.bin: common first names and surnames, zlib-compressed.

    python scripts/build_names.py

First names: US baby-name data derived from the Social Security Administration (public domain), mirrored at
github.com/hadley/data-baby-names. Surnames: US Census Bureau 2010 surname list (public domain).
Each line is 'f<name>' or 'l<name>', lowercase and accent-free.
"""
import csv
import io
import urllib.request
import zipfile
import zlib
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fbroom" / "data" / "names.bin"
BABY = "https://raw.githubusercontent.com/hadley/data-baby-names/master/baby-names.csv"
CENSUS = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
MIN_YEARS = 8
MIN_SURNAME_COUNT = 6000


def fetch(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=300).read()


def main() -> int:
    years: dict[str, set] = defaultdict(set)
    for row in csv.DictReader(io.StringIO(fetch(BABY).decode("utf-8"))):
        years[row["name"].casefold()].add(row["year"])
    first = {name for name, seen in years.items() if len(seen) >= MIN_YEARS and name.isalpha()}
    with zipfile.ZipFile(io.BytesIO(fetch(CENSUS))) as archive:
        text = archive.read("Names_2010Census.csv").decode("latin-1")
    last = set()
    for row in csv.DictReader(io.StringIO(text)):
        count = row["count"]
        if count.isdigit() and int(count) >= MIN_SURNAME_COUNT and row["name"].isalpha():
            last.add(row["name"].casefold())
    lines = sorted("f" + n for n in first) + sorted("l" + n for n in last)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(zlib.compress("\n".join(lines).encode("utf-8"), 9))
    print(f"{len(first)} first names, {len(last)} surnames, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
