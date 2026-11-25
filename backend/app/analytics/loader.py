"""Load everything a project contains into a ProjectData object for analysis."""
from __future__ import annotations

import logging

import pandas as pd

from ..db import Connection
from ..services.ingestion import load_dataset_frame
from .model import Chunk, Column, DatasetView, DocumentView, ProjectData

log = logging.getLogger("dip.analytics")
RESIDUAL_LABELS = {"(other — not listed in the report)", "(not listed)"}
STRATEGY_ORDER = {"previous_strategy": 0, "current_strategy": 1, "plan": 1, "proposed_strategy": 2}


def load_project_data(c: Connection, project_id: str, file_ids: list[str] | None = None) -> ProjectData:
    project = c.fetchone("SELECT * FROM projects WHERE id = ?", [project_id])
    files = c.fetchall("SELECT * FROM files WHERE project_id = ? AND status IN ('processed', 'partial') ORDER BY created_at", [project_id])
    if file_ids:
        wanted = set(file_ids)
        files = [f for f in files if f["id"] in wanted]
    fmap = {f["id"]: f for f in files}
    datasets: list[DatasetView] = []
    for ds in c.fetchall("SELECT * FROM datasets WHERE project_id = ? ORDER BY created_at", [project_id]):
        f = fmap.get(ds["file_id"])
        if not f or not ds["normalized_storage_key"]:
            continue
        cols = c.fetchall("SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY position", [ds["id"]])
        try:
            df = load_dataset_frame(ds, cols)
        except Exception as exc:  # storage object missing etc. – skip but keep going
            log.warning("Could not load dataset %s: %s", ds["id"], exc)
            continue
        for col in cols:
            name = col["normalized_name"]
            if col["data_type"] in ("categorical", "text") and name in df.columns:
                # rows added to complete a "top N" listing count in totals but are not a segment of their own
                df[name] = df[name].map(lambda v: None if isinstance(v, str) and v.strip().lower() in RESIDUAL_LABELS else v)
        columns = {}
        for col in cols:
            st = col.get("stats_json") or {}
            columns[col["normalized_name"]] = Column(
                name=col["normalized_name"], dtype=col["data_type"], role=col["semantic_role"], unit=col["unit"] or "",
                additive=bool(st.get("additive")), distinct=int(col["distinct_count"] or 0),
                granularity=st.get("granularity") or "", period_group=st.get("period_group") or "")
        profile = ds.get("profile_json") or {}
        datasets.append(DatasetView(
            id=ds["id"], name=ds["name"], file_id=f["id"], file_name=f["original_name"], file_role=f["source_role"],
            period_label=f.get("period_label") or "", origin=ds["origin"], locator=ds.get("locator_json") or {},
            approximate=bool(ds["is_approximate"]), df=df, columns=columns, period_groups=profile.get("period_groups") or {}))
    documents: list[DocumentView] = []
    for i, f in enumerate(files):
        chunks = c.fetchall("SELECT * FROM document_chunks WHERE file_id = ? ORDER BY seq", [f["id"]])
        if not chunks:
            continue
        documents.append(DocumentView(
            file_id=f["id"], name=f["original_name"], role=f["source_role"], period_label=f.get("period_label") or "",
            file_type=f["file_type"], order=i,
            chunks=[Chunk(id=ch["id"], file_id=f["id"], kind=ch["kind"], heading=ch["heading"] or "", text=ch["text"],
                          locator=ch.get("locator_json") or {}, approximate=bool(ch["is_approximate"])) for ch in chunks]))
    issues = c.fetchall("SELECT * FROM data_quality_issues WHERE project_id = ?", [project_id])
    if file_ids:
        issues = [i for i in issues if i["file_id"] in fmap]
    datasets = combine_periodic_datasets(datasets)
    return ProjectData(project=project, datasets=datasets, documents=documents, files=files, quality_issues=issues)


def _signature(ds: DatasetView) -> tuple:
    title = " ".join(str(ds.locator.get("title") or "").lower().split())
    return (ds.origin, title, tuple(sorted((c.name, c.dtype) for c in ds.columns.values())))


def combine_periodic_datasets(datasets: list[DatasetView]) -> list[DatasetView]:
    """Stack the same table from several period reports (e.g. monthly Google Ads PDFs) into one time series.

    Only tables with identical columns, the same title and a time column are combined, and only when their
    periods do not overlap (so a file uploaded twice is never double-counted)."""
    groups: dict[tuple, list[DatasetView]] = {}
    for ds in datasets:
        if ds.time_col:
            groups.setdefault(_signature(ds), []).append(ds)
    replace: dict[str, DatasetView | None] = {}
    for grp in groups.values():
        if len(grp) < 2 or len({d.file_id for d in grp}) < len(grp):
            continue
        spans = []
        for d in grp:
            t = d.df[d.time_col].dropna()
            if t.empty:
                break
            spans.append((t.min(), t.max(), d))
        else:
            spans.sort(key=lambda x: x[0])
            if any(b[0] <= a[1] for a, b in zip(spans, spans[1:])):
                log.info("Not combining %s: periods overlap", grp[0].name)
                continue
            ordered = [d for _, _, d in spans]
            first, last = ordered[0], ordered[-1]
            frames = []
            for d in ordered:
                f = d.df.copy()
                f["__source_file__"] = d.file_name
                frames.append(f)
            df = pd.concat(frames, ignore_index=True)
            cols = {}
            for name, col in first.columns.items():
                cols[name] = replace_col(col, distinct=int(df[name].nunique(dropna=True)) if name in df.columns else col.distinct)
            title = first.locator.get("title") or first.name.split(" (")[0]
            p0, p1 = first.locator.get("period") or "", last.locator.get("period") or ""
            span = f"{p0} – {p1}" if p0 and p1 else f"{len(ordered)} periods"
            combined = DatasetView(
                id=first.id, name=f"{title} ({span}, {len(ordered)} reports)", file_id=first.file_id,
                file_name=f"{len(ordered)} reports ({first.file_name} … {last.file_name})", file_role=first.file_role,
                period_label="", origin=first.origin, locator={**first.locator, "combined_from": [d.id for d in ordered],
                                                               "files": [d.file_name for d in ordered]},
                approximate=any(d.approximate for d in ordered), df=df, columns=cols, period_groups=first.period_groups)
            replace[first.id] = combined
            for d in ordered[1:]:
                replace[d.id] = None
            log.info("Combined %d period reports into '%s'", len(ordered), combined.name)
    out = []
    for ds in datasets:
        if ds.id in replace:
            if replace[ds.id] is not None:
                out.append(replace[ds.id])
        else:
            out.append(ds)
    return out


def replace_col(col: Column, **kw) -> Column:
    from dataclasses import replace

    return replace(col, **kw)
