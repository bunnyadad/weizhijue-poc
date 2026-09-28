"""以標準項目比對方案組合（固定套裝，或基礎＋加選）。"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

from .data import Catalog, Plan
from .parser import Query

STALE_DAYS = 30

# 每個需求項目的判定
YES, INFERRED, CHOICE, REPLACED, NO, UNKNOWN = "有", "推定有", "二選一", "被取代", "沒有", "未確認"
OK = {YES, INFERRED, CHOICE, REPLACED}


@dataclass
class Result:
    parts: list[Plan]
    inst_id: str
    price: int | None
    coverage: dict[str, tuple[str, str]]  # item_id -> (判定, 說明)
    missing_popular: list[str]
    extra_fees: list[str]
    confirmed_at: str
    stale: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "＋".join(p.name for p in self.parts)

    def count(self, *states: str) -> int:
        return sum(1 for s, _ in self.coverage.values() if s in states)


def configs(plans: list[Plan]) -> list[list[Plan]]:
    out = [[p] for p in plans if p.kind == "fixed"]
    bases = [p for p in plans if p.kind == "base"]
    addons = [p for p in plans if p.kind == "addon"]
    for b in bases:
        for n in range(0, min(len(addons), 2) + 1):
            for combo in combinations(addons, n):
                out.append([b, *combo])
    return out


def item_state(parts: list[Plan], iid: str, cat: Catalog) -> tuple[str, str]:
    for p in parts:
        if p.items.get(iid) == "yes":
            return YES, p.name
    for p in parts:
        for group in p.choices:
            if iid in group:
                other = "／".join(cat.item_name(i) for i in group if i != iid)
                return CHOICE, f"與{other}二選一"
    for p in parts:
        for rid, st in p.items.items():
            if st == "yes" and iid in cat.items[rid].replaces:
                return REPLACED, f"由{cat.item_name(rid)}取代"
    for p in parts:
        if iid in p.inferred:
            return INFERRED, "依方案名稱推定，待逐項確認"
    if any(p.items.get(iid) == "no" for p in parts):
        return NO, "官網明列不含"
    return UNKNOWN, "資料尚未確認"


def evaluate(parts: list[Plan], q: Query, cat: Catalog, today: date) -> Result:
    inst = parts[0].inst
    cov = {iid: item_state(parts, iid, cat) for iid in q.required}

    # 同一組二選一只能滿足一個需求
    for p in parts:
        for group in p.choices:
            hit = [i for i in q.required if i in group and cov[i][0] == CHOICE]
            for i in hit[1:]:
                cov[i] = (NO, f"與{cat.item_name(hit[0])}二選一，無法同時做")

    popular = [
        it.id for it in cat.items.values()
        if it.popular and it.id not in q.required and (not it.gender or not q.gender or it.gender == q.gender)
    ]
    missing = [i for i in popular if item_state(parts, i, cat)[0] == NO]

    price = None if any(p.price is None for p in parts) else sum(p.price for p in parts)
    confirmed = min(p.confirmed_at for p in parts)
    stale = (today - date.fromisoformat(confirmed)).days > STALE_DAYS
    fees = [f for p in parts for f in p.extra_fees]
    return Result(parts, inst, price, cov, missing, fees, confirmed, stale)


def search(q: Query, cat: Catalog, today: date | None = None, limit: int = 3):
    today = today or date.today()
    kept, dropped = [], []
    by_inst: dict[str, list[Plan]] = {}
    for p in cat.plans:
        inst = cat.institutions[p.inst]
        if q.city and inst.city != q.city:
            continue
        if q.gender and p.gender not in ("all", q.gender):
            continue
        by_inst.setdefault(p.inst, []).append(p)

    for plans in by_inst.values():
        for parts in configs(plans):
            r = evaluate(parts, q, cat, today)
            if r.count(NO):
                bad = [cat.item_name(i) for i, (s, _) in r.coverage.items() if s == NO]
                r.reasons.append(f"缺少：{'、'.join(bad)}")
            if q.budget and r.price and r.price > q.budget:
                r.reasons.append(f"超出預算（{r.price:,}）")
            (dropped if r.reasons else kept).append(r)

    kept = dedupe(kept)
    if q.cheapest:
        kept.sort(key=lambda r: (r.price is None, r.price or 0))
    else:
        kept.sort(key=lambda r: (r.count(UNKNOWN), r.price is None, r.count(INFERRED), r.price or 0))
    return kept[:limit], kept[limit:], dropped


def dedupe(results: list[Result]) -> list[Result]:
    """同機構只留一個：確認度最高、其次最便宜。"""
    best: dict[str, Result] = {}
    for r in results:
        key = r.inst_id + "|" + ",".join(sorted(p.id for p in r.parts if p.kind == "fixed"))
        cur = best.get(key)
        rank = (r.count(UNKNOWN), r.count(INFERRED), r.price or 10**9)
        if cur is None or rank < (cur.count(UNKNOWN), cur.count(INFERRED), cur.price or 10**9):
            best[key] = r
    return list(best.values())
