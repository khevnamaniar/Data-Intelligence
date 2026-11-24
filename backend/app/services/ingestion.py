"""Upload → validation → extraction → normalisation → data quality → source indexing."""
from __future__ import annotations

import gzip
import hashlib
import io
import logging
import traceback

import pandas as pd

from ..config import settings
from ..db import Connection, get_db, new_id, now
from ..parsers import ParserError, detect_type, parse_file, safe_filename
from ..analytics.profiling import ROW_COL, normalize_table, profile_summary, reshape_wide
from ..storage import get_storage

log = logging.getLogger("dip.ingestion")

ROLE_CHOICES = {
    "auto", "report", "dataset", "previous_strategy", "current_strategy", "proposed_strategy", "plan",
    "competitor_data", "marketing_data", "sales_data", "product_data", "customer_data", "survey", "other",
}


def file_key(project_id: str, file_id: str, safe_name: str) -> str:
    """Storage path for an upload. The file's real name is kept in the database (shown to users and used
    for downloads); the storage path only uses plain characters, so names with spaces, brackets or
    accents are always accepted by every storage provider."""
    import re

    stem, dot, ext = safe_name.rpartition(".")
    if not dot:
        stem, ext = safe_name, ""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:100] or "file"
    ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:10].lower()
    return f"projects/{project_id}/files/{file_id}/original/{slug}{'.' + ext if ext else ''}"


def guess_role(name: str, file_type: str) -> str:
    n = name.lower()
    if "strategy" in n or "plan" in n and file_type in ("docx", "pdf", "pptx", "txt", "md"):
        if any(w in n for w in ("previous", "prior", "old", "last", "2023", "2024")) and not any(w in n for w in ("proposed", "new", "future", "2026")):
            return "previous_strategy"
        if any(w in n for w in ("proposed", "new", "future", "draft", "2026", "2027")):
            return "proposed_strategy"
        return "current_strategy"
    for key, role in (("competitor", "competitor_data"), ("marketing", "marketing_data"), ("media", "marketing_data"),
                      ("sales", "sales_data"), ("product", "product_data"), ("customer", "customer_data"),
                      ("survey", "survey"), ("report", "report")):
        if key in n:
            return role
    return "dataset" if file_type in ("csv", "xlsx") else "report"


def store_upload(c: Connection, project_id: str, user_id: str, filename: str, data: bytes, role: str = "auto",
                 period_label: str = "") -> dict:
    """Validate and store one uploaded file. Raises ParserError for invalid files."""
    if len(data) == 0:
        raise ParserError("The file is empty.", "It contains 0 bytes.", "Check the file and upload again.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ParserError("The file is too large.", f"The limit is {settings.max_upload_mb} MB per file.",
                          "Split the file or remove unneeded sheets/pages.")
    safe = safe_filename(filename)
    ftype = detect_type(safe, data)
    fid = new_id()
    key = file_key(project_id, fid, safe)
    from ..parsers import SUPPORTED

    get_storage().put(key, data, SUPPORTED.get(ftype, "application/octet-stream"))
    role = role if role in ROLE_CHOICES else "auto"
    if role == "auto":
        role = guess_role(safe, ftype)
    row = c.insert("files", {
        "id": fid, "project_id": project_id, "uploaded_by": user_id, "original_name": filename[:500], "safe_name": safe,
        "file_type": ftype, "mime_type": SUPPORTED.get(ftype, ""), "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "storage_key": key, "source_role": role,
        "period_label": period_label[:100], "status": "uploaded", "created_at": now(), "updated_at": now(),
    })
    return row


def _context_title(table, chunks) -> str:
    """Find a caption / slide title that names a table, for wide tables reshaped into 'Value'."""
    loc = table.locator
    if table.origin == "pptx_chart":
        if loc.get("chart_title"):
            return loc["chart_title"]
        t = next((ch.text for ch in chunks if ch.kind == "slide_title" and ch.locator.get("slide") == loc.get("slide")), "")
        return t
    if table.origin == "pdf_table":
        caps = [ch.text for ch in chunks if ch.kind in ("caption", "heading") and ch.locator.get("page") == loc.get("page")
                and ch.text.lower().startswith(("table", "exhibit"))]
        if caps:
            title = caps[min(int(loc.get("table", 1)) - 1, len(caps) - 1)]
            return title.split(":", 1)[-1].strip() if ":" in title else title
    return ""


def process_file(file_id: str) -> dict:
    """Extract, normalise and index one stored file. Safe to call again (re-process)."""
    db = get_db()
    with db.tx() as c:
        f = c.fetchone("SELECT * FROM files WHERE id = ?", [file_id])
        if not f:
            return {"status": "missing"}
        c.execute("UPDATE files SET status = 'processing', updated_at = ? WHERE id = ?", [now(), file_id])
    project_id = f["project_id"]
    storage = get_storage()
    import time as _time

    t0 = _time.perf_counter()
    log.info("Processing file %s (%s, %.1f MB)", file_id[:8], f["file_type"], (f["size_bytes"] or 0) / 1e6)
    try:
        data = storage.get(f["storage_key"])
        result = parse_file(f["file_type"], data, f["safe_name"], ocr_enabled=settings.ocr_enabled)
    except ParserError as exc:
        log.info("File %s could not be parsed: %s", file_id[:8], exc.what)
        err = [{"what": exc.what, "why": exc.why, "next": exc.next_step,
                "can_still": "Other files in the project can still be analysed."}]
        with db.tx() as c:
            c.update("files", file_id, {"status": "failed", "error_json": err, "extraction_json": {"tables": 0, "chunks": 0}, "updated_at": now()})
        return {"status": "failed", "errors": err}
    except MemoryError:
        log.error("File %s: out of memory while processing", file_id[:8])
        err = [{"what": "The file is too large to process on this server.", "why": "The server ran out of memory (the free plan has 512 MB).",
                "next": "Split the file into smaller parts (e.g. fewer sheets or pages) and upload them separately.",
                "can_still": "Other files in the project can still be analysed."}]
        with db.tx() as c:
            c.update("files", file_id, {"status": "failed", "error_json": err, "updated_at": now()})
        return {"status": "failed", "errors": err}
    except Exception as exc:  # unexpected parser crash: record it, never crash the app
        from ..storage import StorageError

        if isinstance(exc, StorageError):
            log.error("File %s: storage unavailable while processing (%s)", file_id[:8], exc)
            err = [{"what": "The file could not be read from storage.", "why": "The file storage service was unavailable.",
                    "next": "Click Re-process in a minute.", "can_still": "Other files in the project can still be analysed."}]
            with db.tx() as c:
                c.update("files", file_id, {"status": "failed", "error_json": err, "updated_at": now()})
            return {"status": "failed", "errors": err}
        log.error("Unexpected error processing %s: %s", file_id[:8], traceback.format_exc())
        err = [{"what": "The file could not be processed.", "why": f"Unexpected error ({exc.__class__.__name__}).",
                "next": "Try re-processing. If it keeps failing, re-save the file in its original app and upload it again.",
                "can_still": "Other files in the project can still be analysed."}]
        with db.tx() as c:
            c.update("files", file_id, {"status": "failed", "error_json": err, "updated_at": now()})
        return {"status": "failed", "errors": err}

    datasets_summary, issue_count = [], 0
    with db.tx() as c:
        # remove previous extraction (re-process)
        old = c.fetchall("SELECT normalized_storage_key FROM datasets WHERE file_id = ?", [file_id])
        c.execute("DELETE FROM data_quality_issues WHERE file_id = ?", [file_id])
        c.execute("DELETE FROM datasets WHERE file_id = ?", [file_id])
        c.execute("DELETE FROM document_chunks WHERE file_id = ?", [file_id])
        for o in old:
            if o["normalized_storage_key"]:
                storage.delete(o["normalized_storage_key"])
        for seq, ch in enumerate(result.chunks):
            c.insert("document_chunks", {
                "id": new_id(), "project_id": project_id, "file_id": file_id, "seq": seq, "kind": ch.kind,
                "heading": ch.heading[:1000], "text": ch.text[:20000], "locator_json": ch.locator,
                "is_approximate": ch.approximate, "created_at": now()})
        for ti, table in enumerate(result.tables):
            df, rows, wide_note = reshape_wide(table.df, table.row_numbers)
            if wide_note and "Value" in df.columns:
                title = _context_title(table, result.chunks)
                if title:
                    df = df.rename(columns={"Value": title[:120]})
            norm = normalize_table(df, rows)
            if wide_note:
                norm.transformations.insert(0, wide_note)
            ds_id = new_id()
            key = f"projects/{project_id}/files/{file_id}/derived/{ds_id}.csv.gz"
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(norm.df.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8"))
            storage.put(key, buf.getvalue(), "application/gzip")
            profile = profile_summary(norm)
            profile["period_groups"] = norm.period_groups
            if table.note:
                profile["note"] = table.note
            c.insert("datasets", {
                "id": ds_id, "project_id": project_id, "file_id": file_id, "name": table.name[:500], "origin": table.origin,
                "locator_json": table.locator, "row_count": int(len(norm.df)), "column_count": len(norm.columns),
                "is_approximate": table.approximate, "normalized_storage_key": key, "profile_json": profile,
                "transformations_json": norm.transformations, "created_at": now()})
            for col in norm.columns:
                c.insert("dataset_columns", {
                    "id": new_id(), "dataset_id": ds_id, "position": col.position, "original_name": col.original_name[:500],
                    "normalized_name": col.name[:500], "data_type": col.dtype, "semantic_role": col.role, "unit": col.unit,
                    "missing_count": col.missing, "distinct_count": col.distinct,
                    "stats_json": {**col.stats, "granularity": col.period_granularity, "additive": col.additive}})
            for issue in norm.issues:
                issue_count += 1
                c.insert("data_quality_issues", {
                    "id": new_id(), "project_id": project_id, "file_id": file_id, "dataset_id": ds_id,
                    "issue_type": issue["issue_type"], "field": issue["field"][:500], "severity": issue["severity"],
                    "description": issue["description"], "suggested_resolution": issue["suggested_resolution"],
                    "locator_json": {**table.locator, **issue.get("locator", {})}, "created_at": now()})
            if table.approximate:
                issue_count += 1
                c.insert("data_quality_issues", {
                    "id": new_id(), "project_id": project_id, "file_id": file_id, "dataset_id": ds_id,
                    "issue_type": "approximate_values", "field": "(all)", "severity": "medium",
                    "description": f"Values in '{table.name}' were visually interpreted from an image and are approximate.",
                    "suggested_resolution": "Obtain the underlying data table if exact values matter.",
                    "locator_json": table.locator, "created_at": now()})
            datasets_summary.append({"id": ds_id, "name": table.name, "rows": int(len(norm.df)), "columns": len(norm.columns),
                                     "approximate": table.approximate})
        kinds: dict[str, int] = {}
        for ch in result.chunks:
            kinds[ch.kind] = kinds.get(ch.kind, 0) + 1
        extraction = {"tables": len(result.tables), "chunks": len(result.chunks), "chunk_kinds": kinds,
                      "datasets": datasets_summary, "warnings": result.warnings, "metadata": result.metadata,
                      "quality_issues": issue_count}
        c.update("files", file_id, {"status": result.status, "extraction_json": extraction,
                                    "error_json": result.errors or None, "updated_at": now()})
    log.info("File %s processed: status=%s tables=%s text_blocks=%s issues=%s (%.1fs)", file_id[:8], result.status,
             len(result.tables), len(result.chunks), issue_count, _time.perf_counter() - t0)
    return {"status": result.status, "extraction": extraction, "errors": result.errors}


# ----------------------------------------------------------------- loading for analysis
def load_dataset_frame(ds: dict, columns: list[dict]) -> pd.DataFrame:
    raw = get_storage().get(ds["normalized_storage_key"])
    text_cols = {c["normalized_name"]: str for c in columns if c["data_type"] not in ("datetime", "period", "numeric")}
    df = pd.read_csv(io.BytesIO(gzip.decompress(raw)), low_memory=False, keep_default_na=True, dtype=text_cols)
    for col in columns:
        name = col["normalized_name"]
        if name not in df.columns:
            continue
        if col["data_type"] in ("datetime", "period"):
            df[name] = pd.to_datetime(df[name], errors="coerce")
        elif col["data_type"] == "numeric":
            df[name] = pd.to_numeric(df[name], errors="coerce")
        else:
            df[name] = df[name].astype("object").where(df[name].notna(), None)
            df[name] = df[name].map(lambda v: v if v is None else str(v))
    if "__duplicate__" in df.columns:
        df = df[~df["__duplicate__"].astype(bool)].drop(columns="__duplicate__")
    return df
