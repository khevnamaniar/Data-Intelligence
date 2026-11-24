"""Deterministic statistical helpers. Metrics are only computed when enough data exists."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as sstats


def pct_change(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0 or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return None
    return (b - a) / abs(a) * 100


def cagr(start: float, end: float, years: float) -> float | None:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def period_years(labels: list[str], granularity: str) -> float:
    n = len(labels) - 1
    return {"year": n, "quarter": n / 4, "month": n / 12, "week": n / 52, "day": n / 365}.get(granularity, n / 12)


def trend_stats(series: pd.Series, granularity: str) -> dict | None:
    """Summarise an ordered series indexed by period label."""
    s = series.dropna()
    s = s[np.isfinite(s.values.astype(float))]
    if len(s) < 2:
        return None
    labels = [str(i) for i in s.index]
    vals = s.values.astype(float)
    start, end = float(vals[0]), float(vals[-1])
    out = {
        "periods": len(s), "start_period": labels[0], "end_period": labels[-1], "start": start, "end": end,
        "abs_change": end - start, "pct_change": pct_change(start, end), "cagr": None, "slope": None, "r_squared": None,
        "p_value": None, "direction": "flat", "acceleration": "", "moving_average": None, "volatility": None,
        "series": [{"period": l, "value": float(v)} for l, v in zip(labels, vals)],
    }
    yrs = period_years(labels, granularity)
    if yrs >= 1 and len(s) >= 2:
        out["cagr"] = cagr(start, end, yrs)
    if len(s) >= 3:
        x = np.arange(len(vals))
        lr = sstats.linregress(x, vals)
        out["slope"], out["r_squared"], out["p_value"] = float(lr.slope), float(lr.rvalue ** 2), float(lr.pvalue)
        mean = np.mean(np.abs(vals)) or 1
        rel_slope = lr.slope * (len(vals) - 1) / mean * 100  # % change implied by the fitted line
        out["fitted_pct_change"] = float(rel_slope)
        if lr.pvalue < 0.1 and abs(rel_slope) >= 3:
            out["direction"] = "increasing" if lr.slope > 0 else "decreasing"
        elif out["pct_change"] is not None and abs(out["pct_change"]) >= 5 and len(s) <= 4:
            out["direction"] = "increasing" if end > start else "decreasing"
        else:
            out["direction"] = "volatile" if np.std(vals) / mean > 0.25 else "flat"
        if len(s) >= 4:
            half = len(vals) // 2
            g1 = np.polyfit(np.arange(half + 1), vals[: half + 1], 1)[0]
            g2 = np.polyfit(np.arange(len(vals) - half), vals[half:], 1)[0]
            if abs(g1) + abs(g2) > 0 and np.sign(g1) == np.sign(g2) and abs(g2 - g1) / (abs(g1) + abs(g2)) > 0.25:
                out["acceleration"] = "accelerating" if abs(g2) > abs(g1) else "decelerating"
            elif np.sign(g1) != np.sign(g2) and abs(g1) > 0 and abs(g2) > 0:
                out["acceleration"] = "reversing"
            else:
                out["acceleration"] = "steady"
        if len(s) >= 6:
            ma = pd.Series(vals).rolling(3).mean()
            out["moving_average"] = [None if math.isnan(v) else float(v) for v in ma]
            for i, row in enumerate(out["series"]):
                row["moving_average"] = out["moving_average"][i]
        changes = np.diff(vals) / np.where(vals[:-1] == 0, np.nan, np.abs(vals[:-1]))
        if np.isfinite(changes).sum() >= 2:
            out["volatility"] = float(np.nanstd(changes) * 100)
    else:
        if out["pct_change"] is not None and abs(out["pct_change"]) >= 2:
            out["direction"] = "increasing" if end > start else "decreasing"
    return out


def share_table(values: pd.Series) -> pd.Series:
    total = values.sum()
    return values / total * 100 if total else values * 0


def hhi(shares_pct: pd.Series) -> float:
    return float(((shares_pct / 100) ** 2).sum())


def robust_z(values: pd.Series) -> pd.Series:
    med = values.median()
    mad = (values - med).abs().median()
    if not mad:
        return pd.Series(0.0, index=values.index)
    return 0.6745 * (values - med) / mad


def correlation(a: pd.Series, b: pd.Series) -> dict | None:
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < 8 or df.iloc[:, 0].nunique() < 3 or df.iloc[:, 1].nunique() < 3:
        return None
    r, p = sstats.pearsonr(df.iloc[:, 0], df.iloc[:, 1])
    rho, p2 = sstats.spearmanr(df.iloc[:, 0], df.iloc[:, 1])
    return {"n": len(df), "pearson": float(r), "p_value": float(p), "spearman": float(rho), "spearman_p": float(p2)}


def linear_forecast(values: list[float], steps: int) -> dict | None:
    if len(values) < 3:
        return None
    x = np.arange(len(values))
    lr = sstats.linregress(x, values)
    resid = np.array(values) - (lr.intercept + lr.slope * x)
    sd = float(np.std(resid, ddof=1)) if len(values) > 2 else 0.0
    xs = np.arange(len(values), len(values) + steps)
    pred = lr.intercept + lr.slope * xs
    return {"values": [float(v) for v in pred], "low": [float(v - 1.96 * sd) for v in pred],
            "high": [float(v + 1.96 * sd) for v in pred], "r_squared": float(lr.rvalue ** 2)}
