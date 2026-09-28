# 微直覺 PoC

Proposal v0.2「第 0 階段：資料標準化驗證」的最小可跑版本。場景：台北、腸胃相關自費健檢方案。

純 Python 3.11 標準函式庫，不需安裝任何套件。

## 用法

```bash
# 一句話搜尋
python -m wzj search "台北、4 萬元內、想做無痛胃鏡加大腸鏡，女性。"
python -m wzj search "台北 女 胃鏡大腸鏡 乳房超音波和乳房攝影都要" -v   # -v 顯示被排除的原因

# Go / No-go 指標
python -m wzj report

# 變動偵測（每週跑，滿 14 天自動判定）
python -m wzj watch

# 記錄人工標準化耗時
python -m wzj log-time mj-gi 12

# 10 個需求句的對照測試表（填入 ChatGPT／Perplexity 回答後人工評分）
python -m wzj bench --out bench.csv

# LLM 抽取錯誤率（先把 LLM 輸出存成 JSON）
python -m wzj eval --from llm_output.json

# 測試
python -m unittest -v
```

## 網頁版

```bash
python web/build.py        # 由 data/*.json 產生 web/index.html
open web/index.html        # 直接用瀏覽器開，不需要伺服器
```

網頁版的解析與比對邏輯與 Python 版相同（`web/template.html`），改資料後重跑 build 即可。

## 結構

| 路徑 | 內容 |
|---|---|
| `data/taxonomy.json` | 標準項目 v0（37 項），含同義詞、性別限定、熱門標記、取代關係 |
| `data/plans.json` | 5 家機構、10 筆方案（官網抽樣，2026-09-28） |
| `data/queries.txt` | 10 個需求句 |
| `wzj/parser.py` | 一句話 → 地區／預算／性別／必要項目（規則式，可換 LLM） |
| `wzj/match.py` | 方案組合（固定套裝、基礎＋加選）、二選一、取代、另計費用、排序 |
| `wzj/extract.py` | LLM 抽取介面（未接模型）＋逐欄評分 |
| `wzj/watch.py` | 抓取、正規化、價格指紋比對、變動紀錄 |
| `wzj/report.py` | 第 0 階段四個指標，數據不足一律標「未測」 |
| `web/` | 單檔網頁版（模板＋build 腳本） |

## 資料判定

每個項目有六種狀態：有、推定有（依方案名稱推定，待確認）、二選一、被取代（例如 LDCT 取代胸部 X 光）、沒有（官網明列不含）、未確認。
排序時「未確認」與「推定」會排在已確認的方案後面；官網明列不含必要項目的組合直接排除。

## 接 LLM

實作 `wzj/extract.py` 的 `Extractor` 介面：

```python
class ClaudeExtractor:
    name = "claude"
    def extract(self, plan_id: str, page_text: str) -> dict:
        # 回傳 {"price": 39600, "items": {"egd": "yes", "ca199": "no", "breast_us": "choice"}}
        ...
```

評分只比對人工已確認的欄位，推定與未確認的不計分。

詳細結論見 [FEASIBILITY.md](FEASIBILITY.md)。
