"""Small tables of US states, Canadian provinces and common countries, used to unify mixed spellings.

Each table maps a normalised spelling to (abbreviation, full name[, ISO3]); see `lookup`.
"""
from __future__ import annotations

import re

_STATES = (
    "AL Alabama,AK Alaska,AZ Arizona,AR Arkansas,CA California,CO Colorado,CT Connecticut,DE Delaware,"
    "DC District of Columbia,FL Florida,GA Georgia,HI Hawaii,ID Idaho,IL Illinois,IN Indiana,IA Iowa,KS Kansas,"
    "KY Kentucky,LA Louisiana,ME Maine,MD Maryland,MA Massachusetts,MI Michigan,MN Minnesota,MS Mississippi,"
    "MO Missouri,MT Montana,NE Nebraska,NV Nevada,NH New Hampshire,NJ New Jersey,NM New Mexico,NY New York,"
    "NC North Carolina,ND North Dakota,OH Ohio,OK Oklahoma,OR Oregon,PA Pennsylvania,RI Rhode Island,"
    "SC South Carolina,SD South Dakota,TN Tennessee,TX Texas,UT Utah,VT Vermont,VA Virginia,WA Washington,"
    "WV West Virginia,WI Wisconsin,WY Wyoming,PR Puerto Rico"
)
_PROVINCES = (
    "AB Alberta,BC British Columbia,MB Manitoba,NB New Brunswick,NL Newfoundland and Labrador,NS Nova Scotia,"
    "NT Northwest Territories,NU Nunavut,ON Ontario,PE Prince Edward Island,QC Quebec,SK Saskatchewan,YT Yukon"
)
_COUNTRIES = (
    "US USA United States|united states of america|america|us|u s|u s a,CA CAN Canada,MX MEX Mexico,GB GBR United Kingdom|uk|u k|great britain|britain|england,"
    "IE IRL Ireland,FR FRA France,DE DEU Germany|deutschland,ES ESP Spain,IT ITA Italy,PT PRT Portugal,NL NLD Netherlands|holland,"
    "BE BEL Belgium,CH CHE Switzerland,AT AUT Austria,SE SWE Sweden,NO NOR Norway,DK DNK Denmark,FI FIN Finland,PL POL Poland,"
    "GR GRC Greece,TR TUR Turkey,RU RUS Russia,UA UKR Ukraine,CN CHN China,JP JPN Japan,KR KOR South Korea|korea,IN IND India,"
    "PK PAK Pakistan,BD BGD Bangladesh,ID IDN Indonesia,PH PHL Philippines,VN VNM Vietnam,TH THA Thailand,MY MYS Malaysia,"
    "SG SGP Singapore,AU AUS Australia,NZ NZL New Zealand,BR BRA Brazil,AR ARG Argentina,CL CHL Chile,CO COL Colombia,PE PER Peru,"
    "ZA ZAF South Africa,NG NGA Nigeria,EG EGY Egypt,KE KEN Kenya,SA SAU Saudi Arabia,AE ARE United Arab Emirates|uae,IL ISR Israel"
)

_DOTS = re.compile(r"[.,]")


from functools import lru_cache as _memo


@_memo(maxsize=65536)
def _key(value: str) -> str:
    return re.sub(r"\s+", " ", _DOTS.sub(" ", value.lower())).strip()


def _build_states(spec: str, extra: dict[str, str]) -> dict[str, tuple[str, str, str]]:
    table: dict[str, tuple[str, str, str]] = {}
    for item in spec.split(','):
        abbr, name = item.split(' ', 1)
        table[_key(abbr)] = (abbr, name, '')
        table[_key(name)] = (abbr, name, '')
    for alias, abbr in extra.items():
        name = next(n for a, n, _ in list(table.values()) if a == abbr)
        table[_key(alias)] = (abbr, name, '')
    return table


def _build_countries() -> dict[str, tuple[str, str, str]]:
    table: dict[str, tuple[str, str, str]] = {}
    for item in _COUNTRIES.split(','):
        two, three, rest = item.split(' ', 2)
        parts = rest.split('|')
        name = parts[0]
        row = (two, name, three)
        for spelling in (two, three, name, *parts[1:]):
            table[_key(spelling)] = row
    return table


_STATE_ALIASES = {
    'calif': 'CA', 'cal': 'CA', 'penn': 'PA', 'penna': 'PA', 'mass': 'MA', 'wash': 'WA', 'wisc': 'WI', 'minn': 'MN',
    'fla': 'FL', 'tenn': 'TN', 'ore': 'OR', 'ill': 'IL', 'mich': 'MI', 'colo': 'CO', 'conn': 'CT', 'wash dc': 'DC',
    'washington dc': 'DC', 'washington d c': 'DC', 'd c': 'DC', 'n y': 'NY', 'n j': 'NJ', 'n c': 'NC', 's c': 'SC',
}
_PROVINCE_ALIASES = {'que': 'QC', 'ont': 'ON', 'man': 'MB', 'sask': 'SK', 'alta': 'AB', 'nfld': 'NL', 'n s': 'NS', 'n b': 'NB'}

TABLES = {
    'state': _build_states(_STATES + ',' + _PROVINCES, {**_STATE_ALIASES, **_PROVINCE_ALIASES}),
    'country': _build_countries(),
}


def lookup(kind: str, value: str):
    """(abbreviation, full name, ISO3 or '') for a spelling, or None."""
    return TABLES[kind].get(_key(value))
