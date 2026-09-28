"""把一句需求解析成結構化條件。PoC 用規則式，之後可換成 LLM（輸出同一個 Query）。"""
import re
from dataclasses import dataclass, field

from .data import Catalog

CITIES = {"台北": "台北", "臺北": "台北", "北市": "台北", "新北": "新北"}


@dataclass
class Query:
    text: str
    city: str | None = None
    budget: int | None = None
    gender: str | None = None
    required: list[str] = field(default_factory=list)
    cheapest: bool = False


def parse_budget(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*萬(\d)?", text)
    if m:
        # 「3萬5」= 35000
        tail = int(m.group(2)) * 1000 if m.group(2) else 0
        return int(float(m.group(1)) * 10000) + tail
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,6})\s*(?:元|塊)?\s*(?:內|以內|以下|左右)?", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def parse_gender(text: str) -> str | None:
    if re.search(r"女|婦", text):
        return "F"
    if re.search(r"男|先生", text):
        return "M"
    return None


def parse(text: str, cat: Catalog) -> Query:
    q = Query(text=text)
    for k, v in CITIES.items():
        if k in text:
            q.city = v
            break
    q.budget = parse_budget(text)
    q.gender = parse_gender(text)
    q.cheapest = bool(re.search(r"最便宜|便宜|省錢|最低價", text))

    lowered = text.lower()
    # 同義詞長的先比，避免「全大腸鏡」被拆成「大腸鏡」重複計算
    pairs = sorted(
        ((syn, it.id) for it in cat.items.values() for syn in [it.name, *it.synonyms]),
        key=lambda p: -len(p[0]),
    )
    taken = [False] * len(text)
    found: dict[str, int] = {}
    for syn, iid in pairs:
        start = lowered.find(syn.lower())
        while start != -1:
            span = range(start, start + len(syn))
            if not any(taken[i] for i in span):
                for i in span:
                    taken[i] = True
                found.setdefault(iid, start)
            start = lowered.find(syn.lower(), start + 1)
    for need, spec in cat.needs.items():
        pos = text.find(need)
        if pos != -1:
            for i in spec["required"]:
                found.setdefault(i, pos)
    q.required = sorted(found, key=found.get)
    return q
