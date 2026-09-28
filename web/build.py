"""把 data/*.json 注入網頁模板，產出單檔 web/index.html。改資料後重跑即可。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build() -> Path:
    data = {
        "taxonomy": json.loads((ROOT / "data/taxonomy.json").read_text("utf-8")),
        "plans": json.loads((ROOT / "data/plans.json").read_text("utf-8")),
    }
    queries = [l.strip() for l in (ROOT / "data/queries.txt").read_text("utf-8").splitlines() if l.strip()]
    html = (ROOT / "web/template.html").read_text("utf-8")
    html = html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
    html = html.replace("/*QUERIES*/[]", json.dumps(queries, ensure_ascii=False))
    out = ROOT / "web/index.html"
    out.write_text(html, "utf-8")
    return out


if __name__ == "__main__":
    print(build())
