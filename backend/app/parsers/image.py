"""Image handling: OCR (Tesseract, free/open-source) and approximate chart reading.

Everything extracted from pixels is marked ``approximate`` — it is visually
interpreted, not exact.
"""
from __future__ import annotations

import io
import re
import shutil

import numpy as np
import pandas as pd
from PIL import Image, ImageOps

from .base import ExtractedChunk, ExtractedTable, ExtractionResult, ParserError

_NUM_RE = re.compile(r"^[\(\-−]?[$£€₹¥]?\d{1,3}(?:[,]\d{3})*(?:\.\d+)?[kKmMbB%]?\)?$|^[\(\-−]?[$£€₹¥]?\d+(?:\.\d+)?[kKmMbB%]?\)?$")


def tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def load_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ParserError("The image could not be opened.", f"Unsupported or corrupt image ({exc.__class__.__name__}).",
                          "Upload a PNG or JPEG image.")
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    return img.convert("RGB")


def ocr_words(img: Image.Image) -> list[dict]:
    """Return OCR'd words with bounding boxes and confidence."""
    import pytesseract

    scale = 1.0
    if max(img.size) < 1200:  # upscale small images for better OCR
        scale = 1200 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 11")
    words = []
    for i, text in enumerate(data["text"]):
        t = (text or "").strip()
        conf = float(data["conf"][i]) if str(data["conf"][i]) not in ("", "-1") else -1
        if not t or conf < 30:
            continue
        words.append({
            "text": t,
            "conf": conf,
            "x": data["left"][i] / scale, "y": data["top"][i] / scale,
            "w": data["width"][i] / scale, "h": data["height"][i] / scale,
        })
    return words


def ocr_text(img: Image.Image) -> str:
    import pytesseract

    if max(img.size) < 1200:
        s = 1200 / max(img.size)
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    return pytesseract.image_to_string(img, config="--psm 3")


def parse_number(text: str) -> float | None:
    t = text.strip().replace("−", "-").replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").lstrip("$£€₹¥")
    mult = 1.0
    if t and t[-1] in "kK":
        mult, t = 1e3, t[:-1]
    elif t and t[-1] in "mM":
        mult, t = 1e6, t[:-1]
    elif t and t[-1] in "bB":
        mult, t = 1e9, t[:-1]
    t = t.rstrip("%")
    try:
        v = float(t) * mult
    except ValueError:
        return None
    return -v if neg else v


def read_bar_chart(img: Image.Image) -> dict | None:
    """Try to read a simple vertical bar chart.

    Returns {"labels", "values", "method", "note"} or None. Values are approximate:
    read from data labels when present, otherwise from the y-axis scale, otherwise
    relative bar heights (largest bar = 100).
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return None
    arr = np.asarray(img.convert("RGB"))
    h, w = arr.shape[:2]
    if h < 80 or w < 80:
        return None
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    # bars: saturated colour or clearly darker than a light background, but not thin text
    mask = ((hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 40)) | (gray < 110)
    mask = mask.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        area = cv2.contourArea(cnt)
        if bw < max(6, w * 0.015) or bh < max(6, h * 0.03):
            continue
        if area < 0.8 * bw * bh:  # bars are solid rectangles
            continue
        if bw > w * 0.4 or bh > h * 0.95:
            continue
        rects.append((x, y, bw, bh))
    if len(rects) < 2:
        return None
    # bars in a vertical bar chart share a baseline
    bottoms = np.array([y + bh for x, y, bw, bh in rects])
    base = np.median(bottoms)
    tol = max(4, h * 0.02)
    bars = sorted([r for r in rects if abs(r[1] + r[3] - base) <= tol], key=lambda r: r[0])
    if len(bars) < 2:
        return None
    widths = np.array([b[2] for b in bars])
    if widths.max() > 2.5 * widths.min():
        return None

    words = ocr_words(img) if tesseract_available() else []
    labels, values, methods = [], [], []
    numeric = [wd for wd in words if _NUM_RE.match(wd["text"]) and parse_number(wd["text"]) is not None]

    # y-axis calibration from tick labels left of the first bar
    left_edge = bars[0][0]
    ticks = [wd for wd in numeric if wd["x"] + wd["w"] <= left_edge + 2]
    calib = None
    if len(ticks) >= 2:
        ys = np.array([t["y"] + t["h"] / 2 for t in ticks])
        vs = np.array([parse_number(t["text"]) for t in ticks])
        if np.ptp(ys) > 10 and np.ptp(vs) > 0:
            slope, intercept = np.polyfit(ys, vs, 1)
            if slope < 0:  # values increase upwards
                calib = (slope, intercept)

    centers = [x + bw / 2 for x, y, bw, bh in bars]
    spacing = float(np.median(np.diff(centers))) if len(centers) > 1 else w
    label_words: dict[int, list] = {i: [] for i in range(len(bars))}
    for wd in words:
        if not (base - 2 <= wd["y"] < base + h * 0.2):
            continue
        wc = wd["x"] + wd["w"] / 2
        nearest = int(np.argmin([abs(wc - c) for c in centers]))
        if abs(wc - centers[nearest]) <= spacing * 0.6:
            label_words[nearest].append(wd)
    for bi, (x, y, bw, bh) in enumerate(bars):
        cx = x + bw / 2
        below = label_words[bi]
        label = " ".join(wd["text"] for wd in sorted(below, key=lambda d: (round(d["y"] / 8), d["x"]))
                         if not _NUM_RE.match(wd["text"])) or f"Bar {len(labels) + 1}"
        above = [wd for wd in numeric if abs(wd["x"] + wd["w"] / 2 - cx) <= bw * 0.75 and y - h * 0.12 <= wd["y"] + wd["h"] <= y + 4]
        if above:
            v = parse_number(sorted(above, key=lambda d: -(d["y"]))[0]["text"])
            values.append(v)
            methods.append("data label")
        elif calib:
            values.append(round(float(calib[0] * y + calib[1]), 2))
            methods.append("axis scale")
        else:
            values.append(None)
            methods.append("relative")
        labels.append(label)
    if all(m == "relative" for m in methods):
        tallest = max(b[3] for b in bars)
        values = [round(100 * b[3] / tallest, 1) for b in bars]
        method = "relative bar height (tallest bar = 100)"
    else:
        method = ", ".join(sorted(set(methods)))
        values = [v if v is not None else None for v in values]
    return {"labels": labels, "values": values, "method": method,
            "note": "Approximate / visually interpreted from a chart image"}


def analyse_image(img: Image.Image, res: ExtractionResult, locator: dict, name: str, ocr_enabled: bool = True,
                  min_chars: int = 3) -> None:
    """OCR an image and try to read a chart; append results to ``res``."""
    chart = None
    try:
        chart = read_bar_chart(img)
    except Exception as exc:  # pragma: no cover - defensive
        res.warnings.append(f"Chart reading failed for {name}: {exc.__class__.__name__}")
    if chart:
        df = pd.DataFrame({"Label": chart["labels"], "Value": chart["values"]})
        res.tables.append(ExtractedTable(name=f"{name} (chart, approximate)", df=df, origin="image_chart",
                                         locator={**locator, "method": chart["method"]},
                                         row_numbers=list(range(1, len(df) + 1)), approximate=True, note=chart["note"]))
    if not ocr_enabled:
        res.warnings.append("OCR is turned off (OCR_ENABLED=false); text inside images was not read.")
        return
    if not tesseract_available():
        res.error("Text in the image could not be read.", "The free OCR engine (Tesseract) is not installed on this computer.",
                  "File was stored; any chart shapes were still analysed.",
                  "Install Tesseract (see README → Optional tools) and click Re-process.")
        return
    try:
        text = ocr_text(img)
    except Exception as exc:
        res.error("OCR failed for this image.", str(exc)[:200], next_step="Try a clearer or larger image.")
        return
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    for i, p in enumerate(paragraphs):
        if len(p) >= min_chars:
            res.chunks.append(ExtractedChunk(kind="ocr", text=p, locator={**locator, "block": i + 1}, approximate=True))
    res.metadata["ocr_used"] = True


def parse_image(data: bytes, filename: str, ocr_enabled: bool = True) -> ExtractionResult:
    res = ExtractionResult("image")
    img = load_image(data)
    res.metadata.update({"width": img.width, "height": img.height})
    analyse_image(img, res, {"file": filename}, filename, ocr_enabled=ocr_enabled)
    if not res.tables and not res.chunks and not res.errors:
        res.warnings.append("No readable text or chart data was found in the image.")
    return res
