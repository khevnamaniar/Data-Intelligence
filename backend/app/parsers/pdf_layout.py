"""Layout-aware extraction for dashboard-style PDFs.

Exports from Looker Studio, Google Ads, Google Analytics, Meta Ads, Power BI, Tableau and similar tools draw
their tables without ruling lines and their charts as vector shapes.  Plain text extraction turns them into
jumbled lines.  This module reads the page geometry instead:

* KPI tiles        - boxes holding a label and a single figure ("Clicks 157")
* borderless tables - columns found from the empty vertical gutters between words
* bar charts       - bars + the category labels under them + printed value labels
* pie/donut charts - slices + colour-matched legend + printed value labels
* line charts      - the line's points measured against the axis labels (approximate)
* report period    - "Aug 1, 2026 - Aug 31, 2026" style date ranges

Every extractor is defensive: if anything looks ambiguous it returns nothing rather than a wrong table.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

NUM_RE = re.compile(r"^[-+]?[(]?[$£€₹¥]?\s?[-+]?\d[\d,]*(\.\d+)?\s?[%kKmMbB]?[)]?$")
PLAIN_NUM_RE = re.compile(r"^[-+]?\d[\d,]*(\.\d+)?$")
SORT_ARROWS = "▼▲↑↓⬇⬆▾▴"
CURRENCY_ONLY = {"₹", "$", "£", "€", "¥", "INR", "USD", "AUD", "GBP", "EUR"}
MEASURE_WORDS = ["clicks", "impressions", "conversions", "sessions", "users", "views", "revenue", "sales", "spend",
                 "cost", "orders", "leads", "visits", "installs", "calls", "reach", "engagements", "followers", "units"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def is_num(s: str) -> bool:
    return bool(NUM_RE.match(s.strip())) if s and s.strip() else False


def to_float(s: str) -> float | None:
    t = s.strip().replace(",", "").strip("()")
    t = re.sub(r"^[$£€₹¥]\s?", "", t)
    mult = 1.0
    if t[-1:] in "kK":
        mult, t = 1e3, t[:-1]
    elif t[-1:] in "mM":
        mult, t = 1e6, t[:-1]
    elif t[-1:] in "bB":
        mult, t = 1e9, t[:-1]
    t = t.rstrip("%")
    try:
        return float(t) * mult
    except ValueError:
        return None


# ------------------------------------------------------------------ report period
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_SEP = r"\s*(?:-|–|—|to|until)\s*"
PERIOD_PATTERNS = [
    re.compile(rf"\b{_MON}\s+(\d{{1,2}}),?\s+(\d{{4}}){_SEP}{_MON}\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I),   # Aug 1, 2026 - Aug 31, 2026
    re.compile(rf"\b(\d{{1,2}})\s+{_MON},?\s+(\d{{4}}){_SEP}(\d{{1,2}})\s+{_MON},?\s+(\d{{4}})", re.I),   # 1 Aug 2026 - 31 Aug 2026
    re.compile(rf"\b(\d{{4}})-(\d{{2}})-(\d{{2}}){_SEP}(\d{{4}})-(\d{{2}})-(\d{{2}})"),                    # 2026-08-01 - 2026-08-31
    re.compile(rf"\b(\d{{1,2}})/(\d{{1,2}})/(\d{{4}}){_SEP}(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})"),            # 01/08/2026 - 31/08/2026
]


def _mon(s: str) -> int:
    return MONTHS[s.lower()[:3]]


def _parse_range(text: str) -> tuple[date, date] | None:
    for i, rx in enumerate(PERIOD_PATTERNS):
        m = rx.search(text)
        if not m:
            continue
        g = m.groups()
        try:
            if i == 0:
                a, b = date(int(g[2]), _mon(g[0]), int(g[1])), date(int(g[5]), _mon(g[3]), int(g[4]))
            elif i == 1:
                a, b = date(int(g[2]), _mon(g[1]), int(g[0])), date(int(g[5]), _mon(g[4]), int(g[3]))
            elif i == 2:
                a, b = date(int(g[0]), int(g[1]), int(g[2])), date(int(g[3]), int(g[4]), int(g[5]))
            else:
                d1, m1, d2, m2 = int(g[0]), int(g[1]), int(g[3]), int(g[4])
                if m1 > 12 or m2 > 12:  # month/day order
                    d1, m1, d2, m2 = m1, d1, m2, d2
                a, b = date(int(g[2]), m1, d1), date(int(g[5]), m2, d2)
        except (ValueError, KeyError):
            continue
        if a <= b:
            return a, b
    return None


def describe_period(a: date, b: date) -> dict:
    nxt = (b + timedelta(days=1))
    whole_months = a.day == 1 and nxt.day == 1
    if whole_months and (a.year, a.month) == (b.year, b.month):
        return {"start": a.isoformat(), "end": b.isoformat(), "value": f"{a.year}-{a.month:02d}",
                "label": a.strftime("%b %Y"), "granularity": "month"}
    months = (b.year - a.year) * 12 + b.month - a.month + 1
    if whole_months and months == 3 and a.month in (1, 4, 7, 10):
        q = (a.month - 1) // 3 + 1
        return {"start": a.isoformat(), "end": b.isoformat(), "value": f"{a.year}-Q{q}", "label": f"Q{q} {a.year}",
                "granularity": "quarter"}
    if whole_months and months == 12 and a.month == 1:
        return {"start": a.isoformat(), "end": b.isoformat(), "value": str(a.year), "label": str(a.year), "granularity": "year"}
    return {"start": a.isoformat(), "end": b.isoformat(), "value": a.isoformat(),
            "label": f"{a.strftime('%d %b %Y')} – {b.strftime('%d %b %Y')}", "granularity": "range"}


def detect_report_period(page_texts: list[str]) -> dict | None:
    """The single reporting period a document covers, if it states exactly one (e.g. on each page header)."""
    found = Counter()
    for t in page_texts:
        r = _parse_range(t[:600] if t else "")
        if r:
            found[r] += 1
    if len(found) != 1:
        return None
    (a, b), _ = found.most_common(1)[0]
    return describe_period(a, b)


# ------------------------------------------------------------------ geometry helpers
@dataclass
class Line:
    words: list[dict]
    top: float
    bottom: float
    size: float
    cells: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w["text"] for w in self.words)


def _overlaps(b, region, pad=0.0) -> bool:
    x0, t, x1, bt = b
    rx0, rt, rx1, rb = region
    return not (x1 < rx0 - pad or x0 > rx1 + pad or bt < rt - pad or t > rb + pad)


def _center_in(w, region) -> bool:
    cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def group_lines(words: list[dict]) -> list[Line]:
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[Line] = []
    for w in words:
        mid = (w["top"] + w["bottom"]) / 2
        for ln in lines[-4:]:
            if ln.top - 1 <= mid <= ln.bottom + 1 and abs((ln.top + ln.bottom) / 2 - mid) < max(3.0, 0.45 * (w["bottom"] - w["top"])):
                ln.words.append(w)
                ln.top, ln.bottom = min(ln.top, w["top"]), max(ln.bottom, w["bottom"])
                ln.size = max(ln.size, float(w.get("size") or (w["bottom"] - w["top"])))
                break
        else:
            lines.append(Line([w], w["top"], w["bottom"], float(w.get("size") or (w["bottom"] - w["top"]))))
    for ln in lines:
        ln.words.sort(key=lambda w: w["x0"])
        ln.cells = _cells(ln.words, ln.size)
    lines.sort(key=lambda ln: ln.top)
    return lines


def _cells(words: list[dict], size: float) -> list[dict]:
    gap = max(6.0, 0.9 * size)
    cells: list[dict] = []
    for w in words:
        if cells and w["x0"] - cells[-1]["x1"] <= gap:
            c = cells[-1]
            c["text"] += " " + w["text"]
            c["x1"] = max(c["x1"], w["x1"])
            c["words"].append(w)
        else:
            cells.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"], "bottom": w["bottom"], "words": [w]})
    return cells


def _fill(obj) -> tuple | None:
    col = obj.get("non_stroking_color")
    if col is None:
        return None
    if isinstance(col, (int, float)):
        col = (col, col, col)
    if isinstance(col, (list, tuple)) and len(col) == 4:  # CMYK -> RGB
        c, m, y, k = col
        col = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    try:
        return tuple(round(float(v), 3) for v in col[:3])
    except (TypeError, ValueError):
        return None


def _is_neutral(rgb) -> bool:
    if rgb is None:
        return True
    return max(rgb) - min(rgb) < 0.06  # white, grey, black: backgrounds, grid lines, borders


def _title_above(lines: list[Line], top: float, x0: float, x1: float, body: float, max_dist: float = 90) -> str:
    """Nearest heading-like line above a region (e.g. the chart / table title bar)."""
    best = ""
    for ln in reversed([ln for ln in lines if ln.bottom <= top + 1 and top - ln.bottom <= max_dist]):
        txt = ln.text.strip(SORT_ARROWS + " ")
        if not txt or is_num(txt) or _parse_range(txt):
            continue
        lx0, lx1 = ln.words[0]["x0"], ln.words[-1]["x1"]
        if lx1 < x0 - 40 or lx0 > x1 + 40:
            continue
        if ln.size >= body * 1.15:
            return txt
        best = best or ""
    return best


def split_title(title: str) -> tuple[str, str]:
    """'Devices Wise Clicks' -> ('Devices', 'Clicks'); 'Clicks by Device' -> ('Device', 'Clicks')."""
    t = " ".join(title.split())
    m = re.match(r"^(.+?)\s*[- ]?wise\s+(.+)$", t, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.+?)\s+(?:by|per|across)\s+(.+)$", t, re.I)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    low = t.lower()
    meas = next((w.title() for w in MEASURE_WORDS if w in low), "")
    return "", meas


def _legend_entries(page, words) -> list[tuple[tuple, str, tuple]]:
    """Small coloured markers (squares, circles) with a text label to their right: (rgb, label, bbox)."""
    out = []
    shapes = list(page.rects) + list(page.curves)
    for s in shapes:
        w, h = s["x1"] - s["x0"], s["bottom"] - s["top"]
        if not (3 <= w <= 22 and 3 <= h <= 22):
            continue
        rgb = _fill(s)
        if _is_neutral(rgb) or not s.get("fill", True):
            continue
        cy = (s["top"] + s["bottom"]) / 2
        lab = [wd for wd in words if 0 <= wd["x0"] - s["x1"] <= 30 + 0 and wd["top"] - 3 <= cy <= wd["bottom"] + 3]
        if not lab:
            continue
        lab.sort(key=lambda wd: wd["x0"])
        text, last = [], None
        for wd in lab + sorted([wd for wd in words if wd["x0"] > lab[0]["x0"] and wd["top"] - 3 <= cy <= wd["bottom"] + 3
                                and wd not in lab], key=lambda wd: wd["x0"]):
            if last is not None and wd["x0"] - last["x1"] > 8:
                break
            text.append(wd["text"])
            last = wd
        out.append((rgb, " ".join(text), (s["x0"], s["top"], last["x1"] if last else s["x1"], s["bottom"])))
    # dedupe identical markers drawn twice
    seen, uniq = set(), []
    for e in out:
        k = (e[0], e[1])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def _color_close(a, b, tol=0.04) -> bool:
    return a is not None and b is not None and all(abs(x - y) <= tol for x, y in zip(a, b))


# ------------------------------------------------------------------ KPI tiles
def kpi_tiles(tables, page_area: float) -> list[dict]:
    """Rows of boxes like ['Impressions\\n4,591', 'Avg. CPC\\n88.89\\n₹'] -> {label: raw value}."""
    out = []
    for tbl in tables:
        x0, t, x1, b = tbl.bbox
        if (x1 - x0) * (b - t) > 0.5 * page_area:
            continue
        try:
            rows = tbl.extract()
        except Exception:
            continue
        for row in rows:
            cells = [c for c in row if c and str(c).strip()]
            if len(cells) < 2:
                continue
            parsed = {}
            for c in cells:
                parts = [p.strip() for p in str(c).split("\n") if p.strip()]
                nums = [p for p in parts if is_num(p)]
                cur = [p for p in parts if p in CURRENCY_ONLY]
                labels = [p for p in parts if not is_num(p) and p not in CURRENCY_ONLY]
                if len(nums) != 1 or not labels or len(" ".join(labels)) > 40:
                    break
                val = nums[0]
                if cur and not val.startswith(tuple(CURRENCY_ONLY)):
                    val = cur[0] + val
                parsed[" ".join(labels)] = val
            else:
                if len(parsed) >= 2:
                    title = next((str(r[0]).strip() for r in rows if r and r[0] and sum(1 for c in r if c) == 1
                                  and not is_num(str(r[0]))), "")
                    out.append({"bbox": tbl.bbox, "values": parsed, "title": title})
                    break
    return out


# ------------------------------------------------------------------ bar charts (vertical)
def bar_charts(page, words, lines, body) -> list[dict]:
    bars = []
    for r in page.rects:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        rgb = _fill(r)
        if w < 6 or h < 0.05 or _is_neutral(rgb) or not r.get("fill", True):
            continue
        bars.append({"x0": r["x0"], "x1": r["x1"], "top": r["top"], "bottom": r["bottom"], "rgb": rgb})
    groups: dict = {}
    for bar in sorted(bars, key=lambda b: b["bottom"]):
        wkey = round((bar["x1"] - bar["x0"]) / 3)
        key = next((k for k in groups if k[1] == bar["rgb"] and k[2] == wkey and abs(k[0] - bar["bottom"]) <= 2.5), None)
        if key is None:
            key = (bar["bottom"], bar["rgb"], wkey)
        groups.setdefault(key, []).append(bar)
    charts = []
    for (_, rgb, _), grp in groups.items():
        # de-duplicate shapes drawn twice and require several bars side by side
        grp = sorted({(round(g["x0"]), round(g["x1"])): g for g in grp}.values(), key=lambda g: g["x0"])
        if len(grp) < 2:
            continue
        base = max(g["bottom"] for g in grp)
        centers = [(g["x0"] + g["x1"]) / 2 for g in grp]
        spacing = min(b - a for a, b in zip(centers, centers[1:])) if len(centers) > 1 else 0
        if spacing <= (grp[0]["x1"] - grp[0]["x0"]) * 0.9:  # touching/stacked shapes, not a bar chart
            continue
        # category labels: the first text line just below the baseline
        below = [ln for ln in lines if 0 <= ln.top - base <= 25]
        if not below:
            continue
        cat_line = below[0]
        x_lo, x_hi = grp[0]["x0"] - spacing / 2, grp[-1]["x1"] + spacing / 2
        cats = [c for c in cat_line.cells if x_lo <= (c["x0"] + c["x1"]) / 2 <= x_hi]
        # Labels may be one cell per bar or one long cell; fall back to word-level split by bar slots
        if len(cats) < len(grp):
            cats = []
            for w in cat_line.words:
                cx = (w["x0"] + w["x1"]) / 2
                if x_lo <= cx <= x_hi:
                    if cats and abs(cx - (cats[-1]["x0"] + cats[-1]["x1"]) / 2) < spacing * 0.45:
                        cats[-1]["text"] += " " + w["text"]
                        cats[-1]["x1"] = w["x1"]
                    else:
                        cats.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
        if len(cats) < 2 or len(cats) > 60:
            continue
        cat_c = [(c["x0"] + c["x1"]) / 2 for c in cats]
        step = min(b - a for a, b in zip(cat_c, cat_c[1:]))
        if step < 8:
            continue
        top_lim = min(g["top"] for g in grp) - 30
        nums = [w for w in words if PLAIN_NUM_RE.match(w["text"]) and top_lim <= w["top"] and w["bottom"] <= base + 2]
        # y-axis ticks: a vertical stack of right-aligned numbers left of the first category
        ticks = [w for w in nums if (w["x0"] + w["x1"]) / 2 < cat_c[0] - step / 2]
        vals: dict[int, float] = {}
        for i, cx in enumerate(cat_c):
            cand = [w for w in nums if w not in ticks and abs((w["x0"] + w["x1"]) / 2 - cx) <= step * 0.45]
            bar = next((g for g in grp if g["x0"] - 2 <= cx <= g["x1"] + 2), None)
            if not cand:
                continue
            ref = bar["top"] if bar else base
            cand.sort(key=lambda w: abs((w["top"] + w["bottom"]) / 2 - ref))
            v = to_float(cand[0]["text"])
            if v is not None:
                vals[i] = v
        if len(vals) < max(2, len(cats) // 2):
            continue
        # estimate any unlabeled bar from its height, scaled by the labelled bars
        approx = False
        ratios = []
        for i, v in vals.items():
            bar = next((g for g in grp if g["x0"] - 2 <= cat_c[i] <= g["x1"] + 2), None)
            if bar and v > 0 and base - bar["top"] > 2:
                ratios.append(v / (base - bar["top"]))
        for i, cx in enumerate(cat_c):
            if i in vals:
                continue
            bar = next((g for g in grp if g["x0"] - 2 <= cx <= g["x1"] + 2), None)
            if bar is None:
                continue
            if ratios:
                vals[i] = round((base - bar["top"]) * sorted(ratios)[len(ratios) // 2], 1)
                approx = True
        x0 = min(min(w["x0"] for w in ticks) if ticks else grp[0]["x0"], grp[0]["x0"]) - 4
        region = (x0, top_lim, max(grp[-1]["x1"], cats[-1]["x1"]) + 4, cat_line.bottom + 2)
        legend = [e for e in _legend_entries(page, words) if _color_close(e[0], rgb) and _overlaps(e[2], region, pad=40)]
        title = _title_above(lines, region[1], region[0], region[2], body)
        dim, meas = split_title(title)
        meas = (legend[0][1] if len(legend) == 1 and len(legend[0][1]) < 40 else "") or meas or "Value"
        rows = [[dim or "Category", meas]] + [[cats[i]["text"], vals[i]] for i in range(len(cats)) if i in vals]
        if legend:
            region = (region[0], min(region[1], legend[0][2][1] - 2), region[2], region[3])
        charts.append({"region": region, "rows": rows, "title": title or f"{meas} by {dim or 'category'}",
                       "approximate": approx, "kind": "bar chart"})
    return charts


# ------------------------------------------------------------------ pie / donut charts
def _point_in_poly(x, y, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1:
            inside = not inside
    return inside


def _angle_span(pts, center) -> float:
    cx, cy = center
    angs = sorted(math.atan2(y - cy, x - cx) for x, y in pts if math.hypot(x - cx, y - cy) > 1)
    if len(angs) < 2:
        return 0.0
    gaps = [b - a for a, b in zip(angs, angs[1:])] + [angs[0] + 2 * math.pi - angs[-1]]
    return 2 * math.pi - max(gaps)


def pie_charts(page, words, lines, body) -> list[dict]:
    slices = []
    for c in page.curves:
        rgb = _fill(c)
        pts = [(float(p[0]), float(p[1])) for p in (c.get("pts") or [])]
        if not c.get("fill") or _is_neutral(rgb) or len(pts) < 3:
            continue
        slices.append({"pts": pts, "rgb": rgb, "bbox": (c["x0"], c["top"], c["x1"], c["bottom"])})
    if len(slices) < 2:
        return []
    # group slices sharing a common vertex (the pie centre)
    charts, used = [], set()
    for i, s in enumerate(slices):
        if i in used:
            continue
        grp = [i]
        sv = Counter((round(x), round(y)) for x, y in s["pts"])
        for j in range(i + 1, len(slices)):
            if j in used:
                continue
            if any(abs(a - x) <= 1.5 and abs(b - y) <= 1.5 for (a, b) in sv for (x, y) in slices[j]["pts"]):
                grp.append(j)
        if len(grp) < 2:
            continue
        pts_count = Counter()
        for k in grp:
            for p in {(round(x), round(y)) for x, y in slices[k]["pts"]}:
                pts_count[p] += 1
        center, n = pts_count.most_common(1)[0]
        if n < len(grp):
            continue
        used.update(grp)
        sl = [slices[k] for k in grp]
        region = (min(s["bbox"][0] for s in sl), min(s["bbox"][1] for s in sl), max(s["bbox"][2] for s in sl),
                  max(s["bbox"][3] for s in sl))
        legend = [e for e in _legend_entries(page, words) if _overlaps(e[2], region, pad=320)
                  and abs((e[2][1] + e[2][3]) / 2 - (region[1] + region[3]) / 2) < (region[3] - region[1])]
        labels = {}
        for w in words:
            if not PLAIN_NUM_RE.match(w["text"]):
                continue
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            inside = [k for k, s in enumerate(sl) if _point_in_poly(cx, cy, s["pts"])]
            if inside:
                k = min(inside, key=lambda k: (sl[k]["bbox"][2] - sl[k]["bbox"][0]) * (sl[k]["bbox"][3] - sl[k]["bbox"][1]))
                labels.setdefault(k, to_float(w["text"]))
        if not labels:
            continue
        spans = [_angle_span(s["pts"], center) for s in sl]
        scale = [labels[k] / spans[k] for k in labels if spans[k] > 0.05 and labels[k]]
        rows, approx = [], False
        for k, s in enumerate(sl):
            name = next((e[1] for e in legend if _color_close(e[0], s["rgb"])), None)
            if name is None:
                break
            if k in labels:
                v = labels[k]
            elif scale:
                v, approx = round(spans[k] * sorted(scale)[len(scale) // 2], 1), True
            else:
                break
            rows.append([name, v])
        else:
            # legend entries without any slice are zero-value categories
            for e in legend:
                if not any(_color_close(e[0], s["rgb"]) for s in sl) and e[1] not in [r[0] for r in rows]:
                    rows.append([e[1], 0.0])
            leg_box = [e[2] for e in legend]
            full = (min([region[0]] + [b[0] for b in leg_box]), min([region[1]] + [b[1] for b in leg_box]) - 2,
                    max([region[2]] + [b[2] for b in leg_box]) + 2, max([region[3]] + [b[3] for b in leg_box]) + 2)
            title = _title_above(lines, full[1], full[0], full[2], body, max_dist=120)
            dim, meas = split_title(title)
            charts.append({"region": full, "rows": [[dim or "Category", meas or "Value"]] + rows, "kind": "pie chart",
                           "title": title or "Breakdown", "approximate": approx})
    return charts


# ------------------------------------------------------------------ line charts
def _parse_axis_date(text: str, year: int | None) -> date | None:
    t = text.strip().rstrip(",")
    for fmt in ("%b %d, %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    if year:
        for fmt in ("%b %d", "%d %b", "%B %d", "%d %B"):
            try:
                return datetime.strptime(f"{t} {year}", fmt + " %Y").date()
            except ValueError:
                pass
    return None


def _fit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - a * mx
    resid = max(abs(a * x + b - y) for x, y in zip(xs, ys))
    return a, b, resid


def line_charts(page, words, lines, body, year: int | None) -> list[dict]:
    charts = []
    for c in page.curves:
        pts = [(float(p[0]), float(p[1])) for p in (c.get("pts") or [])]
        if c.get("fill") or len(pts) < 4 or (c["x1"] - c["x0"]) < 120:
            continue
        xs = [p[0] for p in pts]
        if any(b < a - 0.5 for a, b in zip(xs, xs[1:])):  # not a function of x (e.g. an outline)
            continue
        # y ticks: numbers left of the plot, vertically around it
        cand = [w for w in words if PLAIN_NUM_RE.match(w["text"]) and w["x1"] <= c["x0"] + 2 and c["x0"] - w["x1"] < 160
                and c["top"] - 40 <= w["top"] <= c["bottom"] + 25]
        # the y axis is the right-aligned stack of numbers closest to the line
        by_edge: dict[int, list] = {}
        for w in cand:
            by_edge.setdefault(round(w["x1"] / 3), []).append(w)
        stacks = [v for v in by_edge.values() if len(v) >= 2]
        if not stacks:
            continue
        ticks = max(stacks, key=lambda v: (len(v), v[0]["x1"]))
        grid = [(g["top"] + g["bottom"]) / 2 for g in list(page.rects) + list(page.lines)
                if (g["bottom"] - g["top"]) <= 1.5 and (g["x1"] - g["x0"]) >= 0.5 * (c["x1"] - c["x0"])]

        def tick_y(w):
            mid = (w["top"] + w["bottom"]) / 2
            near = [g for g in grid if abs(g - mid) <= 4]
            return min(near, key=lambda g: abs(g - mid)) if near else mid

        fy = _fit([tick_y(w) for w in ticks], [to_float(w["text"]) for w in ticks])
        if fy is None or fy[0] >= 0:
            continue
        ay, by, ry = fy
        tick_vals = sorted(to_float(w["text"]) for w in ticks)
        unit_pts = abs(1 / ay)
        if ry > max(0.5, 0.02 * (tick_vals[-1] - tick_vals[0])):
            continue
        plot_bottom = max(max(w["bottom"] for w in ticks), c["bottom"])
        x_line = next((ln for ln in lines if 0 <= ln.top - plot_bottom <= 25 and len(ln.words) >= 2), None)
        if x_line is None:
            continue
        # x labels: dates (e.g. "Aug 1") or categories
        labels = []
        cells = x_line.cells if len(x_line.cells) > 1 else []
        if not cells or len(cells) < 3:
            # split "Aug 1 Aug 3 ..." by month-name words
            cur = None
            for w in x_line.words:
                if w["text"].lower()[:3] in MONTHS and not PLAIN_NUM_RE.match(w["text"]):
                    cur = {"text": w["text"], "x0": w["x0"], "x1": w["x1"]}
                    labels.append(cur)
                elif cur is not None:
                    cur["text"] += " " + w["text"]
                    cur["x1"] = w["x1"]
        else:
            labels = [{"text": c2["text"], "x0": c2["x0"], "x1": c2["x1"]} for c2 in cells]
        dated = [(lab, _parse_axis_date(lab["text"], year)) for lab in labels]
        dated = [(lab, d) for lab, d in dated if d]
        if len(dated) < 3 or len(dated) < 0.8 * len(labels):
            continue
        fx = _fit([(lab["x0"] + lab["x1"]) / 2 for lab, _ in dated], [d.toordinal() for _, d in dated])
        if fx is None or fx[0] <= 0 or fx[2] > 0.6:
            continue
        ax, bx, _ = fx
        series: dict[date, list[float]] = {}
        for x, y in pts:
            d = date.fromordinal(int(round(ax * x + bx)))
            series.setdefault(d, []).append(ay * y + by)
        integer_axis = all(float(v).is_integer() for v in tick_vals)
        rows_v = []
        for d in sorted(series):
            v = sum(series[d]) / len(series[d])
            v = max(v, 0.0) if tick_vals[0] >= 0 else v
            rows_v.append([d.isoformat(), float(round(v)) if integer_axis else round(v, 1)])
        # series name from a short legend line just above the plot
        legend_words = [w for w in words if c["top"] - 30 <= w["top"] <= min(c["top"], min(t["top"] for t in ticks)) - 2
                        and not is_num(w["text"]) and c["x0"] - 20 <= w["x0"] <= c["x1"]]
        name = " ".join(w["text"] for w in sorted(legend_words, key=lambda w: w["x0"]))[:40]
        region = (min(w["x0"] for w in ticks) - 2, min(c["top"], min(w["top"] for w in ticks)) - 32,
                  max(c["x1"], x_line.words[-1]["x1"]) + 4, x_line.bottom + 2)
        title = _title_above(lines, region[1], region[0], region[2], body)
        _, meas = split_title(title)
        name = name if name and not _parse_range(name) else (meas or "Value")
        step = round(1 / unit_pts, 2)
        charts.append({"region": region, "rows": [["Date", name]] + rows_v, "kind": "line chart",
                       "title": title or f"{name} over time", "approximate": True,
                       "note": (f"Values were measured from the line's position on the chart (accuracy about ±{max(step, 0.5):g}). "
                                "Use the source data export if exact daily figures matter.")})
    return charts


# ------------------------------------------------------------------ borderless tables
def borderless_tables(lines: list[Line], body: float) -> list[dict]:
    """Blocks of aligned rows with several columns and some numbers, columns split at empty vertical gutters."""
    out = []
    i = 0
    while i < len(lines):
        if len(lines[i].cells) < 3:
            i += 1
            continue
        block = [lines[i]]
        j = i + 1
        while j < len(lines):
            prev = block[-1]
            gap = lines[j].top - prev.bottom
            if len(lines[j].cells) >= 2 and gap <= max(14.0, 2.2 * prev.size):
                block.append(lines[j])
                j += 1
            else:
                break
        numeric_rows = sum(1 for ln in block if any(is_num(c["text"]) for c in ln.cells))
        if len(block) >= 3 and numeric_rows >= 2:
            t = _block_to_rows(block)
            if t:
                out.append({"rows": t, "region": (min(w["x0"] for ln in block for w in ln.words) - 2, block[0].top - 2,
                                                  max(w["x1"] for ln in block for w in ln.words) + 2, block[-1].bottom + 2),
                            "top": block[0].top})
            i = j
        else:
            i += 1
    return out


def _block_to_rows(block: list[Line]) -> list[list[str]] | None:
    spans = sorted((w["x0"], w["x1"]) for ln in block for w in ln.words)
    cols = []
    for a, b in spans:
        if cols and a - cols[-1][1] < 7:
            cols[-1][1] = max(cols[-1][1], b)
        else:
            cols.append([a, b])
    if len(cols) < 3 or len(cols) > 30:
        return None
    rows = []
    for ln in block:
        row = [""] * len(cols)
        for w in ln.words:
            cx = (w["x0"] + w["x1"]) / 2
            k = next((n for n, (a, b) in enumerate(cols) if a - 0.5 <= cx <= b + 0.5), None)
            if k is None:
                return None
            row[k] = (row[k] + " " + w["text"]).strip()
        rows.append([c.strip(SORT_ARROWS + " ").strip() for c in row])
    # a header row whose words are wider than its column can split one column in two -> merge empty columns
    filled = [sum(1 for r in rows if r[k]) for k in range(len(cols))]
    if sum(1 for f in filled if f >= 2) < 3:
        return None
    return rows
