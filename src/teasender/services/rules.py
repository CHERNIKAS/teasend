"""Parse a chat's description / pinned text for posting rules.

Returns how often we're allowed to post (min interval in hours) and whether ads
are forbidden outright. Best-effort NLP over common Russian phrasings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that mean "don't advertise here" (posting = ban risk).
_ADS_FORBIDDEN = [
    "реклама запрещена", "запрещена реклама", "реклама строго запрещена",
    "реклама запрещен", "без рекламы", "никакой рекламы", "реклама-запрещена",
    "спам запрещен", "запрещен спам", "без спама", "спам запрещён",
    "объявления запрещены", "запрещены объявления", "запрещена реклама и спам",
    "реклама платная", "платная реклама", "реклама по прайсу", "реклама за деньги",
    "реклама только платно", "реклама платно", "реклама - платно",
    "реклама по согласованию", "по согласованию с админ", "реклама по договоренности",
    "реклама только с разрешения", "реклама с разрешения", "только с разрешения админ",
    "реклама только через админ", "реклама через админ", "реклама только у админ",
    "по вопросам рекламы", "реклама у администрации", "реклама у админа",
    "продажа запрещена", "торговля запрещена", "коммерция запрещена",
    "коммерческая реклама запрещена", "продвижение запрещено",
    "no ads", "ads forbidden",
]

_NUM = {
    "один": 1, "одна": 1, "одного": 1, "одну": 1, "1": 1,
    "два": 2, "две": 2, "2": 2, "три": 3, "3": 3, "четыре": 4, "4": 4,
    "пять": 5, "5": 5, "шесть": 6, "6": 6,
}


@dataclass(slots=True)
class ChatRule:
    min_interval_h: int | None  # minimum hours between our posts (None = unknown)
    ads_forbidden: bool
    note: str  # short human-readable summary


def _num(token: str) -> int | None:
    return _NUM.get(token.strip().lower())


def parse_rules(text: str | None) -> ChatRule:
    if not text:
        return ChatRule(None, False, "")
    t = " ".join(text.lower().split())  # collapse whitespace

    ads = any(p in t for p in _ADS_FORBIDDEN)

    interval: int | None = None
    note = ""

    # "раз в неделю"
    if re.search(r"раз[а]?\s+в\s+недел", t):
        interval, note = 24 * 7, "не чаще 1/неделю"
    # "N раз(а) в день/сутки"
    if interval is None:
        m = re.search(r"(\d+|один|одна|два|две|три|четыре|пять)\s+раз[а]?\s+в\s+(день|сутки)", t)
        if m:
            n = _num(m.group(1)) or 1
            interval = max(1, 24 // max(1, n))
            note = f"не чаще {n}/сутки"
    # "1 пост / одно объявление в день/сутки"
    if interval is None:
        m = re.search(r"(\d+|один|одно|одна)\s+(пост[а]?|объявлени\w+|сообщени\w+)\s+в\s+(день|сутки)", t)
        if m:
            n = _num(m.group(1)) or 1
            interval = max(1, 24 // max(1, n))
            note = f"не чаще {n}/сутки"
    # "раз в сутки / раз в день / один раз в день"
    if interval is None and re.search(r"раз[а]?\s+в\s+(сутки|день)", t):
        interval, note = 24, "не чаще 1/сутки"
    # "раз в N часов" / "не чаще раза в N часов"
    if interval is None:
        m = re.search(r"раз[а]?\s+в\s+(\d+)\s*час", t)
        if m:
            hours = max(1, int(m.group(1)))
            interval, note = hours, f"не чаще 1 в {hours}ч"
    # "раз в N дней"
    if interval is None:
        m = re.search(r"раз[а]?\s+в\s+(\d+)\s*(дн|день|сут)", t)
        if m:
            days = max(1, int(m.group(1)))
            interval, note = days * 24, f"не чаще 1 в {days}д"

    if ads and not note:
        note = "реклама запрещена/по согласованию"
    elif ads:
        note = "реклама ограничена + " + note

    return ChatRule(interval, ads, note)
