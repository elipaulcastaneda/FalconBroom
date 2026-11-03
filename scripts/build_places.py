"""Rebuild fbroom/data/places.bin from GeoNames (CC BY 4.0, https://www.geonames.org).

    python scripts/build_places.py

Keeps every populated place of 5,000+ people (plus capitals), as normalized names
(see fbroom.semantics.normalize_place), zlib-compressed, one name per line.
"""
import io
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fbroom.semantics import normalize_place  # noqa: E402

URL = "https://download.geonames.org/export/dump/"
OUT = ROOT / "fbroom" / "data" / "places.bin"


def fetch(name: str) -> bytes:
    return urllib.request.urlopen(URL + name, timeout=180).read()


def main() -> int:
    with zipfile.ZipFile(io.BytesIO(fetch("cities5000.zip"))) as archive:
        text = archive.read("cities5000.txt").decode("utf-8")
    names: set[str] = set()
    codes: set[str] = set()
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 8:
            continue
        for name in (cols[1], cols[2]):
            key = normalize_place(name)
            if len(key) >= 3:
                names.add(key)
    for line in fetch("countryInfo.txt").decode("utf-8").splitlines():
        cols = line.split("\t")
        if line.startswith("#") or len(cols) < 5:
            continue
        codes.update((cols[0], cols[1]))
        names.add(normalize_place(cols[4]))
    for line in fetch("admin1CodesASCII.txt").decode("utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        for name in cols[1:3]:
            key = normalize_place(name)
            if len(key) >= 3:
                names.add(key)
    lines = sorted(names) + ["#" + code for code in sorted(codes)]
    payload = zlib.compress("\n".join(lines).encode("utf-8"), 9)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(payload)
    print(f"{len(names)} names, {len(codes)} codes -> {OUT} ({len(payload) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
