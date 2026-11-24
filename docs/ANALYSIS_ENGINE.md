# Analysis engine

All analysis is deterministic Python (`backend/app/analytics`). Nothing is invented: if data is insufficient the
engine says *"The available data is insufficient to determine this."* and lists the data needed.

## 1. Understanding the data (`profiling.py`)
* **Header detection** skips title rows; **row numbers** of the original file are kept for traceability.
* **Types:** numbers (currency symbols, thousands separators, `%`, `(negatives)`, `k/m/bn`, header scales such as
  `(£m)`), dates (ISO, d/m/y vs m/d/y detection, month names, `Q1 2024`, years), categories, text, identifiers.
* **Wide tables** (`Category | 2023 | 2024 | 2025`) are reshaped to long format; measures spread over period columns
  (`Sales 2024`, `Sales 2025`) are recognised as a time series.
* **Semantic roles** from column names: revenue, spend, units, impressions, clicks, conversions, ROAS/CTR/CPC/CPM/CPA,
  price, margin, share, market size, rating, plan/actual, time, product, category, brand/company, channel, campaign,
  geography, customer segment, attribute.
* **Data quality:** missing values, duplicates, spelling variants, mixed date formats, mixed currencies, mixed
  units, negative/impossible values (incl. clicks > impressions), extreme values, missing periods, incomplete latest
  period, categories that disappear. Spelling variants are harmonised **in the analysis copy only** and recorded.

## 2. Engines (`pipeline.py` runs them per mode)
| Engine | What it does | Safeguards |
|---|---|---|
| Trends | Overall and per segment: start/end, absolute & % change, CAGR, linear fit/R², direction, acceleration, 3-period moving average, share shift, contribution to change | Headline changes use **complete years** for monthly data (avoids seasonal month-vs-month noise); incomplete latest period excluded; no metric with < 2 periods |
| Comparisons | Year vs year by segment (improved / declined / stable / added / removed), segment shares & concentration (HHI), plan vs actual, source vs source | Source differences flagged as "may differ in scope" (low confidence) |
| Marketing & ROAS | KPIs recomputed from summed components; ROAS by channel/campaign; spend share vs revenue share; ROAS without each channel; **ROAS change decomposition** (spend-mix effect + efficiency effect per channel, sums exactly to the total change); inefficient spend; diminishing-returns check | Always states attributed ≠ causal; recommends incrementality tests |
| Opportunities | Per category/segment: competitor count, demand size, growth, price, rating, focus-brand presence → classification | **Low competition is not an opportunity by itself**: segments with few competitors but falling demand are explicitly labelled "NOT an opportunity"; opportunities never get high confidence; USP ideas from under-represented attributes, fast-growing formats and price-ladder gaps |
| Documents | Quantified statements reported in documents (labelled as reported, unverified); themes (TF-IDF); contradictions between reports and data for the same year | Entity ↔ percentage pairing by position in the sentence |
| Strategy | Statement-level diff of previous vs proposed strategy (added / removed / changed / unchanged, changed targets); **strategy vs data**: each statement's entities and direction (increase / reduce / maintain) checked against performance → carry forward / reconsider / requires validation; strong performers the strategy ignores | Measurement statements are not treated as investment decisions |
| Anomalies & relationships | Unusual period-to-period jumps (robust z-score), outlier segments, correlations (Spearman/Pearson, p-values) | Correlations always flagged as association only |
| Gaps | Data, customer, measurement (spend without outcomes, untracked offline channels), market/channel/distribution/geographic whitespace (empty cross-tab cells), pricing, portfolio, execution, strategy, research gaps | Each with the 7 required fields |
| Impact | "If the trend continues": straight-line extrapolation with range for short and medium term; long term is direction only | Labelled speculative; confidence medium at best |
| Research | What we know / don't know / should find out / how (methods) / priority; assumptions and hypotheses in Research mode; future questions | — |

## 3. Confidence
* **High** – directly computed from reliable data (e.g. 4+ periods with a good fit, exact sums).
* **Medium** – supported by data but needs interpretation (2–3 periods, attributed ROAS, opportunities).
* **Low** – hypothesis or weak evidence (correlations < 0.7, unverified demand, source disagreements).
* Anything using approximate (image/OCR) values is capped at **Medium** and labelled.

## 4. Ranking
Score = magnitude (27%) + relevance to your questions (20%) + confidence (18%) + evidence strength (10%) +
business impact of the metric (17%) + strategic categories bonus; repeated findings about the same subject are
down-weighted (uniqueness). The Results page shows the top 10 Key Findings with **Show more**.

## 5. Questions
Each question is mapped to intents (trend, gap, opportunity, ROAS, strategy, risk, research…) and answered from the
most relevant findings, or marked insufficient with the data needed. "I don't know" adds questions derived from what
the data supports (*What can I do with this data?*).

## 6. Ask Your Data (`ask.py`)
Handles: compare A vs B; why did X change (where the change came from by other dimensions + other sources, with
hypotheses labelled); which X are growing/declining; top/bottom by a measure; totals (optionally by year); ROAS
drivers; gaps / opportunities / risks / what to investigate next; and an evidence-search fallback over findings and
document passages. If a provider is configured, an AI-written summary of the computed facts may be added and
labelled as such.

## 7. Limitations
See README §11. Main ones: image chart reading supports vertical bar charts only; scanned tables are read as text;
Ask Your Data is rule-based (not a general chatbot); extrapolations are straight lines.
