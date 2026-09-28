"""LLM 抽取介面與評測。

PoC 先不接模型：Extractor 是介面，實作留給之後接 Claude / OpenAI。
評測可以先用「把 LLM 輸出存成 JSON」的方式跑（FileExtractor），與人工校對的 plans.json 逐欄比較。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .data import Catalog, Plan


class Extractor(Protocol):
    name: str

    def extract(self, plan_id: str, page_text: str) -> dict:
        """回傳 {"price": int|None, "items": {item_id: "yes"|"no"}}"""
        ...


class NotConfigured:
    name = "未設定"

    def extract(self, plan_id: str, page_text: str) -> dict:
        raise RuntimeError("尚未接上 LLM，請實作 Extractor 或用 --from 指定已存好的輸出")


class FileExtractor:
    """讀取事先存好的抽取結果：{plan_id: {"price": ..., "items": {...}}}"""

    def __init__(self, path: Path):
        self.name = path.stem
        self.data = json.loads(path.read_text("utf-8"))

    def extract(self, plan_id: str, page_text: str) -> dict:
        return self.data.get(plan_id, {})


@dataclass
class Score:
    plan_id: str
    price_ok: bool | None
    wrong_items: list[str] = field(default_factory=list)
    checked_items: int = 0

    @property
    def has_error(self) -> bool:
        return self.price_ok is False or bool(self.wrong_items)


def score_plan(truth: Plan, got: dict) -> Score:
    price_ok = None
    if truth.price is not None and "price" in got:
        price_ok = got["price"] == truth.price
    s = Score(truth.id, price_ok)
    # 只比人工已確認的欄位；推定與未確認的不算
    for iid, st in truth.items.items():
        s.checked_items += 1
        if got.get("items", {}).get(iid) != st:
            s.wrong_items.append(iid)
    for group in truth.choices:
        for iid in group:
            s.checked_items += 1
            if got.get("items", {}).get(iid) not in ("choice", "yes"):
                s.wrong_items.append(iid)
    return s


def evaluate(cat: Catalog, ex: Extractor, pages: dict[str, str] | None = None) -> dict:
    pages = pages or {}
    scores = []
    for p in cat.plans:
        got = ex.extract(p.id, pages.get(p.id, ""))
        if not got:
            continue
        scores.append(score_plan(p, got))
    n = len(scores)
    err = sum(1 for s in scores if s.has_error)
    fields = sum(s.checked_items + (s.price_ok is not None) for s in scores)
    wrong = sum(len(s.wrong_items) + (s.price_ok is False) for s in scores)
    return {
        "extractor": ex.name,
        "plans": n,
        "plan_error_rate": err / n if n else None,
        "field_error_rate": wrong / fields if fields else None,
        "details": [
            {"plan": s.plan_id, "price_ok": s.price_ok, "wrong_items": [cat.item_name(i) for i in s.wrong_items]}
            for s in scores
        ],
    }
