"""Normalisation, column profiling, semantic-role detection and data-quality checks.

The original data is never changed. ``normalize_table`` returns a *copy* with
parsed numbers/dates and harmonised labels, plus a log of every transformation.
"""
from __future__ import annotations

import re
import warnings
from collections import Counter
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

ROW_COL = "__row__"

# ----------------------------------------------------------------- semantic roles
# Order matters: first match wins. Patterns are matched against a lower-cased, space-separated column name.
ROLE_PATTERNS: list[tuple[str, str]] = [
    ("roas", r"\broas\b|return on ad spend"),
    ("ctr", r"\bctr\b|click[- ]?through"),
    ("cpc", r"\bcpc\b|cost per click"),
    ("cpm", r"\bcpm\b|cost per (mille|thousand)"),
    ("cpa", r"\bcpa\b|cost per (acquisition|conversion|order|lead|sale)"),
    ("cvr", r"\bcvr\b|conversion rate"),
    ("frequency", r"\bfrequency\b"),
    ("growth", r"growth|\byoy\b|y/y|change %|% change|cagr"),
    ("market_share", r"share"),
    ("plan", r"\b(plan|planned|target|budget|forecast|goal|objective)\b"),
    ("actual", r"\bactual(s)?\b|achieved"),
    ("spend", r"spend|\bcost\b|media cost|investment|expenditure|\bad cost"),
    ("impressions", r"impression|\bimps?\b|views"),
    ("reach", r"\breach\b"),
    ("clicks", r"\bclicks?\b"),
    ("conversions", r"conversion|\borders?\b|purchases?|\bleads?\b|sign[- ]?ups?|transactions?"),
    ("engagement", r"engagement|likes|shares|comments"),
    ("market_size", r"market size|category size|market value|\btam\b"),
    ("revenue", r"revenue|\bsales\b|sales value|turnover|\bgmv\b|income|value sales|net sales|gross sales|bookings"),
    ("units", r"\bunits?\b|volume|quantity|\bqty\b|units sold"),
    ("price", r"price|\brrp\b|\basp\b|\bmsrp\b"),
    ("margin", r"margin|profit"),
    ("rating", r"rating|stars|\bnps\b|satisfaction|\bscore\b|reviews? score"),
    ("competitor_count", r"(number|no\.?|count|#) of (competitors|brands|products|players)|competitor count|competitors"),
    ("count", r"\bcount\b|number of|headcount|\bfte\b|employees"),
    ("search_volume", r"search volume|searches"),
]
DIM_PATTERNS: list[tuple[str, str]] = [
    ("time", r"^(date|day|week|month|quarter|year|period|fy|fiscal year|time|reporting period|month year)$|\b(date|month|quarter|year|period)\b"),
    ("campaign", r"campaign|ad set|adset|creative"),
    ("keyword", r"keyword|search term|search query|\bquer(y|ies)\b"),
    ("device", r"\bdevices?\b|device type|operating system|\bbrowser\b"),
    ("channel", r"channel|platform|media|medium|\bsource\b|network|publisher|touchpoint"),
    ("customer_segment", r"customer|audience|persona|age|gender|demographic|cohort|shopper"),
    ("company", r"competitor|company|manufacturer|vendor|supplier|player|owner|advertiser|retailer"),
    ("brand", r"\bbrand\b"),
    ("product", r"product|\bsku\b|\bitem\b|\bline\b|offering|\bservice\b|model|\bname\b"),
    ("category", r"categor|segment|sub[- ]?cat|\btype\b|class|need state|\bgroup\b|portfolio|range|tier|price band"),
    ("geography", r"region|country|market|city|state|territory|\bgeo\b|area|location|province|zone"),
    ("attribute", r"format|flavou?r|claim|ingredient|pack|size|\bform\b|variant|benefit|feature|colour|color|material"),
    ("department", r"department|team|function|division|business unit"),
    ("scenario", r"scenario|version|plan vs|actual vs|\bstatus\b"),
]
DATE_ATTRIBUTE_RE = re.compile(r"launch|start|end|birth|created|opened|joined|due|expir|founded|hire|listing|introduc")
YEAR_TOKEN_RE = re.compile(r"(?<!\d)(?:fy\s?)?((?:19|20)\d{2})(?!\d)", re.I)
PERIOD_HEADER_RE = re.compile(
    r"^\s*(?:fy\s?)?(?:19|20)\d{2}\s*$|^\s*q[1-4][\s\-']*(?:19|20)?\d{2}\s*$|^\s*(?:19|20)\d{2}[\s\-]*q[1-4]\s*$"
    r"|^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?[\s\-'/.,]*(?:19|20)?\d{2}\s*$"
    r"|^\s*(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])(?:[-/.]0?1)?\s*$|^\s*(?:0?[1-9]|1[0-2])[-/.](?:19|20)\d{2}\s*$", re.I)
TOTAL_COL_RE = re.compile(r"^\s*(grand\s+)?(total|totals|sum|ytd|fytd|full\s+year|annual|fy\s+total|year\s+total|average|avg)\s*$", re.I)
TOTAL_ROW_RE = re.compile(r"^\s*((grand|sub)[\s-]*)?totals?\s*:?\s*$|^.{1,60}\s(sub[\s-]?)?totals?\s*:?\s*$|^totals?\s+(for|of|all)\b"
                          r"|^(overall|all\s+(regions|products|stores|items|countries|channels))\s*$", re.I)
ADDITIVE_ROLES = {"spend", "revenue", "units", "impressions", "reach", "clicks", "conversions", "engagement", "count",
                  "market_size", "search_volume", "plan", "actual", "competitor_count", "measure"}
RATE_ROLES = {"roas", "ctr", "cpc", "cpm", "cpa", "cvr", "frequency", "growth", "market_share", "price", "margin", "rating"}
CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY"}
CURRENCY_CODES = {"usd", "gbp", "eur", "inr", "aud", "cad", "jpy", "cny", "sgd", "nzd", "chf", "rs", "rs."}


@dataclass
class ColumnMeta:
    name: str  # normalised display name used for analysis
    original_name: str
    position: int
    dtype: str  # numeric | datetime | period | categorical | text | identifier | boolean | empty
    role: str = "unknown"
    unit: str = ""  # %, currency code, or ''
    additive: bool = False
    missing: int = 0
    distinct: int = 0
    stats: dict = field(default_factory=dict)
    period_granularity: str = ""  # year | quarter | month | week | day

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------- value parsing
_NUM_CLEAN = re.compile(r"[\s,]")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_NUM_RE = re.compile(
    r"^(?P<neg>[\(\-−–])?\s*(?P<cur>[$£€₹¥]|usd|gbp|eur|inr|aud|cad|rs\.?)?\s*(?P<num>\d+(?:\.\d+)?|\.\d+)\s*"
    r"(?P<scale>k|thousand|mn|mm|m|million|bn|b|billion)?\s*(?P<pct>%)?\s*(?P<cur2>usd|gbp|eur|inr|aud|cad)?\s*\)?$",
    re.I,
)


def parse_numeric(value) -> tuple[float | None, dict]:
    """Parse a cell into a float. Returns (value, info) where info notes currency/percent/scale."""
    if value is None:
        return None, {}
    if isinstance(value, bool):
        return float(value), {}
    if isinstance(value, (int, float, np.integer, np.floating)):
        f = float(value)
        return (None, {}) if np.isnan(f) else (f, {})
    s = str(value).strip().replace("\u00a0", " ").replace("\u202f", " ")
    if s == "" or s.lower() in {"na", "n/a", "nan", "none", "null", "-", "—", "–", "#n/a", "n.a.", "tbc", "?"} or \
            re.fullmatch(r"#(div/0!|n/a|ref!|value!|name\?|num!|null!|spill!|calc!)", s.lower()):
        return None, {"missing_token": s}
    if re.fullmatch(r"[^-]*\d[\d,.' ]*-", s):  # trailing minus (accounting exports): "1,234-"
        s = "-" + s[:-1]
    s2 = _NUM_CLEAN.sub("", s.replace(" ", "").replace("'", "")) if re.search(r"\d", s) else s
    m = _NUM_RE.match(s2)
    if not m:
        return None, {"unparsed": True}
    v = float(m.group("num"))
    info: dict = {}
    if m.group("scale"):
        v *= _SCALE[m.group("scale").lower()]
        info["scale"] = m.group("scale").lower()
    cur = m.group("cur") or m.group("cur2")
    if cur:
        info["currency"] = CURRENCY_SYMBOLS.get(cur, cur.upper().rstrip("."))
    if m.group("pct"):
        info["percent"] = True
    if m.group("neg"):
        v = -v
    return v, info


_QUARTER_RE = re.compile(r"^(?:(?P<y1>\d{4})\s*[-/ ]?\s*q(?P<q1>[1-4])|q(?P<q2>[1-4])\s*[-/ ']?\s*(?P<y2>\d{2,4}))$", re.I)
_DATE_PATTERNS = [
    ("iso", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T].*)?$")),
    ("year_month", re.compile(r"^\d{4}[-/]\d{1,2}$")),
    ("slash_dmy_or_mdy", re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$")),
    ("month_name", re.compile(r"^[A-Za-z]{3,9}[ \-']?\d{2,4}$|^\d{1,2}[ \-][A-Za-z]{3,9}[ \-]\d{2,4}$|^[A-Za-z]{3,9} \d{1,2},? \d{4}$")),
    ("quarter", _QUARTER_RE),
    ("year", re.compile(r"^(19|20)\d{2}$")),
]


def date_pattern(s) -> str | None:
    if not isinstance(s, str):
        return None
    s = s.strip()
    for name, rx in _DATE_PATTERNS:
        if rx.match(s):
            return name
    return None


def _parse_quarter(s: str):
    m = _QUARTER_RE.match(s.strip())
    if not m:
        return None
    y = m.group("y1") or m.group("y2")
    q = int(m.group("q1") or m.group("q2"))
    y = int(y) + (2000 if len(y) == 2 else 0)
    return pd.Timestamp(year=y, month=3 * (q - 1) + 1, day=1)


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_YY_RE = re.compile(r"([A-Za-z]{3,9})\.?[\s\-'/.,]*((?:19|20)?\d{2})")


def _umap(series: pd.Series, fn) -> pd.Series:
    """series.map(fn), computing fn once per distinct value (columns repeat values a lot: 150k rows, 700 dates)."""
    try:
        cache: dict = {}
        out = []
        for v in series:
            try:
                r = cache[v]
            except KeyError:
                r = cache[v] = fn(v)
            except TypeError:  # unhashable
                r = fn(v)
            out.append(r)
        return pd.Series(out, index=series.index, dtype=object)
    except Exception:
        return series.map(fn)


def parse_dates(series: pd.Series) -> tuple[pd.Series, dict]:
    """Parse a column of date-like strings. Returns (datetime series, info)."""
    raw = series.dropna()
    if raw.empty:
        return pd.Series(pd.NaT, index=series.index), {}
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce"), {"patterns": ["native"]}
    strs = pd.Series([None if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA or v is pd.NaT else
                      (v.isoformat() if hasattr(v, "isoformat") else str(v).strip()) for v in series],
                     index=series.index, dtype=object)
    uniq = list(dict.fromkeys(strs.dropna()))
    pat_of = {u: date_pattern(u) for u in uniq}
    counts = Counter(strs.dropna())
    patterns = Counter()
    for u, p in pat_of.items():
        if p:
            patterns[p] += counts[u]
    dayfirst = False
    slash = pd.Series([u for u in uniq if pat_of[u] == "slash_dmy_or_mdy"], dtype=object)
    if not slash.empty:
        firsts = slash.str.split(r"[/.\-]").map(lambda p: int(p[0]))
        seconds = slash.str.split(r"[/.\-]").map(lambda p: int(p[1]))
        dayfirst = bool((firsts > 12).any() and not (seconds > 12).any())
    memo: dict = {}
    out = []
    for s in strs:
        if not isinstance(s, str):
            out.append(pd.NaT)
            continue
        if s in memo:
            out.append(memo[s])
            continue
        n_before = len(out)
        p = pat_of.get(s) if s in pat_of else date_pattern(s)
        if p == "quarter":
            out.append(_parse_quarter(s))
        elif p == "year":
            out.append(pd.Timestamp(year=int(s), month=1, day=1))
        elif (mm := _MONTH_YY_RE.fullmatch(s)) and mm.group(1)[:3].lower() in _MONTHS:
            # "Jan-26", "Sep '25", "March 2026" (otherwise read as 26 January of year 1)
            y = int(mm.group(2))
            y = y if y > 99 else (2000 + y if y < 70 else 1900 + y)
            out.append(pd.Timestamp(year=y, month=_MONTHS[mm.group(1)[:3].lower()], day=1))
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    out.append(pd.to_datetime(s, dayfirst=dayfirst, errors="coerce", format="ISO8601" if p == "iso" else None))
                except Exception:
                    out.append(pd.NaT)
        if len(out) > n_before:
            memo[s] = out[-1]
    out = [v if (v is not pd.NaT and v is not None and not (isinstance(v, float)) and 1800 <= getattr(v, "year", 0) <= 2200) else pd.NaT
           for v in out]  # nonsense years (e.g. a code read as a date) are not dates
    parsed = pd.Series(out, index=series.index, dtype="datetime64[ns]")
    granularity = ""
    if patterns:
        top = patterns.most_common(1)[0][0]
        granularity = {"quarter": "quarter", "year": "year", "year_month": "month"}.get(top, "")
        if top == "month_name":
            granularity = "month"
    return parsed, {"patterns": list(patterns), "dayfirst": dayfirst, "granularity": granularity}


def label_key(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v).lower())


def _norm_colname(name: str) -> str:
    return " ".join(re.sub(r"[_\.]+", " ", str(name)).split())


def detect_role(name: str, dtype: str) -> str:
    n = " " + _norm_colname(name).lower() + " "
    if dtype in ("datetime", "period"):
        return "time"
    if dtype == "numeric":
        if re.search(r"^\s*(fy|year|yr)\s*$", n):
            return "time"
        for role, pat in ROLE_PATTERNS:
            if re.search(pat, n):
                return role
        return "measure"
    if dtype in ("categorical", "identifier", "text", "boolean"):
        for role, pat in DIM_PATTERNS:
            if role == "time":
                continue
            if re.search(pat, n):
                return role
        return "dimension" if dtype in ("categorical", "boolean") else ("identifier" if dtype == "identifier" else "text")
    return "unknown"


# ----------------------------------------------------------------- normalisation
@dataclass
class NormalizedTable:
    df: pd.DataFrame
    columns: list[ColumnMeta]
    transformations: list[dict]
    issues: list[dict]
    period_groups: dict = field(default_factory=dict)

    def meta(self, name: str) -> ColumnMeta | None:
        return next((c for c in self.columns if c.name == name), None)


def _issue(issue_type, field, severity, description, resolution, locator=None) -> dict:
    return {"issue_type": issue_type, "field": field, "severity": severity, "description": description,
            "suggested_resolution": resolution, "locator": locator or {}}


def _rows_label(rows: list[int], limit: int = 8) -> str:
    rows = sorted(set(int(r) for r in rows))
    shown = ", ".join(str(r) for r in rows[:limit])
    return shown + (f" … (+{len(rows) - limit} more)" if len(rows) > limit else "")


_PLACEHOLDERS = {"", "0", "0.0", "-", "–", "—", "n/a", "na", "none", "null", "nan", "$0", "$0.00", "0%"}
ID_NAME_RE = re.compile(r"^\S.*\s(id|code|sku|ref|reference|number|no\.?|num|#)$")


def _informative(v) -> bool:
    if v is None or v is pd.NaT or v is pd.NA:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
        return not (np.isnan(float(v)) or float(v) == 0)
    return str(v).strip().lower() not in _PLACEHOLDERS


def _small_number(v) -> bool:
    f, _ = parse_numeric(v)
    return f is None or f in (0.0, 1.0)


def drop_template_rows(raw: pd.DataFrame, rows: list[int]) -> tuple[pd.DataFrame, list[int], int]:
    """Planner/order forms often contain many unused rows that hold only formula defaults (0, 1, '-', a
    'Check Qty Column' warning). They are not data: ignore them when the sheet also has properly filled rows."""
    ncols = raw.shape[1]
    if ncols < 8 or len(raw) < 6:
        return raw, rows, 0
    flags = pd.DataFrame({i: _umap(raw.iloc[:, i].astype(object), _informative).astype(bool).to_numpy() for i in range(ncols)})
    info = flags.sum(axis=1).to_numpy()
    full = int((info >= 0.5 * ncols).sum())
    if full < 3:
        return raw, rows, 0
    limit = max(2, int(0.15 * ncols))
    vals = raw.astype(object).to_numpy()
    drop = [i for i in np.nonzero(info <= limit)[0]
            if all(_small_number(v) for v in vals[i] if _informative(v) and parse_numeric(v)[0] is not None)]
    if not drop:
        return raw, rows, 0
    keep = [i for i in range(len(raw)) if i not in set(drop)]
    return raw.iloc[keep].reset_index(drop=True), [rows[i] for i in keep], len(drop)


def drop_total_rows(raw: pd.DataFrame, rows: list[int]) -> tuple[pd.DataFrame, list[int], int]:
    """Rows labelled 'Total', 'Grand Total', 'North Total', 'Subtotal' repeat the sum of other rows."""
    if len(raw) < 3:
        return raw, rows, 0
    hit = np.zeros(len(raw), dtype=bool)
    for i in range(min(raw.shape[1], 4)):  # labels sit in the first columns
        hit |= _umap(raw.iloc[:, i].astype(object), lambda v: isinstance(v, str) and bool(TOTAL_ROW_RE.match(v))).astype(bool).to_numpy()
    vals = raw.astype(object).to_numpy()
    drop = [i for i in np.nonzero(hit)[0] if any(parse_numeric(v)[0] is not None for v in vals[i] if not isinstance(v, bool))]
    if not drop or len(drop) > 0.4 * len(raw):
        return raw, rows, 0
    keep = [i for i in range(len(raw)) if i not in set(drop)]
    return raw.iloc[keep].reset_index(drop=True), [rows[i] for i in keep], len(drop)


def _present(v) -> bool:
    if v is None or v is pd.NaT or v is pd.NA:
        return False
    if isinstance(v, float) and np.isnan(v):
        return False
    return str(v).strip() not in ("", "nan", "None", "NaT")


def fill_group_labels(raw: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """'North' written once above six rows (merged or grouped cells) -> repeat it on each row of its group."""
    filled = []
    if len(raw) < 4:
        return raw, filled
    raw = raw.copy()
    for col in list(raw.columns)[:2]:
        s = raw[col].astype(object)
        present = _umap(s, _present).astype(bool)
        share_missing = 1 - present.mean()
        if not present.iloc[0] or not (0.3 <= share_missing <= 0.95):
            continue
        if re.search(r"comment|note|remark|descr|detail|address", str(col), re.I):
            continue
        vals = s[present]
        if _umap(vals, lambda v: isinstance(v, str) and len(v) <= 40 and parse_numeric(v)[0] is None).astype(bool).mean() < 0.9:
            continue
        others = raw.drop(columns=[col]).astype(object)
        rest_filled = others.apply(lambda r: sum(1 for v in r if _present(v)), axis=1)
        if (rest_filled[~present] >= 1).mean() < 0.9:  # rows without a label must still hold data
            continue
        raw[col] = s.where(present).ffill()
        filled.append(col)
    return raw, filled


def _sep_vote(v: str) -> int:
    """+1: decimal comma (1.234,56), -1: decimal point (1,234.56), 0: can't tell (1,234 / 1.234)."""
    core = re.sub(r"[^\d.,]", "", v)
    if not core or not re.search(r"\d", core):
        return 0
    if "," in core and "." in core:
        return 1 if core.rfind(",") > core.rfind(".") else -1
    if "," in core:
        after = len(core) - core.rfind(",") - 1
        return 0 if (after == 3 and core.count(",") == 1) else (-1 if core.count(",") > 1 else 1)
    if "." in core:
        after = len(core) - core.rfind(".") - 1
        return 1 if core.count(".") > 1 else (0 if after == 3 else -1)
    return 0


def decimal_comma_column(values: pd.Series) -> bool:
    strs = [v for v in values.iloc[:3000] if isinstance(v, str) and re.search(r"\d", v)]
    if len(strs) < 2:
        return False
    votes = [_sep_vote(v) for v in strs[:500]]
    eu, us = votes.count(1), votes.count(-1)
    return eu >= 1 and eu >= 3 * us


def _from_decimal_comma(v):
    if not isinstance(v, str):
        return v
    return v.replace(".", "").replace(" ", "").replace(",", ".")


def normalize_table(raw: pd.DataFrame, row_numbers: list[int] | None = None) -> NormalizedTable:
    raw = raw.copy()
    rows = row_numbers if row_numbers and len(row_numbers) == len(raw) else list(range(2, len(raw) + 2))
    raw, rows, n_template = drop_template_rows(raw, rows)
    raw, rows, n_totals = drop_total_rows(raw, rows)
    raw, filled_down = fill_group_labels(raw)
    out = pd.DataFrame(index=raw.index)
    out[ROW_COL] = rows
    columns: list[ColumnMeta] = []
    transformations: list[dict] = []
    issues: list[dict] = []
    used_names: set[str] = set()
    n = len(raw)
    if n_totals:
        transformations.append({"field": "(rows)", "action": "set aside total rows",
                                "detail": f"{n_totals} total/subtotal row(s) were left out so figures are not counted twice"})
    for col in filled_down:
        transformations.append({"field": str(col), "action": "filled down group labels",
                                "detail": "labels written once per group (e.g. merged cells) were repeated on each row of the group"})
    if n_template:
        transformations.append({"field": "(rows)", "action": "ignored unused template rows",
                                "detail": f"{n_template} row(s) held only blanks or default values (such as 0, 1, '-' or a check message)"})
    for pos, col in enumerate(raw.columns):
        # plain Python values: pandas 3 text columns would otherwise turn "missing" into NaN floats
        s = raw[col].astype(object)
        # treat blank strings as missing
        s = _umap(s, lambda v: None if (v is None or (isinstance(v, str) and v.strip() == "") or
                                     (isinstance(v, float) and np.isnan(v))) else v)
        name = _norm_colname(col) or f"Column {pos + 1}"
        base, k = name, 2
        while name in used_names:
            name = f"{base} ({k})"
            k += 1
        used_names.add(name)
        if name != str(col):
            transformations.append({"field": str(col), "action": "renamed column", "detail": f"'{col}' → '{name}'"})
        nonnull = s.dropna()
        missing = int(s.isna().sum())
        meta = ColumnMeta(name=name, original_name=str(col), position=pos, dtype="empty", missing=missing)
        if nonnull.empty:
            out[name] = s
            columns.append(meta)
            issues.append(_issue("missing_values", name, "medium", f"Column '{name}' is completely empty.",
                                 "Remove the column or supply the missing data."))
            continue
        # --- numeric? (European exports write 1.234,56)
        if decimal_comma_column(nonnull) and date_pattern(str(nonnull.iloc[0])) is None:
            conv = _umap(nonnull, _from_decimal_comma)
            if _umap(conv, lambda v: parse_numeric(v)[0] is not None).astype(bool).mean() >= 0.85:
                s = _umap(s, _from_decimal_comma)
                nonnull = s.dropna()
                transformations.append({"field": name, "action": "read decimal commas", "detail": "values like 1.234,56 read as 1234.56"})
        parsed = _umap(nonnull, parse_numeric)
        values = parsed.map(lambda t: t[0])
        infos = parsed.map(lambda t: t[1])
        num_ok = values.notna().mean()
        lower = name.lower()
        looks_time_name = bool(re.search(r"\b(date|month|quarter|period|week|day)\b", lower)) or lower in {"year", "fy", "yr", "time"}
        date_like_share = _umap(nonnull, lambda v: date_pattern(str(v)) is not None and date_pattern(str(v)) != "year").astype(bool).mean() \
            if not pd.api.types.is_numeric_dtype(nonnull) else 0.0
        is_native_dt = pd.api.types.is_datetime64_any_dtype(s) or _umap(nonnull, lambda v: hasattr(v, "year") and hasattr(v, "month") and not isinstance(v, (int, float))).astype(bool).mean() > 0.9
        if looks_time_name and not is_native_dt and num_ok >= 0.95 and not re.search(r"\byear\b|\bfy\b|\byr\b", lower):
            nums = values.dropna()
            if len(nums) and nums.between(20000, 80000).all() and (nums % 1 == 0).mean() >= 0.95:
                # Excel stores dates as day numbers (45291 = 1 Jan 2024); shown as numbers when the cell format is lost
                conv = pd.to_datetime(s.map(lambda v: parse_numeric(v)[0]), unit="D", origin="1899-12-30", errors="coerce")
                s = conv.astype(object).where(conv.notna(), None)
                nonnull = s.dropna()
                is_native_dt = True
                transformations.append({"field": name, "action": "read Excel day numbers as dates", "detail": "e.g. 45291 → 2024-01-01"})
        if is_native_dt or (date_like_share >= 0.8) or (looks_time_name and num_ok < 0.8 and date_like_share >= 0.5):
            parsed_dt, info = parse_dates(s)
            ok = parsed_dt.notna().sum() / max(len(nonnull), 1)
            if ok >= 0.7:
                meta.dtype = "datetime"
                meta.role = "date_attribute" if DATE_ATTRIBUTE_RE.search(lower) else "time"
                gran = info.get("granularity") or _infer_granularity(parsed_dt.dropna())
                meta.period_granularity = gran
                out[name] = parsed_dt
                bad = s.notna() & parsed_dt.isna()
                if bad.any():
                    issues.append(_issue("inconsistent_dates", name, "medium",
                                         f"{int(bad.sum())} value(s) in '{name}' could not be read as dates.",
                                         "Correct these dates in the source file.", {"rows": _rows_label(out.loc[bad, ROW_COL].tolist())}))
                if len(info.get("patterns", [])) > 1:
                    issues.append(_issue("inconsistent_dates", name, "medium",
                                         f"'{name}' mixes date formats ({', '.join(info['patterns'])}).",
                                         "Use one date format throughout the column."))
                transformations.append({"field": name, "action": "parsed dates",
                                        "detail": f"formats: {', '.join(info.get('patterns', [])) or 'native'}; day-first={info.get('dayfirst', False)}"})
                meta.distinct = int(parsed_dt.nunique())
                meta.stats = {"min": str(parsed_dt.min().date()) if parsed_dt.notna().any() else None,
                              "max": str(parsed_dt.max().date()) if parsed_dt.notna().any() else None}
                columns.append(meta)
                continue
        if num_ok >= 0.85 and ID_NAME_RE.match(lower.strip()) and not re.match(r"^(number|no\.?|num|#)\s+of\b", lower.strip()) \
                and all(float(parse_numeric(v)[0]).is_integer() for v in nonnull if parse_numeric(v)[0] is not None):
            # "Item Number", "Package ID", "Activity No": codes, not amounts to add up
            s = s.map(lambda v: None if v is None else (str(int(parse_numeric(v)[0])) if parse_numeric(v)[0] is not None else str(v)))
            nonnull = s.dropna()
            num_ok = 0.0
            transformations.append({"field": name, "action": "treated as a code", "detail": "whole numbers in an ID/number column are labels, not amounts"})
        if num_ok >= 0.85:
            meta.dtype = "numeric"
            full = _umap(s, lambda v: parse_numeric(v)[0])
            cur = Counter(i.get("currency") for i in infos if i.get("currency"))
            pct = sum(1 for i in infos if i.get("percent"))
            scales = Counter(i.get("scale") for i in infos if i.get("scale"))
            if cur:
                meta.unit = cur.most_common(1)[0][0]
                if len(cur) > 1:
                    issues.append(_issue("inconsistent_currency", name, "high",
                                         f"'{name}' mixes currencies ({', '.join(cur)}). Totals across currencies are not meaningful.",
                                         "Convert all values to one currency before analysis."))
            elif re.search(r"\b(usd|gbp|eur|inr|aud|cad)\b|[$£€₹]", lower):
                code = re.search(r"\b(usd|gbp|eur|inr|aud|cad)\b|[$£€₹]", lower).group(0)
                meta.unit = CURRENCY_SYMBOLS.get(code, code.upper())
            if pct and pct >= len(nonnull) * 0.5:
                meta.unit = "%"
            elif pct:
                issues.append(_issue("inconsistent_units", name, "medium",
                                     f"Only some values in '{name}' have a % sign.", "Check whether the column mixes percentages and absolute numbers."))
            if scales and len(nonnull) - sum(scales.values()) > 0 and len(scales) >= 1:
                issues.append(_issue("inconsistent_units", name, "medium",
                                     f"'{name}' mixes scaled values ({', '.join(scales)}) with plain numbers; they were converted to full numbers.",
                                     "Confirm that the scale suffixes (k/m/bn) are correct."))
            hdr = re.search(r"\(\s*([£$€₹])?\s*(m|mn|bn|k|000s|millions?|billions?|thousands?)\s*\)|\b([£$€₹])(m|bn|k)\b", name, re.I)
            if hdr and not scales:
                sym = hdr.group(1) or hdr.group(3)
                word = (hdr.group(2) or hdr.group(4)).lower()
                factor = {"k": 1e3, "000s": 1e3, "thousand": 1e3, "thousands": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
                          "millions": 1e6, "bn": 1e9, "billion": 1e9, "billions": 1e9}.get(word, 1)
                if sym:
                    meta.unit = CURRENCY_SYMBOLS.get(sym, meta.unit)
                if factor != 1:
                    full = full * factor
                    meta.stats["header_scale"] = factor
                    transformations.append({"field": name, "action": "applied header scale",
                                            "detail": f"values multiplied by {factor:,.0f} because the column header indicates '{hdr.group(0)}'"})
            if scales:
                transformations.append({"field": name, "action": "expanded scale suffixes", "detail": ", ".join(f"{k}×{v}" for k, v in scales.items())})
            unparsed = s.notna() & full.isna()
            if unparsed.any():
                issues.append(_issue("suspicious_values", name, "medium",
                                     f"{int(unparsed.sum())} value(s) in numeric column '{name}' are not numbers and were treated as missing.",
                                     "Correct or remove the non-numeric entries.", {"rows": _rows_label(out.loc[unparsed, ROW_COL].tolist())}))
            if any(not isinstance(v, (int, float, np.number)) for v in nonnull):
                transformations.append({"field": name, "action": "converted text to numbers", "detail": "removed currency symbols, thousands separators and % signs"})
            out[name] = full.astype(float)
            role = detect_role(name, "numeric")
            # integer year column?
            ints = out[name].dropna()
            year_like = bool(len(ints)) and ints.between(1900, 2100).all() and (ints % 1 == 0).all()
            if (role == "time" and year_like) or (year_like and (lower in {"year", "fy", "yr", "fiscal year", "period"} or looks_time_name)):
                meta.dtype, meta.role, meta.period_granularity = "period", "time", "year"
                out[name] = pd.to_datetime(ints.astype(int).astype(str).reindex(out.index), format="%Y", errors="coerce")
                transformations.append({"field": name, "action": "interpreted as year", "detail": "integer years converted to periods"})
                meta.distinct = int(ints.nunique())
                meta.stats = {"min": int(ints.min()), "max": int(ints.max())}
                columns.append(meta)
                continue
            meta.role = role
            meta.additive = role in ADDITIVE_ROLES and meta.unit != "%"
            meta.missing = int(out[name].isna().sum())
            meta.distinct = int(out[name].nunique())
            v = out[name].dropna()
            meta.stats = {"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()), "median": float(v.median()),
                          "sum": float(v.sum()), "std": float(v.std()) if len(v) > 1 else 0.0}
            if meta.distinct <= 2 and set(v.unique()).issubset({0.0, 1.0}) and role == "measure":
                meta.role = "flag"
            columns.append(meta)
            continue
        # --- boolean / categorical / text / identifier
        strs = _umap(nonnull, lambda v: " ".join(str(v).split()))
        lowered = set(strs.str.lower())
        if lowered and lowered.issubset({"yes", "no", "true", "false", "y", "n", "1", "0"}):
            meta.dtype = "boolean"
        else:
            distinct = strs.nunique()
            avg_len = strs.str.len().mean()
            ratio = distinct / max(len(strs), 1)
            if avg_len > 60:
                meta.dtype = "text"
            elif ratio > 0.95 and len(strs) > 20 and re.search(r"\b(id|code|sku|ref|number|no)\b", lower):
                meta.dtype = "identifier"
            elif ratio > 0.9 and len(strs) > 50 and strs.str.split().str.len().mean() > 7:
                meta.dtype = "text"
            else:
                meta.dtype = "categorical"
        cleaned = _umap(s, lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else " ".join(str(v).split()))
        if meta.dtype == "categorical":
            # harmonise case / punctuation variants: 'meta', 'Meta ', 'META' -> most frequent spelling
            groups: dict[str, Counter] = {}
            for v in cleaned.dropna():
                groups.setdefault(label_key(v), Counter())[v] += 1
            mapping = {}
            for key, cnt in groups.items():
                if len(cnt) > 1:
                    canonical = cnt.most_common(1)[0][0]
                    for variant in cnt:
                        if variant != canonical:
                            mapping[variant] = canonical
            if mapping:
                cleaned = cleaned.map(lambda v: mapping.get(v, v) if v is not None else None)
                examples = "; ".join(f"'{a}' → '{b}'" for a, b in list(mapping.items())[:5])
                issues.append(_issue("inconsistent_naming", name, "low",
                                     f"'{name}' contains the same label spelled differently ({examples}).",
                                     "Spellings were harmonised for analysis only; consider standardising the source."))
                transformations.append({"field": name, "action": "harmonised label spelling", "detail": examples})
        trimmed = (s.dropna().map(str) != s.dropna().map(lambda v: " ".join(str(v).split()))).sum()
        if trimmed:
            transformations.append({"field": name, "action": "trimmed whitespace", "detail": f"{int(trimmed)} value(s)"})
        out[name] = cleaned
        meta.role = detect_role(name, meta.dtype)
        meta.distinct = int(cleaned.nunique())
        top = cleaned.value_counts().head(10)
        meta.stats = {"top_values": {str(k): int(v) for k, v in top.items()}}
        columns.append(meta)

    # ---- row-level checks
    data_cols = [c.name for c in columns if c.dtype != "empty"]
    if data_cols and n:
        dup_mask = out.duplicated(subset=data_cols, keep="first")
        if dup_mask.any():
            issues.append(_issue("duplicates", "(all columns)", "medium" if dup_mask.mean() < 0.05 else "high",
                                 f"{int(dup_mask.sum())} row(s) are exact duplicates of earlier rows.",
                                 "Duplicates were kept in the data but excluded from totals. Check whether they are genuine.",
                                 {"rows": _rows_label(out.loc[dup_mask, ROW_COL].tolist())}))
            transformations.append({"field": "(rows)", "action": "flagged duplicate rows", "detail": f"{int(dup_mask.sum())} excluded from calculations"})
            out["__duplicate__"] = dup_mask
    for meta in columns:
        if meta.dtype == "empty":
            continue
        share = meta.missing / max(n, 1)
        if share >= 0.05 and meta.missing:
            missing_rows = out.loc[out[meta.name].isna(), ROW_COL].tolist()
            issues.append(_issue("missing_values", meta.name, "high" if share >= 0.3 else "medium" if share >= 0.1 else "low",
                                 f"{meta.missing} of {n} values ({share:.0%}) are missing in '{meta.name}'.",
                                 "Fill the gaps in the source, or treat results using this field with caution.",
                                 {"rows": _rows_label(missing_rows)}))
        if meta.dtype == "numeric":
            v = out[meta.name]
            if meta.role in {"spend", "impressions", "clicks", "reach", "conversions", "units", "price", "market_size",
                             "competitor_count", "count", "cpc", "cpm", "cpa", "ctr", "cvr", "roas"} and (v < 0).any():
                bad = out.loc[v < 0, ROW_COL].tolist()
                issues.append(_issue("impossible_values", meta.name, "high", f"{len(bad)} negative value(s) in '{meta.name}', which cannot be negative.",
                                     "Correct the values; they may be refunds or data-entry errors.", {"rows": _rows_label(bad)}))
            if meta.unit == "%" and meta.role in {"market_share", "ctr", "cvr"} and (v > 100).any():
                bad = out.loc[v > 100, ROW_COL].tolist()
                issues.append(_issue("impossible_values", meta.name, "high", f"{len(bad)} value(s) above 100% in '{meta.name}'.",
                                     "Check the percentages.", {"rows": _rows_label(bad)}))
            vv = v.dropna()
            heterogeneous = any(c.dtype == "categorical" and c.distinct >= 3 for c in columns)
            if len(vv) >= 8 and not heterogeneous:
                med = vv.median()
                mad = (vv - med).abs().median()
                if mad > 0:
                    rz = 0.6745 * (vv - med).abs() / mad
                    out_idx = rz[rz > 8].index
                    if len(out_idx) and len(out_idx) <= max(3, len(vv) * 0.02):
                        issues.append(_issue("suspicious_values", meta.name, "low",
                                             f"{len(out_idx)} extreme value(s) in '{meta.name}' (far outside the typical range).",
                                             "Check whether these are genuine or errors (e.g. wrong units).",
                                             {"rows": _rows_label(out.loc[out_idx, ROW_COL].tolist())}))
    by_role = {c.role: c.name for c in columns if c.dtype == "numeric"}
    for small, big, label in (("clicks", "impressions", "more clicks than impressions"),
                              ("conversions", "clicks", "more conversions than clicks")):
        if small in by_role and big in by_role:
            bad = (out[by_role[big]] > 0) & (out[by_role[small]] > out[by_role[big]])
            if bad.any():
                issues.append(_issue("impossible_values", f"{by_role[small]} / {by_role[big]}", "high",
                                     f"{int(bad.sum())} row(s) have {label}.", "Check the tracking or the column mapping.",
                                     {"rows": _rows_label(out.loc[bad, ROW_COL].tolist())}))
    # incomplete periods / missing periods
    time_cols = [c for c in columns if c.role == "time"]
    if time_cols:
        tc = time_cols[0]
        gran = tc.period_granularity or _infer_granularity(out[tc.name].dropna())
        tc.period_granularity = gran
        periods = to_period_labels(out[tc.name], gran)
        counts = periods.value_counts().sort_index()
        if len(counts) >= 3:
            expected = expected_periods(counts.index.tolist(), gran)
            missing_p = [p for p in expected if p not in counts.index]
            if missing_p:
                issues.append(_issue("incomplete_periods", tc.name, "medium",
                                     f"{len(missing_p)} period(s) are missing from the time series: {', '.join(missing_p[:6])}{'…' if len(missing_p) > 6 else ''}.",
                                     "Add the missing periods or interpret trends across the gap with care."))
            med = counts.iloc[:-1].median()
            if med >= 4 and counts.iloc[-1] < 0.5 * med:
                issues.append(_issue("incomplete_periods", tc.name, "high",
                                     f"The latest period ({counts.index[-1]}) has only {counts.iloc[-1]} rows versus a typical {med:.0f}; it may be incomplete.",
                                     "Exclude the latest period from trend comparisons or wait for complete data."))
            # categories present in some periods but not others
            for meta in columns:
                if meta.dtype == "categorical" and 2 <= meta.distinct <= 30 and meta.role not in ("time",):
                    present = out.groupby(periods)[meta.name].apply(lambda x: set(x.dropna()))
                    allv = set().union(*present.tolist()) if len(present) else set()
                    missing_cats = {v: [p for p, s in present.items() if v not in s] for v in allv}
                    partial = {v: ps for v, ps in missing_cats.items() if 0 < len(ps) < len(present) and len(ps) <= len(present) - 1}
                    # only flag gaps in the middle/end, not launches
                    gaps = {v: ps for v, ps in partial.items() if any(p > min(q for q, s in present.items() if v in s) for p in ps)}
                    if gaps:
                        ex = "; ".join(f"{v} (missing {', '.join(ps[:3])})" for v, ps in list(gaps.items())[:3])
                        issues.append(_issue("missing_categories", meta.name, "low",
                                             f"Some '{meta.name}' values disappear in later periods: {ex}.",
                                             "Confirm whether these were discontinued or are missing from the data."))
                    break
    # measures spread across period columns, e.g. "Sales 2024", "Sales 2025"
    groups: dict[str, dict[str, str]] = {}
    for meta in columns:
        if meta.dtype != "numeric":
            continue
        m = YEAR_TOKEN_RE.search(meta.name)
        if not m:
            continue
        base = " ".join(YEAR_TOKEN_RE.sub("", meta.name).replace("()", "").split()).strip(" -_") or "Value"
        groups.setdefault(base, {})[m.group(1)] = meta.name
    period_groups = {b: dict(sorted(g.items())) for b, g in groups.items() if len(g) >= 2}
    for b, g in period_groups.items():
        role = detect_role(b, "numeric")
        for col in g.values():
            meta = next(c for c in columns if c.name == col)
            meta.stats["period_group"] = b
            if meta.role in ("measure", "time"):
                meta.role = role if role != "time" else "measure"
                meta.additive = meta.role in ADDITIVE_ROLES and meta.unit != "%"
    return NormalizedTable(df=out, columns=columns, transformations=transformations, issues=issues,
                           period_groups=period_groups)


def reshape_wide(raw: pd.DataFrame, row_numbers: list[int] | None) -> tuple[pd.DataFrame, list[int] | None, dict | None]:
    """Turn 'wide' tables whose headers are periods (2023 | 2024 | 2025) into long format.

    Returns (frame, row_numbers, transformation) – unchanged when the table is not wide.
    """
    cols = [str(c) for c in raw.columns]
    period_cols = [c for c in cols if PERIOD_HEADER_RE.match(c)]
    totals = [c for c in cols if TOTAL_COL_RE.match(c)] if len(period_cols) >= 2 else []
    other = [c for c in cols if c not in period_cols and c not in totals]
    if len(period_cols) < 2 or not other:
        return raw, row_numbers, None
    # the period columns must be mostly numeric
    sample = raw[period_cols].stack()
    if sample.empty or sample.map(lambda v: parse_numeric(v)[0] is not None).mean() < 0.8:
        return raw, row_numbers, None
    rows = row_numbers if row_numbers and len(row_numbers) == len(raw) else list(range(2, len(raw) + 2))
    tmp = raw.copy()
    tmp.columns = cols
    tmp = tmp.drop(columns=totals)
    tmp["__src_row__"] = rows
    long = tmp.melt(id_vars=other + ["__src_row__"], value_vars=period_cols, var_name="Period", value_name="Value")
    long["Period"] = long["Period"].str.strip()
    long_rows = long.pop("__src_row__").tolist()
    note = {"field": "(table)", "action": "reshaped wide table to long format",
            "detail": f"period columns {', '.join(period_cols)} became rows (Period, Value)"
                      + (f"; the {', '.join(totals)} column(s) repeat the sum and were left out" if totals else "")}
    return long.reset_index(drop=True), long_rows, note


def _infer_granularity(dts: pd.Series) -> str:
    if dts.empty:
        return ""
    d = pd.to_datetime(dts)
    if (d.dt.month == 1).all() and (d.dt.day == 1).all():
        return "year"
    if (d.dt.day == 1).all():
        if d.dt.month.isin([1, 4, 7, 10]).all() and d.dt.to_period("M").nunique() >= 2 and d.dt.year.nunique() >= 1:
            months = sorted(d.dt.to_period("M").unique())
            if len(months) >= 2 and all((b - a).n % 3 == 0 for a, b in zip(months, months[1:])):
                return "quarter"
        return "month"
    months = d.dt.to_period("M").nunique()
    if months > 36:
        return "year"
    if months >= 3:
        return "month"
    return "day"


def to_period_labels(series: pd.Series, granularity: str) -> pd.Series:
    d = pd.to_datetime(series, errors="coerce")
    if granularity == "year":
        return d.dt.year.astype("Int64").astype(str).where(d.notna())
    if granularity == "quarter":
        return (d.dt.year.astype("Int64").astype(str) + "-Q" + d.dt.quarter.astype("Int64").astype(str)).where(d.notna())
    if granularity in ("day", "week") and d.dt.to_period("M").nunique() >= 3:
        return d.dt.strftime("%Y-%m").where(d.notna())
    if granularity == "day":
        return d.dt.strftime("%Y-%m-%d").where(d.notna())
    return d.dt.strftime("%Y-%m").where(d.notna())


def expected_periods(labels: list[str], granularity: str) -> list[str]:
    labels = sorted(labels)
    try:
        if granularity == "year":
            ys = [int(x) for x in labels]
            return [str(y) for y in range(min(ys), max(ys) + 1)]
        if granularity == "quarter":
            ps = pd.period_range(pd.Period(labels[0].replace("-", ""), "Q"), pd.Period(labels[-1].replace("-", ""), "Q"), freq="Q")
            return [f"{p.year}-Q{p.quarter}" for p in ps]
        if len(labels[0]) == 7:
            ps = pd.period_range(labels[0], labels[-1], freq="M")
            return [p.strftime("%Y-%m") for p in ps]
    except Exception:
        return labels
    return labels


def profile_summary(norm: NormalizedTable) -> dict:
    roles = Counter(c.role for c in norm.columns)
    time_col = next((c for c in norm.columns if c.role == "time"), None)
    summary = {"roles": dict(roles), "columns": [c.to_dict() for c in norm.columns]}
    if time_col is not None:
        d = pd.to_datetime(norm.df[time_col.name], errors="coerce").dropna()
        if not d.empty:
            summary["time"] = {"column": time_col.name, "granularity": time_col.period_granularity,
                               "start": str(d.min().date()), "end": str(d.max().date()),
                               "periods": int(to_period_labels(d, time_col.period_granularity).nunique())}
    return summary
