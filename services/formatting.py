"""
Утилиты форматирования — shared между хендлерами.
"""

from datetime import datetime
from html import escape
from urllib.parse import urlsplit


def h(value: object) -> str:
    """HTML-escape любого значения."""
    return escape(str(value) if value is not None else "", quote=True)


def safe_url(value: str | None) -> str | None:
    """Возвращает URL только если схема http/https, иначе None."""
    if not value or not isinstance(value, str):
        return None
    try:
        p = urlsplit(value.strip())
        if p.scheme in ("http", "https") and p.netloc:
            return value.strip()
    except Exception:
        pass
    return None


def youtube_thumb(video_url: str | None) -> str | None:
    """Извлекает превью ролика YouTube в хорошем качестве."""
    if not video_url or not isinstance(video_url, str):
        return None
    url = video_url.strip()
    vid = None
    if "youtu.be/" in url:
        vid = url.split("youtu.be/")[1].split("?")[0].split("&")[0]
    elif "watch?v=" in url:
        vid = url.split("watch?v=")[1].split("&")[0].split("#")[0]
    elif "/embed/" in url:
        vid = url.split("/embed/")[1].split("?")[0]
    if vid and len(vid) >= 6:
        return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
    return None


COUNTRY_NAME_TO_ISO: dict[str, str] = {
    "united-states": "US", "united states": "US", "usa": "US",
    "russia": "RU", "russian federation": "RU",
    "spain": "ES", "canada": "CA", "south-korea": "KR", "south korea": "KR", "korea": "KR",
    "united-kingdom": "GB", "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "ukraine": "UA", "finland": "FI", "australia": "AU", "poland": "PL",
    "germany": "DE", "france": "FR", "brazil": "BR", "kazakhstan": "KZ",
    "new-zealand": "NZ", "new zealand": "NZ", "romania": "RO", "chile": "CL",
    "portugal": "PT", "armenia": "AM", "mexico": "MX", "italy": "IT",
    "moldova": "MD", "norway": "NO", "argentina": "AR", "israel": "IL",
    "sweden": "SE", "denmark": "DK", "belgium": "BE", "netherlands": "NL",
    "singapore": "SG", "japan": "JP", "china": "CN", "india": "IN",
    "philippines": "PH", "turkey": "TR", "czech-republic": "CZ", "czechia": "CZ",
    "slovakia": "SK", "hungary": "HU", "austria": "AT", "switzerland": "CH",
    "taiwan": "TW", "thailand": "TH", "vietnam": "VN", "indonesia": "ID",
    "malaysia": "MY", "south-africa": "ZA", "south africa": "ZA",
    "egypt": "EG", "nigeria": "NG", "peru": "PE", "colombia": "CO",
    "belarus": "BY", "georgia": "GE", "greece": "GR", "ireland": "IE",
}


def country_code(country: str | None) -> str:
    """Возвращает ISO-код страны или пустую строку."""
    if not country:
        return ""
    val = str(country).strip()
    key = val.lower().replace("_", "-")

    if len(key) == 2 and key.isascii() and key.isalpha():
        return key.upper()

    code = COUNTRY_NAME_TO_ISO.get(key) or COUNTRY_NAME_TO_ISO.get(key.replace("-", " "))
    return code or ""


def country_flag(country: str | None) -> str:
    """Возвращает эмодзи флага страны или пустую строку."""
    code = country_code(country)
    if not code or len(code) != 2:
        return ""
    return "".join(chr(127397 + ord(c.upper())) for c in code)


def country_display_with_flag(country: str | None) -> str:
    """Возвращает форматированную строку с флагом, например: '🇷🇺 Russia'."""
    if not country:
        return "Не указана"
    c_name = str(country).replace("-", " ").title()
    flag = country_flag(country)
    if flag:
        return f"{flag} {c_name}"
    return c_name


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except (ValueError, TypeError, AttributeError):
        return str(iso)


def record_rank(record: dict) -> int:
    """Ключ сортировки: чем меньше placement в топе, тем хардестнее."""
    lvl = record.get("level") or {}
    placement = lvl.get("placement")
    if placement is None:
        return 999999
    try:
        return int(placement)
    except (ValueError, TypeError):
        return 999999


def validate_username(username: str) -> bool:
    if not username:
        return False
    clean = username.strip()
    return (
        bool(clean)
        and clean.replace("_", "").replace("-", "").isalnum()
        and len(clean) <= 32
    )
