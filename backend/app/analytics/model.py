"""In-memory representation of a project's data and of analysis findings."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .profiling import ADDITIVE_ROLES, ROW_COL, to_period_labels

CONF_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}
CURRENCY_SIGN = {"GBP": "£", "USD": "$", "EUR": "€", "INR": "₹", "JPY": "¥"}

MEASURE_PRIORITY = ["revenue", "roas", "spend", "market_size", "units", "conversions", "market_share", "growth", "margin",
                    "actual", "plan", "clicks", "impressions", "reach", "price", "rating", "cpa", "cpc", "ctr", "cvr", "cpm",
                    "search_volume", "count", "competitor_count", "engagement", "frequency", "measure"]
DIM_PRIORITY = ["category", "product", "channel", "brand", "company", "geography", "campaign", "customer_segment",
                "attribute", "department", "dimension", "scenario"]


# ----------------------------------------------------------------- formatting
def fmt_num(v: float | None, unit: str = "", digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/a"
    if unit == "%":
        return f"{v:,.{digits}f}%"
    if unit == "x":
        return f"{v:,.2f}x"
    sign = CURRENCY_SIGN.get(unit, "")
    a = abs(v)
    if a >= 1e9:
        s = f"{v / 1e9:,.{digits}f}bn"
    elif a >= 1e6:
        s = f"{v / 1e6:,.{digits}f}m"
    elif a >= 1e4:
        s = f"{v / 1e3:,.{digits}f}k"
    elif a >= 100:
        s = f"{v:,.0f}"
    else:
        s = f"{v:,.2f}".rstrip("0").rstrip(".") if a < 10 else f"{v:,.1f}"
    if sign:
        return ("-" if v < 0 else "") + sign + s.lstrip("-")
    return s + (f" {unit}" if unit and unit not in CURRENCY_SIGN else "")


def fmt_pct(p: float | None, signed: bool = True) -> str:
    if p is None or (isinstance(p, float) and (math.isnan(p) or math.isinf(p))):
        return "n/a"
    return f"{p:+.1f}%" if signed else f"{p:.1f}%"


def safe_float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


# ----------------------------------------------------------------- project data
@dataclass
class Column:
    name: str
    dtype: str
    role: str
    unit: str = ""
    additive: bool = False
    distinct: int = 0
    granularity: str = ""
    period_group: str = ""


@dataclass
class DatasetView:
    id: str
    name: str
    file_id: str
    file_name: str
    file_role: str
    period_label: str
    origin: str
    locator: dict
    approximate: bool
    df: pd.DataFrame
    columns: dict[str, Column]
    period_groups: dict = field(default_factory=dict)

    # -------- helpers
    def cols(self, *roles: str, dtype: str | None = None) -> list[str]:
        return [c.name for c in self.columns.values() if c.role in roles and (dtype is None or c.dtype == dtype)]

    def first(self, *roles: str) -> str | None:
        for r in roles:
            for c in self.columns.values():
                if c.role == r:
                    return c.name
        return None

    @property
    def time_col(self) -> str | None:
        for c in self.columns.values():
            if c.role == "time" and c.dtype in ("datetime", "period"):
                return c.name
        return None

    def measures(self, limit: int = 8) -> list[str]:
        ms = [c for c in self.columns.values() if c.dtype == "numeric" and c.role not in ("flag", "time", "identifier")
              and not c.period_group]
        ms.sort(key=lambda c: MEASURE_PRIORITY.index(c.role) if c.role in MEASURE_PRIORITY else 99)
        return [c.name for c in ms[:limit]]

    def dims(self, max_levels: int = 25, min_levels: int = 2, limit: int = 5) -> list[str]:
        ds = [c for c in self.columns.values() if c.dtype in ("categorical", "boolean") and min_levels <= c.distinct <= max_levels]
        ds.sort(key=lambda c: DIM_PRIORITY.index(c.role) if c.role in DIM_PRIORITY else 99)
        return [c.name for c in ds[:limit]]

    def unit(self, col: str) -> str:
        c = self.columns.get(col)
        return c.unit if c else ""

    def role(self, col: str) -> str:
        c = self.columns.get(col)
        return c.role if c else ""

    def additive(self, col: str) -> bool:
        c = self.columns.get(col)
        if not c:
            return False
        return c.additive or (c.role in ADDITIVE_ROLES and c.unit != "%")

    def agg(self, col: str) -> str:
        return "sum" if self.additive(col) else "mean"

    def periods(self) -> pd.Series | None:
        t = self.time_col
        if not t:
            return None
        return to_period_labels(self.df[t], self.columns[t].granularity or "month")

    def source_label(self, columns: list[str] | None = None, rows: list[int] | None = None, filt: dict | None = None) -> str:
        parts = [self.file_name]
        if self.locator.get("sheet"):
            parts.append(f"sheet '{self.locator['sheet']}'")
        if self.locator.get("page"):
            parts.append(f"page {self.locator['page']}")
        if self.locator.get("slide"):
            parts.append(f"slide {self.locator['slide']}")
        if self.locator.get("table") and self.origin in ("pdf_table", "docx_table", "md_table"):
            parts.append(f"table {self.locator['table']}")
        if rows:
            rs = sorted(set(int(r) for r in rows))
            parts.append(f"rows {rs[0]}–{rs[-1]}" if len(rs) > 1 else f"row {rs[0]}")
        if columns:
            parts.append("columns " + ", ".join(columns[:6]))
        if filt:
            parts.append("where " + ", ".join(f"{k} = {v}" for k, v in filt.items()))
        return " › ".join(parts)

    def evidence(self, columns: list[str], mask: pd.Series | None = None, excerpt: str = "", values: dict | None = None,
                 filt: dict | None = None) -> "Evidence":
        sub = self.df if mask is None else self.df[mask]
        rows = sub[ROW_COL].dropna().astype(int).tolist() if ROW_COL in sub.columns else []
        loc = {k: v for k, v in self.locator.items() if k in ("sheet", "page", "slide", "table", "file", "image", "method")}
        if rows:
            loc["rows"] = f"{min(rows)}–{max(rows)}" if len(rows) > 1 else str(rows[0])
            loc["row_count"] = len(rows)
            loc["row_sample"] = sorted(rows)[:12]
        loc["columns"] = columns
        if filt:
            loc["filter"] = filt
        return Evidence(source_label=self.source_label(columns, rows, filt), file_id=self.file_id, dataset_id=self.id,
                        locator=loc, excerpt=excerpt, values=values or {}, approximate=self.approximate)


@dataclass
class Chunk:
    id: str
    file_id: str
    kind: str
    heading: str
    text: str
    locator: dict
    approximate: bool


@dataclass
class DocumentView:
    file_id: str
    name: str
    role: str
    period_label: str
    file_type: str
    order: int
    chunks: list[Chunk]

    def statements(self, min_len: int = 20) -> list[Chunk]:
        return [c for c in self.chunks if c.kind not in ("heading", "slide_title") and len(c.text) >= min_len]

    def chunk_label(self, ch: Chunk) -> str:
        parts = [self.name]
        loc = ch.locator
        if loc.get("page"):
            parts.append(f"page {loc['page']}")
        if loc.get("slide"):
            parts.append(f"slide {loc['slide']}")
        if loc.get("section"):
            parts.append(f"section '{loc['section']}'")
        if loc.get("paragraph") and not loc.get("page"):
            parts.append(f"paragraph {loc['paragraph']}")
        if loc.get("line"):
            parts.append(f"line {loc['line']}")
        if loc.get("lines"):
            parts.append(f"lines {loc['lines']}")
        if loc.get("part"):
            parts.append(loc["part"])
        return " › ".join(parts)

    def evidence(self, ch: Chunk, excerpt: str | None = None) -> "Evidence":
        return Evidence(source_label=self.chunk_label(ch), file_id=self.file_id, chunk_id=ch.id,
                        locator={**ch.locator, "kind": ch.kind}, excerpt=(excerpt or ch.text)[:600], approximate=ch.approximate)


@dataclass
class ProjectData:
    project: dict
    datasets: list[DatasetView]
    documents: list[DocumentView]
    files: list[dict]
    quality_issues: list[dict]

    @property
    def focus(self) -> str:
        return (self.project.get("focus_entity") or "").strip()

    def all_dimension_values(self) -> dict[str, set]:
        """Map lower-cased dimension value -> set of (dataset_id, column)."""
        out: dict[str, set] = {}
        for ds in self.datasets:
            for c in ds.columns.values():
                if c.dtype == "categorical" and 1 <= c.distinct <= 500:
                    for v in ds.df[c.name].dropna().unique():
                        sv = str(v).strip()
                        if 2 <= len(sv) <= 60:
                            out.setdefault(sv.lower(), set()).add((ds.id, c.name))
        return out

    def dataset(self, ds_id: str) -> DatasetView | None:
        return next((d for d in self.datasets if d.id == ds_id), None)


# ----------------------------------------------------------------- findings
@dataclass
class Evidence:
    source_label: str
    file_id: str | None = None
    dataset_id: str | None = None
    chunk_id: str | None = None
    locator: dict = field(default_factory=dict)
    excerpt: str = ""
    values: dict = field(default_factory=dict)
    approximate: bool = False

    def to_dict(self) -> dict:
        return {"source_label": self.source_label, "file_id": self.file_id, "dataset_id": self.dataset_id,
                "chunk_id": self.chunk_id, "locator": self.locator, "excerpt": self.excerpt, "values": self.values,
                "is_approximate": self.approximate}


@dataclass
class Finding:
    category: str  # trend | change | comparison | gap | opportunity | risk | marketing | roas | strategy | impact | anomaly | relationship | concentration | reported | contradiction | quality | product | plan_vs_actual
    title: str
    fact: str
    interpretation: str = ""
    implication: str = ""
    hypothesis: str = ""
    validation: str = ""
    confidence: str = "medium"
    evidence: list[Evidence] = field(default_factory=list)
    magnitude: float = 0.3  # 0..1, size of the effect
    impact: float = 0.5  # 0..1, business importance of the metric/topic
    tags: list[str] = field(default_factory=list)  # intents and entity names (for question matching)
    chart: dict | None = None
    details: dict = field(default_factory=dict)
    approximate: bool = False
    key: str = ""  # de-duplication key
    section: str = ""  # results section override
    records: dict = field(default_factory=dict)  # typed records: trend/comparison/gap/opportunity/risks/recommendations/research
    score: float = 0.0
    rank: int = 0
    id: str = ""

    def __post_init__(self):
        if not self.key:
            self.key = hashlib.sha1((self.category + self.title).encode()).hexdigest()[:16]
        if any(e.approximate for e in self.evidence):
            self.approximate = True
        if self.approximate and self.confidence == "high":
            self.confidence = "medium"


def to_list(v: Any) -> list:
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    return [v]
