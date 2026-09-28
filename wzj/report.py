"""第 0 階段 Go / No-go 指標。數據不足的一律標「未測」，不猜。"""
import json
from pathlib import Path

from . import watch
from .data import DATA, Catalog

EVAL = DATA / "eval_result.json"


def metrics(cat: Catalog) -> list[dict]:
    rows = []

    ev = json.loads(EVAL.read_text("utf-8")) if EVAL.exists() else None
    if ev and ev.get("plan_error_rate") is not None:
        r = ev["plan_error_rate"]
        verdict = "繼續" if r >= 0.3 else "重新思考" if r < 0.1 else "灰色地帶"
        rows.append(dict(name="通用 AI 錯誤率", value=f"{r:.0%}（{ev['extractor']}，{ev['plans']} 筆）", verdict=verdict))
    else:
        rows.append(dict(name="通用 AI 錯誤率", value="未測", verdict="—",
                         todo="接上 Extractor 或存下 ChatGPT/Perplexity 輸出，跑 `python -m wzj eval --from 檔案`"))

    mins = [p.normalize_minutes for p in cat.plans if p.normalize_minutes is not None]
    if mins:
        avg = sum(mins) / len(mins)
        verdict = "繼續" if avg <= 10 else "重新思考" if avg >= 30 else "灰色地帶"
        rows.append(dict(name="每筆人工標準化時間", value=f"平均 {avg:.1f} 分（{len(mins)} 筆）", verdict=verdict))
    else:
        rows.append(dict(name="每筆人工標準化時間", value="未測", verdict="—",
                         todo="每校對一筆就用 `python -m wzj log-time <plan_id> <分鐘>` 記錄"))

    w = watch.summary(cat)
    if w["days"] >= 14:
        rate = w["changes"] / max(w["runs"] - w["fails"], 1)
        rows.append(dict(name="兩週變動率", value=f"{w['changes']} 次變動 / {w['runs']} 次抓取（{rate:.0%}）",
                         verdict="繼續" if w["changes"] <= len(cat.institutions) else "需評估人力"))
    else:
        rows.append(dict(name="兩週變動率", value=f"觀察中（已 {w['days']} 天，{w['runs']} 次抓取）", verdict="—",
                         todo="每週跑 `python -m wzj watch`，滿 14 天後自動判定"))

    src = json.loads((DATA / "sources.json").read_text("utf-8"))["sources"]
    by = {k: sum(1 for x in src if x.get("category") == k) for k in ("A", "B1", "B2", "C")}
    n = len(src)
    low, high = by["A"] / n, (by["A"] + by["B1"] + by["B2"]) / n
    rows.append(dict(
        name="可自動化來源比例",
        value=f"雙北 {n} 家候選：A {by['A']}、B1 {by['B1']}、B2 {by['B2']}、C {by['C']}；"
              f"只算 A 為 {low:.0%}，B2 在台灣 IP 可抓時最高 {high:.0%}",
        verdict="繼續" if low >= 0.5 else "待台灣 IP 重測" if high >= 0.5 else "重新思考",
        todo="在台灣主機跑 `python -m wzj probe` 重新分類 B2" if by["B2"] else None,
    ))

    plans = cat.plans
    confirmed = sum(len(p.items) for p in plans)
    inferred = sum(len(p.inferred) for p in plans)
    rows.append(dict(
        name="資料完整度（參考）",
        value=f"{len(plans)} 筆方案；已確認欄位 {confirmed}、推定 {inferred}；"
              f"價格未公開 {sum(p.price is None for p in plans)} 筆",
        verdict="—",
    ))
    return rows


def render(rows: list[dict]) -> str:
    out = ["第 0 階段 Go / No-go", ""]
    for r in rows:
        out.append(f"■ {r['name']}：{r['value']}  → {r['verdict']}")
        if r.get("todo"):
            out.append(f"    下一步：{r['todo']}")
    return "\n".join(out)
