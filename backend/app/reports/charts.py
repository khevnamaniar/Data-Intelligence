"""Render chart specifications (the same specs the web UI draws) to PNG for PDF/DOCX exports."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

PALETTE = ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#dc2626", "#0891b2", "#65a30d", "#db2777"]


def _fmt_axis(ax, unit: str):
    from matplotlib.ticker import FuncFormatter

    def f(v, _):
        a = abs(v)
        if unit == "%":
            return f"{v:.0f}%"
        if unit == "x":
            return f"{v:.1f}x"
        sign = {"GBP": "£", "USD": "$", "EUR": "€", "INR": "₹"}.get(unit, "")
        if a >= 1e9:
            return f"{sign}{v / 1e9:.1f}bn"
        if a >= 1e6:
            return f"{sign}{v / 1e6:.1f}m"
        if a >= 1e3:
            return f"{sign}{v / 1e3:.0f}k"
        return f"{sign}{v:.1f}" if a < 10 else f"{sign}{v:.0f}"

    ax.yaxis.set_major_formatter(FuncFormatter(f))


def render_chart(spec: dict, width: float = 7.0, height: float = 3.4) -> bytes | None:
    if not spec:
        return None
    t = spec.get("type")
    try:
        fig, ax = plt.subplots(figsize=(width, height), dpi=150)
        cats = [str(c) for c in spec.get("categories", [])]
        series = spec.get("series", [])
        unit = spec.get("unit", "")
        if t == "line":
            for i, s in enumerate(series[:6]):
                vals = [np.nan if v is None else v for v in s["values"]]
                ax.plot(range(len(vals)), vals, label=s["name"], color=PALETTE[i % 8], lw=1.8,
                        ls="--" if s.get("style") == "dashed" else "-")
            step = max(1, len(cats) // 10)
            ax.set_xticks(range(0, len(cats), step))
            ax.set_xticklabels(cats[::step], rotation=30, ha="right", fontsize=7)
        elif t in ("bar", "grouped_bar"):
            n = max(1, len(series))
            w = 0.8 / n
            x = np.arange(len(cats))
            for i, s in enumerate(series[:6]):
                vals = [0 if v is None else v for v in s["values"]]
                ax.bar(x + (i - (n - 1) / 2) * w, vals, w, label=s["name"], color=PALETTE[i % 8])
            ax.set_xticks(x)
            ax.set_xticklabels(cats, rotation=30 if len(cats) > 5 else 0, ha="right" if len(cats) > 5 else "center", fontsize=7)
        elif t == "stacked_bar":
            x = np.arange(len(cats))
            bottom = np.zeros(len(cats))
            for i, s in enumerate(series[:8]):
                vals = np.array([0 if v is None else v for v in s["values"]], dtype=float)
                ax.bar(x, vals, 0.7, bottom=bottom, label=s["name"], color=PALETTE[i % 8])
                bottom += vals
            step = max(1, len(cats) // 10)
            ax.set_xticks(x[::step])
            ax.set_xticklabels(cats[::step], rotation=30, ha="right", fontsize=7)
        elif t == "waterfall":
            vals = [0 if v is None else v for v in (series[0]["values"] if series else [])]
            x = np.arange(len(vals))
            colors = ["#0d9488" if v >= 0 else "#dc2626" for v in vals]
            ax.bar(x, vals, 0.6, color=colors)
            ax.axhline(0, color="#64748b", lw=0.8)
            ax.set_xticks(x)
            ax.set_xticklabels(cats, rotation=30, ha="right", fontsize=7)
        elif t in ("scatter", "matrix"):
            pts = spec.get("points", [])
            xs = [p["x"] for p in pts]
            ys = [p["y"] for p in pts]
            sizes = [p.get("size", 1) for p in pts]
            if sizes and max(sizes) > 0:
                sizes = [30 + 400 * (s / max(sizes)) for s in sizes]
            ax.scatter(xs, ys, s=sizes or 30, alpha=0.6, color=PALETTE[0])
            if len(pts) <= 20:
                for p in pts:
                    if p.get("label"):
                        ax.annotate(str(p["label"])[:18], (p["x"], p["y"]), fontsize=6, xytext=(3, 3), textcoords="offset points")
            if spec.get("reference_line") == "y=x" and xs:
                m = max(max(xs), max(ys))
                ax.plot([0, m], [0, m], ls="--", color="#94a3b8", lw=0.8)
            if t == "matrix":
                if spec.get("x_split") is not None:
                    ax.axvline(spec["x_split"], ls=":", color="#94a3b8")
                if spec.get("y_split") is not None:
                    ax.axhline(spec["y_split"], ls=":", color="#94a3b8")
            ax.set_xlabel(spec.get("x_label", ""), fontsize=8)
            ax.set_ylabel(spec.get("y_label", ""), fontsize=8)
        elif t == "heatmap":
            vals = np.array(spec.get("values", []), dtype=float)
            if vals.size == 0:
                plt.close(fig)
                return None
            im = ax.imshow(vals, cmap="Blues", aspect="auto")
            ax.set_yticks(range(len(spec["rows"])))
            ax.set_yticklabels(spec["rows"], fontsize=7)
            ax.set_xticks(range(len(spec["cols"])))
            ax.set_xticklabels(spec["cols"], rotation=30, ha="right", fontsize=7)
            for (i, j), v in np.ndenumerate(vals):
                if v == 0:
                    ax.text(j, i, "0", ha="center", va="center", fontsize=6, color="#dc2626")
            fig.colorbar(im, ax=ax, fraction=0.03)
        else:
            plt.close(fig)
            return None
        if t not in ("heatmap", "scatter", "matrix"):
            _fmt_axis(ax, unit)
        ax.set_title(spec.get("title", "")[:90], fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=7)
        if t in ("line", "grouped_bar", "stacked_bar") and len(series) > 1:
            ax.legend(fontsize=6, frameon=False, ncol=min(3, len(series)))
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        plt.close("all")
        return None
