# Third-party data notices

## GeoNames place names

`fbroom/data/places.bin` is a compressed list of place, region and country names derived from
[GeoNames](https://www.geonames.org) (cities with 5,000+ people, country and first-level administrative division names),
licensed under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
It is used only to recognise that a value looks like a place name. Rebuild it with `python scripts/build_places.py`.

## Given names and surnames

`fbroom/data/names.bin` is a compressed list of common US given names (U.S. Social Security Administration baby-name data) and surnames (U.S. Census Bureau 2010 surname list), both U.S. government public-domain data. It is used only to recognise that a value looks like a person's name. Rebuild it with `python scripts/build_names.py`.
