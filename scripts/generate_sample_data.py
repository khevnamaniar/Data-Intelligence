"""Generate the fictional 'Consumer Health Market Analysis' demo files.

All companies, brands, products and figures are invented. Run:
    python scripts/generate_sample_data.py
Files are written to sample-data/consumer-health/.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "sample-data" / "consumer-health"
RNG = np.random.default_rng(42)
FOCUS = "Verdant Health"

CATEGORIES = {
    # category: (base monthly revenue for Verdant, annual growth, gummy share)
    "Multivitamins": (210_000, -0.09, 0.15),
    "Vitamin D": (120_000, 0.03, 0.30),
    "Probiotics": (95_000, 0.24, 0.10),
    "Sleep Aids": (60_000, 0.38, 0.45),
    "Omega-3": (80_000, -0.02, 0.05),
    "Immune Support": (110_000, 0.01, 0.35),
    "Joint Health": (70_000, 0.05, 0.05),
}
FORMAT_BY_CAT = {
    "Multivitamins": ["Tablet", "Gummy"], "Vitamin D": ["Tablet", "Gummy", "Liquid"], "Probiotics": ["Capsule", "Powder"],
    "Sleep Aids": ["Gummy", "Capsule"], "Omega-3": ["Capsule"], "Immune Support": ["Tablet", "Gummy"], "Joint Health": ["Tablet", "Capsule"],
}
CHANNELS = {"Pharmacy": (0.42, -0.04), "Grocery": (0.25, -0.10), "E-commerce": (0.23, 0.30), "Direct-to-consumer": (0.10, 0.22)}
REGIONS = {"North": 0.30, "South": 0.27, "East": 0.23, "West": 0.20}


def own_products():
    rows = []
    for cat, fmts in FORMAT_BY_CAT.items():
        for f in fmts:
            rows.append({"Product": f"Verdant {cat} {f}", "Category": cat, "Format": f})
    return rows


def sales_dataset() -> pd.DataFrame:
    months = pd.date_range("2023-01-01", "2025-12-01", freq="MS")
    rows = []
    for p in own_products():
        base, growth, gummy_share = CATEGORIES[p["Category"]]
        n_fmt = len(FORMAT_BY_CAT[p["Category"]])
        share = gummy_share if p["Format"] == "Gummy" else (1 - gummy_share) / max(n_fmt - 1, 1) if n_fmt > 1 else 1.0
        fmt_growth = growth + (0.15 if p["Format"] == "Gummy" else 0)
        for i, m in enumerate(months):
            t = i / 12
            season = 1 + 0.12 * np.cos((m.month - 1) / 12 * 2 * np.pi) if p["Category"] in ("Immune Support", "Vitamin D") else 1.0
            for ch, (ch_share, ch_growth) in CHANNELS.items():
                for rg, rg_share in REGIONS.items():
                    rev = base * share * ch_share * rg_share * (1 + fmt_growth) ** t * (1 + ch_growth) ** t * season
                    rev *= RNG.normal(1, 0.06)
                    price = {"Tablet": 9.5, "Capsule": 14.0, "Gummy": 12.5, "Liquid": 11.0, "Powder": 19.0}[p["Format"]]
                    rows.append({"Month": m.strftime("%Y-%m-%d"), "Product": p["Product"], "Category": p["Category"],
                                 "Format": p["Format"], "Channel": ch, "Region": rg,
                                 "Revenue (GBP)": round(rev, 2), "Units": int(rev / price)})
    df = pd.DataFrame(rows)
    # deliberate, realistic data-quality issues
    idx = RNG.choice(df.index, 40, replace=False)
    df.loc[idx[:25], "Units"] = None
    df.loc[idx[25:], "Channel"] = df.loc[idx[25:], "Channel"].str.lower()
    df = pd.concat([df, df.iloc[[100, 101]]], ignore_index=True)  # two duplicate rows
    return df


def competitor_dataset() -> pd.DataFrame:
    # category: (number of competitor products, market growth, market size £m, avg price)
    market = {
        "Multivitamins": (28, -0.04, 410, 8.9), "Vitamin D": (22, 0.04, 260, 7.5), "Probiotics": (15, 0.19, 230, 21.0),
        "Sleep Aids": (9, 0.27, 120, 13.5), "Omega-3": (17, -0.01, 190, 15.0), "Immune Support": (20, 0.02, 240, 10.0),
        "Joint Health": (12, 0.05, 150, 16.5), "Magnesium": (4, 0.31, 70, 11.0), "Collagen": (5, -0.12, 55, 24.0),
        "Kids Vitamins": (19, 0.03, 140, 8.0),
    }
    brands = ["NovaVita", "PureRoot", "Helix Wellness", "BrightLeaf", "CoreNutri", "Sunvale", "Optima Labs", "Kindly Co", "Meridian", FOCUS]
    fmts_all = ["Tablet", "Capsule", "Gummy", "Powder", "Liquid"]
    rows = []
    for cat, (n, g, size, price) in market.items():
        per_product = size * 1e6 / (n + 2)
        for i in range(n):
            brand = brands[i % (len(brands) - 1)]
            fmt = fmts_all[int(RNG.integers(0, 3 if cat not in ("Sleep Aids", "Magnesium") else 2))]
            if cat == "Sleep Aids" and i < 6:
                fmt = "Capsule"
            if cat == "Magnesium":
                fmt = "Tablet" if i < 3 else "Capsule"
            s24 = per_product * RNG.lognormal(0, 0.5)
            s25 = s24 * (1 + g + RNG.normal(0, 0.05))
            rows.append({"Brand": brand, "Product": f"{brand} {cat} {fmt} {i + 1}", "Category": cat, "Format": fmt,
                         "Price (GBP)": round(price * RNG.normal(1, 0.18), 2), "Rating": round(min(5, RNG.normal(4.1, 0.35)), 1),
                         "Sales 2024 (GBP)": round(s24), "Sales 2025 (GBP)": round(s25)})
        if cat in CATEGORIES:  # Verdant's own products also appear in the market dataset
            for f in FORMAT_BY_CAT[cat]:
                s24 = per_product * 1.4
                g2 = CATEGORIES[cat][1] + (0.15 if f == "Gummy" else 0)
                rows.append({"Brand": FOCUS, "Product": f"Verdant {cat} {f}", "Category": cat, "Format": f,
                             "Price (GBP)": round(price * 1.05, 2), "Rating": 4.2,
                             "Sales 2024 (GBP)": round(s24), "Sales 2025 (GBP)": round(s24 * (1 + g2))})
    df = pd.DataFrame(rows)
    df.loc[5, "Price (GBP)"] = None
    return df


def marketing_dataset() -> pd.DataFrame:
    months = pd.date_range("2024-01-01", "2025-12-01", freq="MS")
    # channel: (monthly spend start, spend growth/yr, roas start, roas change/yr, cpm, ctr, cvr)
    ch = {
        "Meta": (60_000, 0.25, 4.2, 0.35, 7.0, 0.012, 0.035),
        "Google Search": (45_000, 0.10, 5.1, 0.05, 25.0, 0.045, 0.06),
        "TikTok": (12_000, 0.90, 2.4, 0.9, 5.0, 0.009, 0.02),
        "TV": (90_000, -0.05, 1.6, -0.35, 18.0, 0.0, 0.0),
        "Retail Media": (25_000, 0.30, 3.6, 0.1, 9.0, 0.006, 0.05),
        "YouTube": (20_000, 0.05, 1.9, -0.05, 10.0, 0.004, 0.015),
    }
    campaigns = {"Always-on": 0.6, "Winter Immunity": 0.25, "Sleep Well Launch": 0.15}
    rows = []
    for i, m in enumerate(months):
        t = i / 12
        for c, (sp, sg, roas, rg, cpm, ctr, cvr) in ch.items():
            for camp, w in campaigns.items():
                if camp == "Winter Immunity" and m.month not in (10, 11, 12, 1, 2):
                    continue
                if camp == "Sleep Well Launch" and m < pd.Timestamp("2025-03-01"):
                    continue
                spend = sp * w * (1 + sg) ** t * RNG.normal(1, 0.07)
                r = max(0.3, (roas + rg * t) * RNG.normal(1, 0.08)) * (1.15 if camp == "Sleep Well Launch" else 1)
                imps = spend / cpm * 1000
                clicks = imps * ctr * RNG.normal(1, 0.05)
                conv = clicks * cvr if cvr else spend * r / 30 / 2.2
                rows.append({"Month": m.strftime("%b %Y"), "Channel": c, "Campaign": camp, "Spend (GBP)": round(spend, 2),
                             "Impressions": int(imps), "Clicks": int(clicks), "Conversions": int(conv),
                             "Revenue (GBP)": round(spend * r, 2)})
    df = pd.DataFrame(rows)
    df.loc[17, "Clicks"] = df.loc[17, "Impressions"] + 500  # impossible value
    return df


def product_dataset() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for p in own_products():
        fmt = p["Format"]
        rows.append({**p, "Launch Date": f"{RNG.integers(2015, 2025)}-{RNG.integers(1, 13):02d}-01",
                     "Price (GBP)": {"Tablet": 9.5, "Capsule": 14.0, "Gummy": 12.5, "Liquid": 11.0, "Powder": 19.0}[fmt],
                     "Gross Margin %": f"{RNG.integers(38, 64)}%", "Rating": round(RNG.normal(4.2, 0.2), 1)})
    plan = []
    for cat, (base, growth, _) in CATEGORIES.items():
        actual = base * 12 * (1 + growth) ** 2.5
        target = base * 12 * (1.06 ** 2.5) if cat != "Sleep Aids" else base * 12 * 1.25
        plan.append({"Category": cat, "Plan Revenue 2025 (GBP)": round(target), "Actual Revenue 2025 (GBP)": round(actual)})
    return pd.DataFrame(rows), pd.DataFrame(plan)


def market_report_pdf(path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib import colors

    cats = ["Multivitamins", "Vitamin D", "Probiotics", "Sleep Aids", "Magnesium", "Collagen"]
    growth = [-4, 4, 19, 27, 31, -12]
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=150)
    bars = ax.bar(cats, [max(g, 0.5) if g > 0 else 0.5 for g in growth], color="#2f6fb3")
    for b, g in zip(bars, growth):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.6, f"{g}%", ha="center", fontsize=10)
    ax.set_title("Figure 2: 2025 value growth by category (positive growth shown)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=8)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm)
    story = [
        Paragraph("UK Consumer Health Market Report 2025", styles["Title"]),
        Paragraph("Prepared by Northgate Insights (fictional) for demonstration purposes only.", styles["Italic"]),
        Spacer(1, 12),
        Paragraph("Executive summary", styles["Heading2"]),
        Paragraph("The UK vitamins and supplements market reached £1.87bn in 2025, growing 3.1% year on year. "
                  "Growth is increasingly concentrated in need-state categories: Sleep Aids grew 27% and Probiotics grew 19%, "
                  "while Multivitamins declined 4% as shoppers move to targeted solutions.", styles["Normal"]),
        Spacer(1, 6),
        Paragraph("Magnesium is an emerging category, growing 31% from a small base of £70m, with only four branded products "
                  "currently on shelf. Collagen also has few competitors but declined 12% in 2025 after two years of heavy promotion.¹", styles["Normal"]),
        Spacer(1, 6),
        Paragraph("Gummy formats now account for 34% of category sales, up from 26% in 2023. E-commerce is the fastest-growing "
                  "channel, now representing 29% of value sales, while grocery share fell to 21%.", styles["Normal"]),
        Spacer(1, 10),
        Paragraph("Table 1: Market size by category (£m)", styles["Heading4"]),
    ]
    tbl = [["Category", "2023", "2024", "2025"],
           ["Multivitamins", "445", "427", "410"], ["Vitamin D", "240", "250", "260"], ["Probiotics", "162", "193", "230"],
           ["Sleep Aids", "74", "94", "120"], ["Omega-3", "194", "192", "190"], ["Immune Support", "232", "235", "240"],
           ["Magnesium", "41", "53", "70"], ["Collagen", "71", "63", "55"]]
    t = Table(tbl, hAlign="LEFT")
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    story += [t, Spacer(1, 8), Paragraph("Source: Northgate retail panel, 52 weeks to December 2025 (fictional).", small),
              PageBreak(), Paragraph("Category dynamics", styles["Heading2"]),
              Paragraph("Shoppers report that sleep quality and gut health are their two most important wellbeing priorities. "
                        "Competition in Sleep Aids is moderate, with nine products, most of them capsules. "
                        "Retailers indicate limited range space for new Multivitamin launches.", styles["Normal"]),
              Spacer(1, 8), Image(buf, width=16 * cm, height=8.2 * cm), Spacer(1, 6),
              Paragraph("Risks: rising media costs on social platforms, and possible regulation of health claims on gummy products, "
                        "could slow growth over the next two years.", styles["Normal"]),
              Spacer(1, 40),
              Paragraph("1 Collagen decline partly reflects the end of a major competitor's promotional programme.", small)]
    doc.build(story)


def strategy_docs(prev: Path, new: Path) -> None:
    import docx

    def build(path, title, sections):
        d = docx.Document()
        d.add_heading(title, 0)
        for heading, bullets in sections:
            d.add_heading(heading, 1)
            for b in bullets:
                d.add_paragraph(b, style="List Bullet")
        d.save(path)

    build(prev, "Verdant Health Marketing & Portfolio Strategy 2024", [
        ("Portfolio priorities", [
            "Grow Multivitamins as our core franchise through grocery distribution gains.",
            "Maintain the Vitamin D range with seasonal promotions in winter.",
            "Extend Probiotics into a powder format.",
            "Keep Collagen under review as a possible launch for 2025.",
        ]),
        ("Channel & media", [
            "TV remains the lead brand-building channel with 40% of the media budget.",
            "Increase Meta investment by 15% to drive online sales.",
            "Test TikTok with a small budget of £100k.",
            "Grow grocery as the main volume channel.",
        ]),
        ("Measurement", ["Report ROAS monthly for all digital channels."]),
    ])
    build(new, "Verdant Health Proposed Strategy 2026", [
        ("Portfolio priorities", [
            "Launch a Magnesium range in tablet and gummy formats in Q2 2026.",
            "Expand Sleep Aids with two new gummy products.",
            "Launch Collagen gummies to capture the low-competition beauty-from-within segment.",
            "Maintain the Vitamin D range with seasonal promotions in winter.",
            "Extend Probiotics into a powder format.",
        ]),
        ("Channel & media", [
            "Increase TV investment by 30% to rebuild brand awareness.",
            "Increase Meta investment by 20% to drive online sales.",
            "Scale TikTok to £600k following strong 2025 results.",
            "Prioritise E-commerce and Direct-to-consumer channels for new launches.",
        ]),
        ("Measurement", ["Report ROAS monthly for all digital channels.",
                         "Introduce marketing mix modelling to measure TV and offline effects."]),
    ])


def review_pptx(path: Path, sales: pd.DataFrame) -> None:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    s = sales.copy()
    s["Channel"] = s["Channel"].str.title().str.replace("E-Commerce", "E-commerce").str.replace("Direct-To-Consumer", "Direct-to-consumer")
    s["Year"] = s["Month"].str[:4]
    pivot = s.groupby(["Channel", "Year"])["Revenue (GBP)"].sum().unstack() / 1e6
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[0])
    sl.shapes.title.text = "Verdant Health – 2025 Business Review"
    sl.placeholders[1].text = "Internal, fictional demo data"
    sl = prs.slides.add_slide(prs.slide_layouts[5])
    sl.shapes.title.text = "Revenue by channel (£m)"
    cd = CategoryChartData()
    cd.categories = list(pivot.index)
    for y in pivot.columns:
        cd.add_series(str(y), [round(v, 2) for v in pivot[y]])
    sl.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.7), Inches(1.6), Inches(8.5), Inches(4.8), cd)
    sl.notes_slide.notes_text_frame.text = "E-commerce overtook Grocery in 2025. Pharmacy remains the largest channel."
    sl = prs.slides.add_slide(prs.slide_layouts[5])
    sl.shapes.title.text = "2026 priorities"
    tb = sl.shapes.add_textbox(Inches(0.7), Inches(1.6), Inches(8.5), Inches(3)).text_frame
    tb.text = "Launch Magnesium range"
    for t in ["Double down on Sleep Aids gummies", "Rebuild brand awareness with TV", "Improve Multivitamins profitability"]:
        tb.add_paragraph().text = t
    prs.save(path)


def survey_chart_png(path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["Price", "Efficacy", "Taste", "Brand", "Natural"]
    vals = [62, 71, 38, 24, 45]
    fig, ax = plt.subplots(figsize=(6.4, 3.8), dpi=140)
    bars = ax.bar(labels, vals, color="#3a8f5c")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v}%", ha="center", fontsize=12)
    ax.set_ylim(0, 85)
    ax.set_title("Shopper survey: top purchase drivers (% of respondents)")
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sales = sales_dataset()
    with pd.ExcelWriter(OUT / "sales_dataset.xlsx") as xw:
        pd.DataFrame([["Verdant Health monthly sell-out (fictional)"]]).to_excel(xw, sheet_name="Sales", header=False, index=False)
        sales.to_excel(xw, sheet_name="Sales", index=False, startrow=2)
    competitor_dataset().to_csv(OUT / "competitor_dataset.csv", index=False)
    marketing_dataset().to_csv(OUT / "marketing_dataset.csv", index=False)
    prod, plan = product_dataset()
    with pd.ExcelWriter(OUT / "product_dataset.xlsx") as xw:
        prod.to_excel(xw, sheet_name="Portfolio", index=False)
        plan.to_excel(xw, sheet_name="Plan vs Actual 2025", index=False)
    market_report_pdf(OUT / "market_report_2025.pdf")
    strategy_docs(OUT / "strategy_2024_previous.docx", OUT / "strategy_2026_proposed.docx")
    review_pptx(OUT / "business_review_2025.pptx", sales)
    survey_chart_png(OUT / "survey_purchase_drivers.png")
    (OUT / "README.md").write_text(
        "# Consumer Health demo data (fictional)\n\nEvery company, brand, product and number in these files is invented "
        "for demonstration and testing. 'Verdant Health' is the fictional focus company.\n\n"
        "| File | What it contains |\n|---|---|\n"
        "| market_report_2025.pdf | Market report with text, a table, a chart image and a footnote |\n"
        "| sales_dataset.xlsx | Monthly sales 2023–2025 by product, category, format, channel and region (with deliberate quality issues) |\n"
        "| competitor_dataset.csv | Competitor products with category, format, price, rating and sales |\n"
        "| marketing_dataset.csv | Monthly media spend, impressions, clicks, conversions and revenue by channel and campaign |\n"
        "| product_dataset.xlsx | Own portfolio, plus a Plan vs Actual sheet |\n"
        "| strategy_2024_previous.docx | Previous strategy |\n"
        "| strategy_2026_proposed.docx | Proposed strategy |\n"
        "| business_review_2025.pptx | Slides with a native chart and speaker notes |\n"
        "| survey_purchase_drivers.png | Screenshot of a bar chart (tests OCR / visual interpretation) |\n",
        encoding="utf-8")
    print(f"Sample data written to {OUT}")


if __name__ == "__main__":
    main()
