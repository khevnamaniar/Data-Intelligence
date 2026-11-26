"""Every awkward spreadsheet in tests/messy_sheets.py must be processed quickly, never get stuck, and be read
correctly (numbers summed, labels kept as labels, dates found, totals rows not double-counted)."""
from __future__ import annotations

import time

import pytest

from tests.messy_sheets import corpus
from tests.test_production import register

CASES = corpus()


def _frames(fid):
    from app.db import get_db
    from app.services.ingestion import load_dataset_frame

    out = []
    with get_db().tx() as c:
        for ds in c.fetchall("SELECT * FROM datasets WHERE file_id = ? ORDER BY name", [fid]):
            cols = c.fetchall("SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY position", [ds["id"]])
            out.append((ds, cols, load_dataset_frame(ds, cols)))
    return out


def check_case(client, name, data, exp):
    register(client)
    pid = client.post("/api/projects", json={"name": name[:60]}).json()["project"]["id"]
    t0 = time.time()
    r = client.post(f"/api/projects/{pid}/files", files=[("files", (name, data, "application/octet-stream"))])
    files = client.get(f"/api/projects/{pid}/files").json()["files"]
    elapsed = time.time() - t0
    problems = []
    if r.status_code not in (201, 400, 415, 422):
        problems.append(f"upload HTTP {r.status_code}: {r.text[:200]}")
    if not files:  # refused at upload
        if exp.get("status") != "failed":
            problems.append(f"refused at upload: {r.text[:300]}")
        return problems, elapsed, None
    f = files[0]
    if f["status"] in ("processing", "uploaded"):
        problems.append("left in processing")
    if "status" in exp and f["status"] != exp["status"]:
        problems.append(f"status {f['status']} != {exp['status']} ({f['error_json']})")
    if "status_in" in exp and f["status"] not in exp["status_in"]:
        problems.append(f"status {f['status']}")
    if exp.get("status") != "failed" and "status_in" not in exp and f["status"] == "failed":
        problems.append(f"failed: {f['error_json']}")
    errs = " ".join(str(e) for e in (f["error_json"] or []))
    if "error_contains" in exp and exp["error_contains"].lower() not in (errs + r.text).lower():
        problems.append(f"error text lacks '{exp['error_contains']}': {errs[:200]} {r.text[:200]}")
    frames = _frames(f["id"]) if f["status"] != "failed" else []
    ex = f.get("extraction_json") or {}
    if "warn_contains" in exp and exp["warn_contains"].lower() not in (str(ex.get("warnings")) + errs + str(ex)).lower():
        problems.append(f"no note about '{exp['warn_contains']}'")
    if "tables_min" in exp and len(frames) < exp["tables_min"]:
        problems.append(f"{len(frames)} tables < {exp['tables_min']}")
    if "tables_max" in exp and len(frames) > exp["tables_max"]:
        problems.append(f"{len(frames)} tables > {exp['tables_max']}: {[d['name'] for d, _, _ in frames]}")
    if frames and any(k in exp for k in ("num", "rows", "cat", "time_col", "filled", "cols_contain", "num_any", "time_any", "rows_min")):
        # the main table = the one holding the expected numeric columns, else the largest
        def score(fr):
            names = {c["normalized_name"] for c in fr[1]}
            return (sum(1 for k in exp.get("num", {}) if k in names), len(fr[2]))
        ds, cols, df = max(frames, key=score)
        types = {c["normalized_name"]: c for c in cols}
        for col, total in exp.get("num", {}).items():
            if col not in types:
                problems.append(f"missing column '{col}' (have {list(types)[:12]})")
            elif types[col]["data_type"] != "numeric":
                problems.append(f"'{col}' is {types[col]['data_type']}, not numeric")
            elif abs(float(df[col].sum()) - total) > max(0.01, abs(total) * 1e-6):
                problems.append(f"'{col}' sums to {float(df[col].sum())} not {total}")
        if "rows" in exp and len(df) != exp["rows"]:
            problems.append(f"{len(df)} rows != {exp['rows']}")
        if "rows_min" in exp and len(df) < exp["rows_min"]:
            problems.append(f"{len(df)} rows < {exp['rows_min']}")
        for col in exp.get("cat", []):
            if col in types and types[col]["data_type"] not in ("categorical", "text", "identifier"):
                problems.append(f"'{col}' is {types[col]['data_type']}")
        if "time_col" in exp and types.get(exp["time_col"], {}).get("semantic_role") != "time":
            problems.append(f"'{exp['time_col']}' not recognised as time ({types.get(exp['time_col'], {}).get('data_type')})")
        if exp.get("time_any") and not any(c["semantic_role"] == "time" or "period_group" in str(c.get("stats_json") or "") for c in cols):
            problems.append("no time dimension found")
        for col, n in exp.get("filled", {}).items():
            if col in df and int(df[col].notna().sum()) != n:
                problems.append(f"'{col}' has {int(df[col].notna().sum())} filled, expected {n}")
        for frag in exp.get("cols_contain", []):
            if not any(frag in c for c in types):
                problems.append(f"no column containing '{frag}': {list(types)}")
        if "num_any" in exp:
            sums = [float(df[c["normalized_name"]].sum()) for c in cols if c["data_type"] == "numeric"]
            if not any(abs(s - exp["num_any"]) < 0.01 for s in sums) and abs(sum(sums) - exp["num_any"]) > 0.01:
                problems.append(f"no numeric total equals {exp['num_any']} (sums {sums[:8]})")
    if f["status"] != "failed":
        rid = client.post(f"/api/projects/{pid}/analyses", json={"mode": "deep", "questions": ["What are the main insights?"]}).json()["run"]["id"]
        run = client.get(f"/api/projects/{pid}/analyses/{rid}").json()["run"]
        if run["status"] not in ("completed", "partial"):
            problems.append(f"analysis {run['status']}: {run.get('errors')}")
        elif run.get("errors"):
            problems.append(f"analysis errors: {run['errors']}")
    if elapsed > 20:
        problems.append(f"slow: {elapsed:.1f}s")
    return problems, elapsed, f


@pytest.mark.parametrize("name,data,exp", CASES, ids=[c[0] for c in CASES])
def test_messy_sheet(client, name, data, exp):
    problems, elapsed, _ = check_case(client, name, data, exp)
    assert not problems, f"{name}: " + " | ".join(problems)


def test_files_over_the_memory_limit_are_read_in_part(client, monkeypatch):
    from app.parsers import tabular
    from tests.messy_sheets import large_csv

    monkeypatch.setattr(tabular, "MAX_CELLS", 40_000)  # ~5,000 rows of 8 columns
    problems, _, f = check_case(client, "big.csv", large_csv(20_000), {"status": "partial"})
    assert not problems, problems
    assert "first 5,000 of about 20,000 rows" in f["error_json"][0]["what"] and "split the file" in f["error_json"][0]["next"].lower()
    assert f["extraction_json"]["datasets"][0]["rows"] == 5000


def test_large_file_is_processed_quickly(client):
    from tests.messy_sheets import large_csv

    problems, elapsed, f = check_case(client, "large.csv", large_csv(60_000), {"rows": 60_000})
    assert not problems, problems
    assert elapsed < 15, f"took {elapsed:.1f}s"
