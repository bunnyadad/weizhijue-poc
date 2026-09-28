import json
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Item:
    id: str
    name: str
    cat: str
    synonyms: list[str]
    gender: str | None = None
    popular: bool = False
    replaces: list[str] = field(default_factory=list)


@dataclass
class Institution:
    id: str
    name: str
    city: str
    source_url: str
    source_format: str
    booking: dict
    notes: str = ""


@dataclass
class Plan:
    id: str
    inst: str
    name: str
    kind: str  # fixed | base | addon
    gender: str  # M | F | all
    price: int | None
    items: dict[str, str]
    confirmed_at: str
    inferred: list[str] = field(default_factory=list)
    choices: list[list[str]] = field(default_factory=list)
    extra_fees: list[str] = field(default_factory=list)
    normalize_minutes: float | None = None
    notes: str = ""


@dataclass
class Catalog:
    items: dict[str, Item]
    needs: dict[str, dict]
    institutions: dict[str, Institution]
    plans: list[Plan]

    def item_name(self, item_id: str) -> str:
        return self.items[item_id].name if item_id in self.items else item_id


def load(data_dir: Path = DATA) -> Catalog:
    tax = json.loads((data_dir / "taxonomy.json").read_text("utf-8"))
    raw = json.loads((data_dir / "plans.json").read_text("utf-8"))
    items = {d["id"]: Item(**d) for d in tax["items"]}
    insts = {d["id"]: Institution(**d) for d in raw["institutions"]}
    plans = [Plan(**d) for d in raw["plans"]]
    for p in plans:
        unknown = [i for i in [*p.items, *p.inferred, *sum(p.choices, [])] if i not in items]
        if unknown:
            raise ValueError(f"{p.id} 使用未定義的標準項目：{unknown}")
        if p.inst not in insts:
            raise ValueError(f"{p.id} 對應不到機構 {p.inst}")
    return Catalog(items, tax.get("needs", {}), insts, plans)
