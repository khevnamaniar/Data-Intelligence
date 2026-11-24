"""Unit tests of the analytical engines on small, hand-checkable datasets."""
import numpy as np
import pandas as pd
import pytest

from app.analytics.comparisons import period_comparison, plan_vs_actual, segment_comparison
from app.analytics.gaps import analyse_gaps
from app.analytics.marketing import analyse_marketing
from app.analytics.model import Column, DatasetView, DocumentView, Chunk, ProjectData
from app.analytics.opportunities import analyse_opportunities
from app.analytics.profiling import normalize_table
from app.analytics.stats import cagr, pct_change, trend_stats
from app.analytics.trends import analyse_trends


def view(df: pd.DataFrame, name="data.csv", role="dataset", period_groups=None) -> DatasetView:
    norm = normalize_table(df)
    cols = {c.name: Column(name=c.name, dtype=c.dtype, role=c.role, unit=c.unit, additive=c.additive, distinct=c.distinct,
                           granularity=c.period_granularity, period_group=c.stats.get("period_group", "")) for c in norm.columns}
    return DatasetView(id="ds1", name=name, file_id="f1", file_name=name, file_role=role, period_label="", origin="csv",
                       locator={"file": name}, approximate=False, df=norm.df, columns=cols, period_groups=norm.period_groups)


def pdata(*datasets, docs=(), focus=""):
    return ProjectData(project={"focus_entity": focus}, datasets=list(datasets), documents=list(docs), files=[], quality_issues=[])


# ----------------------------------------------------------------- stats
def test_basic_stats():
    assert pct_change(100, 150) == 50
    assert pct_change(0, 10) is None
    assert cagr(100, 121, 2) == pytest.approx(10.0)
    assert cagr(-1, 10, 2) is None
    st = trend_stats(pd.Series([100, 110, 121, 133.1], index=["2021", "2022", "2023", "2024"]), "year")
    assert st["pct_change"] == pytest.approx(33.1)
    assert st["cagr"] == pytest.approx(10.0, abs=0.01)
    assert st["direction"] == "increasing" and st["r_squared"] > 0.99
    assert trend_stats(pd.Series([5.0], index=["2024"]), "year") is None  # insufficient data → no metric


# ----------------------------------------------------------------- trends
def test_trend_engine_annual_headline_and_segments():
    rows = []
    for y in (2023, 2024, 2025):
        for m in range(1, 13):
            rows.append({"Month": f"{y}-{m:02d}-01", "Product": "Alpha", "Revenue": 100 * (1.5 ** (y - 2023))})
            rows.append({"Month": f"{y}-{m:02d}-01", "Product": "Beta", "Revenue": 100 * (0.8 ** (y - 2023))})
    ds = view(pd.DataFrame(rows))
    fs = analyse_trends(pdata(ds), "deep")
    overall = next(f for f in fs if f.key == "trend:ds1:Revenue")
    # complete-year totals: 2023 = 2400, 2025 = 1200*2.25 + 1200*0.64 = 3468
    assert overall.details["stats"]["headline"]["pct_change"] == pytest.approx((3468 - 2400) / 2400 * 100)
    alpha = next(f for f in fs if f.details.get("value") == "Alpha")
    beta = next(f for f in fs if f.details.get("value") == "Beta")
    assert alpha.details["stats"]["pct_change"] == pytest.approx(125.0)
    assert beta.details["stats"]["pct_change"] == pytest.approx(-36.0)
    assert alpha.confidence == "high" and alpha.evidence[0].locator["filter"] == {"Product": "Alpha"}
    assert "growing" in alpha.tags and "declining" in beta.tags


def test_no_trend_without_time():
    ds = view(pd.DataFrame({"Product": ["A", "B", "C"], "Revenue": [1, 2, 3]}))
    assert [f for f in analyse_trends(pdata(ds), "deep") if f.category == "trend"] == []


# ----------------------------------------------------------------- comparisons
def test_period_comparison_added_removed_improved():
    df = pd.DataFrame({"Year": [2024, 2024, 2024, 2025, 2025, 2025],
                       "Channel": ["Meta", "TV", "Print", "Meta", "TV", "TikTok"],
                       "Revenue": [100, 100, 50, 150, 80, 40]})
    f = period_comparison(view(df), "deep")[0]
    d = f.details
    assert d["improved"] == ["Meta"] and d["declined"] == ["TV"]
    assert d["added"] == ["TikTok"] and d["removed"] == ["Print"]
    assert d["changes"]["Meta"] == pytest.approx(50.0)


def test_segment_concentration():
    df = pd.DataFrame({"Channel": ["A", "B", "C", "D"], "Revenue": [700, 100, 100, 100]})
    f = segment_comparison(view(df), "deep")[0]
    assert f.category == "concentration" and f.details["shares"]["A"] == pytest.approx(70.0)


def test_plan_vs_actual():
    df = pd.DataFrame({"Category": ["A", "B"], "Plan Revenue": [100, 200], "Actual Revenue": [80, 230]})
    f = plan_vs_actual(view(df))[0]
    assert f.records["comparison"]["value_a"] == 300 and f.records["comparison"]["value_b"] == 310
    assert f.details["behind"] == ["A"]


# ----------------------------------------------------------------- marketing / ROAS
def _mkt():
    rows = []
    for y, meta_r, tv_r in ((2024, 4.0, 2.0), (2025, 5.0, 1.0)):
        for m in range(1, 13):
            rows.append({"Month": f"{y}-{m:02d}-01", "Channel": "Meta", "Spend": 100, "Impressions": 10000, "Clicks": 100, "Revenue": 100 * meta_r})
            rows.append({"Month": f"{y}-{m:02d}-01", "Channel": "TV", "Spend": 300, "Impressions": 20000, "Clicks": 0, "Revenue": 300 * tv_r})
    return view(pd.DataFrame(rows), "marketing.csv", "marketing_data")


def test_roas_computed_from_sums_and_decomposition_adds_up():
    fs = analyse_marketing(pdata(_mkt()), "deep")
    summary = next(f for f in fs if f.key.startswith("mkt:summary"))
    # total revenue / total spend = (1200*4.5 + 3600*1.5) / 9600
    assert summary.details["totals"]["ROAS"] == pytest.approx((100 * 12 * 9 + 300 * 12 * 3) / 9600)
    by_ch = next(f for f in fs if f.key.startswith("roas:") and f.details.get("dimension") == "Channel")
    assert by_ch.details["table"]["Meta"]["ROAS"] == pytest.approx(4.5)
    assert by_ch.details["table"]["TV"]["ROAS"] == pytest.approx(1.5)
    drv = next(f for f in fs if f.key.startswith("roasdrv:"))
    total_delta = drv.details["roas_b"] - drv.details["roas_a"]
    assert sum(drv.details["contribution"].values()) == pytest.approx(total_delta)
    assert drv.details["roas_a"] == pytest.approx(2.5) and drv.details["roas_b"] == pytest.approx(2.0)
    assert "TV" in drv.title
    assert drv.confidence in ("medium", "low")  # attribution ≠ causation


def test_measurement_gap_for_untracked_channel():
    fs = analyse_gaps(pdata(_mkt()), "deep", {"roas"})
    assert any("TV" in f.title and "measurement" in f.tags for f in fs)


# ----------------------------------------------------------------- opportunity
def _market():
    rows = []
    spec = {"Crowded": (12, 0.02), "Niche Growth": (2, 0.40), "Niche Decline": (2, -0.20), "Mid": (6, 0.05)}
    for cat, (n, g) in spec.items():
        for i in range(n):
            rows.append({"Brand": f"B{i}", "Product": f"{cat} {i}", "Category": cat, "Format": "Tablet" if i % 2 else "Capsule",
                         "Price": 10 + i, "Sales 2024": 1000, "Sales 2025": 1000 * (1 + g)})
    rows.append({"Brand": "Us", "Product": "Us Crowded", "Category": "Crowded", "Format": "Tablet", "Price": 9, "Sales 2024": 1000, "Sales 2025": 1000})
    return view(pd.DataFrame(rows), "competitors.csv", "competitor_data")


def test_low_competition_is_not_automatically_an_opportunity():
    fs = analyse_opportunities(pdata(_market(), focus="Us"), "deep")
    by_seg = {f.details.get("segment"): f for f in fs if f.category == "opportunity"}
    assert by_seg["Niche Growth"].details["classification"] == "potential_opportunity"
    assert by_seg["Niche Decline"].details["classification"] == "low_competition_weak_demand"
    assert "NOT an opportunity" in by_seg["Niche Decline"].title
    assert "Crowded" not in by_seg
    assert all(f.confidence != "high" for f in by_seg.values())  # opportunities always need validation
    assert by_seg["Niche Growth"].validation
    gap = next(f for f in fs if f.category == "gap")
    assert "Niche Growth" in gap.fact  # focus brand absent from these segments


def test_opportunity_requires_competitor_information():
    df = pd.DataFrame({"Product": [f"P{i}" for i in range(10)], "Category": ["A", "B", "C", "D", "E"] * 2, "Revenue": range(10)})
    assert analyse_opportunities(pdata(view(df, role="product_data")), "deep") == []


# ----------------------------------------------------------------- strategy vs data
def test_strategy_mismatch_detected():
    from app.analytics.documents import strategy_comparison

    old = DocumentView("d1", "old.docx", "previous_strategy", "", "docx", 0, [
        Chunk("c1", "d1", "list_item", "", "Increase Meta investment by 15% to drive sales.", {"paragraph": 1}, False),
        Chunk("c2", "d1", "list_item", "", "Grow print advertising in regional newspapers.", {"paragraph": 2}, False)])
    new = DocumentView("d2", "new.docx", "proposed_strategy", "", "docx", 1, [
        Chunk("c3", "d2", "list_item", "", "Increase Meta investment by 25% to drive sales.", {"paragraph": 1}, False),
        Chunk("c4", "d2", "list_item", "", "Increase TV investment by 30% to rebuild awareness.", {"paragraph": 2}, False)])
    fs = strategy_comparison(pdata(_mkt(), docs=[old, new]), "deep")
    diff = next(f for f in fs if f.key.startswith("sdiff"))
    assert any("TV" in s for s in diff.details["added"]) and any("print" in s for s in diff.details["removed"])
    assert diff.details["modified"][0]["to"].startswith("Increase Meta investment by 25%")
    mismatch = [f for f in fs if "mismatch" in f.tags]
    assert mismatch and "tv" in mismatch[0].tags
    assert any(e.chunk_id == "c4" for e in mismatch[0].evidence)  # traceable to the strategy paragraph
