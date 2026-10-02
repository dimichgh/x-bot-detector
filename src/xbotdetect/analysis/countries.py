"""Resolve free-text locations / X's "based in" strings to ISO country codes (best effort)."""

from __future__ import annotations

import re

# code -> aliases (English, native, a few major cities). Lower-case.
_ALIASES: dict[str, tuple[str, ...]] = {
    "US": (
        "united states",
        "united states of america",
        "usa",
        "u.s.a.",
        "u.s.",
        "america",
        "new york",
        "nyc",
        "los angeles",
        "california",
        "texas",
        "florida",
        "washington dc",
        "washington, dc",
        "chicago",
        "san francisco",
        "boston",
        "miami",
        "seattle",
    ),
    "GB": (
        "united kingdom",
        "uk",
        "u.k.",
        "great britain",
        "britain",
        "england",
        "scotland",
        "wales",
        "northern ireland",
        "london",
        "manchester",
        "birmingham",
        "edinburgh",
        "glasgow",
    ),
    "CA": ("canada", "toronto", "montreal", "vancouver", "ottawa"),
    "AU": ("australia", "sydney", "melbourne", "brisbane", "perth"),
    "NZ": ("new zealand", "auckland", "wellington"),
    "IE": ("ireland", "éire", "dublin"),
    "DE": (
        "germany",
        "deutschland",
        "allemagne",
        "германия",
        "німеччина",
        "berlin",
        "münchen",
        "munich",
        "hamburg",
        "köln",
        "cologne",
        "frankfurt",
        "stuttgart",
        "düsseldorf",
        "leipzig",
        "dresden",
        "bayern",
        "bavaria",
        "nrw",
        "берлин",
    ),
    "AT": ("austria", "österreich", "австрия", "wien", "vienna", "graz", "salzburg", "linz"),
    "CH": (
        "switzerland",
        "schweiz",
        "suisse",
        "svizzera",
        "швейцария",
        "zürich",
        "zurich",
        "geneva",
        "genève",
        "bern",
        "basel",
    ),
    "NL": (
        "netherlands",
        "the netherlands",
        "nederland",
        "holland",
        "нидерланды",
        "голландия",
        "amsterdam",
        "rotterdam",
        "den haag",
        "the hague",
        "utrecht",
        "eindhoven",
    ),
    "BE": (
        "belgium",
        "belgië",
        "belgique",
        "бельгия",
        "brussels",
        "bruxelles",
        "brussel",
        "antwerp",
        "antwerpen",
    ),
    "LU": ("luxembourg", "luxemburg"),
    "FR": ("france", "франция", "paris", "париж", "marseille", "lyon", "toulouse", "nice", "bordeaux"),
    "ES": ("spain", "españa", "espana", "испания", "madrid", "barcelona", "valencia", "sevilla", "seville"),
    "PT": ("portugal", "португалия", "lisboa", "lisbon", "porto"),
    "IT": (
        "italy",
        "italia",
        "италия",
        "roma",
        "rome",
        "milano",
        "milan",
        "napoli",
        "naples",
        "torino",
        "turin",
    ),
    "GR": ("greece", "ελλάδα", "ellada", "греция", "athens", "αθήνα", "thessaloniki"),
    "CY": ("cyprus", "кипр", "κύπρος", "nicosia", "limassol"),
    "MT": ("malta",),
    "DK": ("denmark", "danmark", "дания", "copenhagen", "københavn"),
    "SE": ("sweden", "sverige", "швеция", "stockholm", "göteborg", "gothenburg", "malmö"),
    "NO": ("norway", "norge", "норвегия", "oslo", "bergen"),
    "FI": ("finland", "suomi", "финляндия", "helsinki", "helsingfors"),
    "IS": ("iceland", "ísland", "reykjavik"),
    "EE": ("estonia", "eesti", "эстония", "tallinn", "таллин"),
    "LV": ("latvia", "latvija", "латвия", "riga", "rīga", "рига"),
    "LT": ("lithuania", "lietuva", "литва", "vilnius", "вильнюс", "kaunas"),
    "PL": (
        "poland",
        "polska",
        "польша",
        "польща",
        "warszawa",
        "warsaw",
        "варшава",
        "kraków",
        "krakow",
        "wrocław",
        "gdańsk",
        "poznań",
        "łódź",
    ),
    "CZ": (
        "czech republic",
        "czechia",
        "česko",
        "česká republika",
        "чехия",
        "praha",
        "prague",
        "прага",
        "brno",
    ),
    "SK": ("slovakia", "slovensko", "словакия", "bratislava"),
    "HU": ("hungary", "magyarország", "венгрия", "budapest", "будапешт"),
    "SI": ("slovenia", "slovenija", "ljubljana"),
    "HR": ("croatia", "hrvatska", "хорватия", "zagreb", "split"),
    "RS": ("serbia", "srbija", "србија", "сербия", "belgrade", "beograd", "београд", "белград", "novi sad"),
    "BA": ("bosnia", "bosnia and herzegovina", "босния", "sarajevo"),
    "ME": ("montenegro", "crna gora", "черногория", "podgorica"),
    "MK": ("north macedonia", "macedonia", "македонија", "skopje"),
    "AL": ("albania", "shqipëri", "shqiperia", "tirana"),
    "BG": ("bulgaria", "българия", "болгария", "sofia", "софия", "plovdiv", "varna"),
    "RO": ("romania", "românia", "румыния", "bucharest", "bucurești", "cluj"),
    "MD": ("moldova", "молдова", "молдавия", "chișinău", "chisinau", "кишинёв", "кишинев"),
    "UA": (
        "ukraine",
        "україна",
        "украина",
        "kyiv",
        "kiev",
        "київ",
        "киев",
        "kharkiv",
        "харків",
        "харьков",
        "odesa",
        "odessa",
        "одеса",
        "одесса",
        "lviv",
        "львів",
        "львов",
        "dnipro",
        "дніпро",
        "днепр",
        "zaporizhzhia",
        "запоріжжя",
    ),
    "BY": ("belarus", "беларусь", "білорусь", "белоруссия", "minsk", "мінск", "минск"),
    "RU": (
        "russia",
        "russian federation",
        "россия",
        "российская федерация",
        "рф",
        "росія",
        "moscow",
        "москва",
        "saint petersburg",
        "st. petersburg",
        "st petersburg",
        "санкт-петербург",
        "петербург",
        "спб",
        "novosibirsk",
        "новосибирск",
        "екатеринбург",
        "yekaterinburg",
        "казань",
        "kazan",
        "краснодар",
        "нижний новгород",
        "самара",
        "ростов-на-дону",
        "уфа",
        "челябинск",
        "омск",
        "красноярск",
        "пермь",
        "воронеж",
        "волгоград",
        "владивосток",
        "сибирь",
        "siberia",
        "крым",
        "crimea",
        "севастополь",
    ),
    "GE": ("georgia (country)", "sakartvelo", "საქართველო", "грузия", "tbilisi", "тбилиси"),
    "AM": ("armenia", "հայաստան", "армения", "yerevan", "ереван"),
    "AZ": ("azerbaijan", "azərbaycan", "азербайджан", "baku", "баку"),
    "KZ": ("kazakhstan", "қазақстан", "казахстан", "almaty", "алматы", "astana", "астана"),
    "UZ": ("uzbekistan", "oʻzbekiston", "узбекистан", "tashkent", "ташкент"),
    "KG": ("kyrgyzstan", "кыргызстан", "киргизия", "bishkek", "бишкек"),
    "TM": ("turkmenistan", "türkmenistan", "туркменистан", "ashgabat", "ашхабад"),
    "KP": ("north korea", "dprk", "democratic people's republic of korea", "조선", "pyongyang"),
    "MM": ("myanmar", "burma", "မြန်မာ", "yangon", "naypyidaw"),
    "TZ": ("tanzania", "dar es salaam", "dodoma"),
    "TR": ("turkey", "türkiye", "turkiye", "турция", "istanbul", "i̇stanbul", "ankara", "izmir"),
    "IL": ("israel", "ישראל", "израиль", "tel aviv", "jerusalem", "haifa"),
    "PS": ("palestine", "فلسطين", "gaza", "west bank", "ramallah"),
    "LB": ("lebanon", "لبنان", "beirut"),
    "SY": ("syria", "سوريا", "damascus"),
    "IQ": ("iraq", "العراق", "baghdad"),
    "IR": ("iran", "ایران", "иран", "tehran", "تهران"),
    "SA": ("saudi arabia", "السعودية", "riyadh", "jeddah"),
    "AE": ("united arab emirates", "uae", "الإمارات", "dubai", "дубай", "abu dhabi"),
    "QA": ("qatar", "قطر", "doha"),
    "EG": ("egypt", "مصر", "cairo"),
    "MA": ("morocco", "المغرب", "maroc", "casablanca", "rabat"),
    "DZ": ("algeria", "الجزائر", "algérie", "algiers"),
    "TN": ("tunisia", "تونس", "tunisie"),
    "NG": ("nigeria", "lagos", "abuja"),
    "GH": ("ghana", "accra"),
    "KE": ("kenya", "nairobi"),
    "ZA": ("south africa", "johannesburg", "cape town", "durban", "pretoria"),
    "ET": ("ethiopia", "addis ababa"),
    "IN": (
        "india",
        "भारत",
        "bharat",
        "new delhi",
        "delhi",
        "mumbai",
        "bangalore",
        "bengaluru",
        "kolkata",
        "chennai",
        "hyderabad",
    ),
    "PK": ("pakistan", "پاکستان", "karachi", "lahore", "islamabad"),
    "BD": ("bangladesh", "বাংলাদেশ", "dhaka"),
    "LK": ("sri lanka", "colombo"),
    "NP": ("nepal", "kathmandu"),
    "CN": ("china", "中国", "китай", "beijing", "shanghai", "shenzhen", "guangzhou"),
    "HK": ("hong kong", "香港"),
    "TW": ("taiwan", "台灣", "台湾", "taipei"),
    "JP": ("japan", "日本", "япония", "tokyo", "東京", "osaka"),
    "KR": ("south korea", "korea", "대한민국", "한국", "seoul", "서울"),
    "VN": ("vietnam", "việt nam", "hanoi", "ho chi minh"),
    "TH": ("thailand", "ประเทศไทย", "bangkok"),
    "MY": ("malaysia", "kuala lumpur"),
    "SG": ("singapore",),
    "ID": ("indonesia", "jakarta", "bali"),
    "PH": ("philippines", "manila"),
    "BR": ("brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro", "brasília"),
    "AR": ("argentina", "buenos aires"),
    "CL": ("chile", "santiago de chile"),
    "CO": ("colombia", "bogotá", "bogota", "medellín"),
    "PE": ("peru", "perú", "lima"),
    "VE": ("venezuela", "caracas"),
    "MX": ("mexico", "méxico", "ciudad de méxico", "cdmx", "guadalajara", "monterrey"),
    "CU": ("cuba", "havana", "la habana"),
}

# Countries where X is blocked, so ordinary users connect through a VPN and X's "based in" shows
# the VPN's exit country. Mismatches against "based in" are expected there and say little.
# Reflects 2026; override with --x-blocked.
X_BLOCKED_DEFAULT = frozenset({"RU", "CN", "IR", "KP", "TM", "MM", "VE", "TZ"})

_ALIAS_TO_CODE: dict[str, str] = {}
for _code, _names in _ALIASES.items():
    for _n in _names:
        _ALIAS_TO_CODE[_n] = _code
_ALIAS_TO_CODE["georgia"] = "GE"  # X's "based in" uses the country; US state is ambiguous in free text

_NAMES = {code: names[0].title() for code, names in _ALIASES.items()}
_NAMES.update({"US": "United States", "GB": "United Kingdom", "AE": "UAE", "RU": "Russia"})

_SOURCE_SUFFIX = re.compile(
    r"\s+(android app|app store|google play|ios app|web|play store|android|ios|iphone app)\s*$", re.I
)
_FLAG = re.compile("([\U0001f1e6-\U0001f1ff]{2})")
_SPLIT = re.compile(r"[,|/·•;()\[\]]+|\s+-\s+|\s+–\s+")
# Long aliases first so "new york" wins over "york"-like collisions.
_SORTED_ALIASES = sorted(_ALIAS_TO_CODE, key=len, reverse=True)
_WORD_PATTERNS = {a: re.compile(r"(?<![\w])" + re.escape(a) + r"(?![\w])") for a in _SORTED_ALIASES}


def country_name(code: str | None) -> str:
    return _NAMES.get(code or "", code or "?")


def _flag_codes(text: str) -> set[str]:
    out = set()
    for m in _FLAG.findall(text):
        out.add("".join(chr(ord(c) - 0x1F1E6 + ord("A")) for c in m))
    return {"GB" if c == "UK" else c for c in out}


def resolve(text: str | None, strict: bool = False) -> set[str]:
    """Country codes mentioned in ``text``. ``strict`` requires the whole string to be one name
    (used for X's own "based in" / signup-source strings)."""
    if not text:
        return set()
    codes = _flag_codes(text)
    t = text.strip().lower().rstrip(".!")
    if strict:
        t = _SOURCE_SUFFIX.sub("", t).strip()
        code = _ALIAS_TO_CODE.get(t)
        return {code} if code else codes
    if t in _ALIAS_TO_CODE:
        return codes | {_ALIAS_TO_CODE[t]}
    for part in _SPLIT.split(t):
        part = part.strip().rstrip(".!")
        if part in _ALIAS_TO_CODE:
            codes.add(_ALIAS_TO_CODE[part])
    if not codes:
        for alias in _SORTED_ALIASES:
            if len(alias) >= 4 and _WORD_PATTERNS[alias].search(t):
                codes.add(_ALIAS_TO_CODE[alias])
                break
    return codes


def source_country(source: str | None) -> set[str]:
    """``"Russian Federation Android App"`` -> ``{"RU"}``."""
    return resolve(source, strict=True)
