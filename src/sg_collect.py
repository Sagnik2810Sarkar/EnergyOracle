#!/usr/bin/env python3
"""Collection script for Singapore's half-hourly system demand from www.ema.gov.sg

Commands
    python src/sg_collect.py download [--start YYYY-MM-DD] [--end YYYY-MM-DD]
    python src/sg_collect.py parse


"""
import argparse
import csv
import datetime as dt
import hashlib
import math
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

UA = "seasia-energy-collector/0.1 (contact: michstone994@gmail.com)"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"   # .xls
ZIP_MAGIC = b"PK\x03\x04"         # .xlsx
SERIES = ("system_demand_mw", "nem_demand_mw", "nem_forecast_mw")


# helpers
def load_cfg(root: Path):
    with open(root / "config" / "singapore.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def as_date(v):
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v 
    else:
        return v.date() if isinstance(v, dt.datetime) else dt.date.fromisoformat(str(v))


def mondays(start: dt.date, end: dt.date):
    d = start + dt.timedelta(days=(7 - start.weekday()) % 7)
    while d <= end:
        yield d
        d += dt.timedelta(weeks=1)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def magic_ok(ext: str, body: bytes) -> bool:
    return body[:4] == (OLE_MAGIC if ext == "xls" else ZIP_MAGIC)


def fetch(session, url, retries, timeout):
    for i in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 404:
                return 404, b""
            r.raise_for_status()
            return r.status_code, r.content
        except requests.RequestException as e:
            if i == retries - 1:
                return -1, str(e).encode()
            time.sleep(2 ** i)


def append_csv(path: Path, fields, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


# download

def cmd_download(a, cfg, root):
    raw = root / cfg["raw_dir"]
    today = dt.date.today()
    last_monday = today - dt.timedelta(days=today.weekday())
    start = as_date(a.start or cfg["start"])
    end = as_date(a.end) if a.end else last_monday
    refresh_from = last_monday - dt.timedelta(weeks=cfg["refresh_weeks"])
    s = requests.Session()
    s.headers["User-Agent"] = UA
    counts = {"new": 0, "unchanged": 0, "revised": 0, "missing": 0, "error": 0, "skipped": 0}

    for m in mondays(start, end):
        folder = raw / str(m.year)
        existing = sorted(folder.glob(f"{m:%Y%m%d}.xls*"))
        
        if existing and m < refresh_from:
            counts["skipped"] += 1
            continue
        found, bad = False, False
        
        for ext in cfg["extensions"]:
            url = cfg["url_template"].format(year=m.year, ymd=f"{m:%Y%m%d}", ext=ext)
            status, body = fetch(s, url, cfg["retries"], cfg["timeout_seconds"])
            time.sleep(cfg["request_delay_seconds"])
            
            if status == 404:
                continue
            if status != 200 or not magic_ok(ext, body):
                bad = True          
                continue
            
            target = folder / f"{m:%Y%m%d}.{ext}"
            folder.mkdir(parents=True, exist_ok=True)
            
            target.write_bytes(body)
            counts["new"] += 1

            found = True
            break
        if not found:
            counts["error" if bad else "missing"] += 1
    
    print("download summary:", counts)


# parsing
def to_minutes(v):
    
    if isinstance(v, (dt.datetime, pd.Timestamp, dt.time)):
        return (v.hour * 60 + v.minute) % 1440

    if isinstance(v, str):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", v)
        if m:
            return (int(m[1]) * 60 + int(m[2])) % 1440
        
    return None


def to_date(v):
    
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, dt.date):
        return v
    
    if isinstance(v, str):
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)       # d/m/YYYY
        if m:
            try:
                return dt.date(int(m[3]), int(m[2]), int(m[1]))
            except ValueError:
                return None
    
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 36000 < v < 60000:   # Excel serial
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(v))).date()
    return None


def series_from_text(text: str):
    
    t = text.lower()
    if "forecast" in t:
        return "nem_forecast_mw"
    if "nem" in t:
        return "nem_demand_mw"
    if "system" in t:
        return "system_demand_mw"
    
    return None


def parse_grid(df: pd.DataFrame, monday: dt.date, cfg):
    """Parse one wide sheet. Returns (wide_df, warnings). Raises ValueError if layout is unrecognised."""
    n_per = cfg["periods_per_day"]
    warns = []

    def is_value(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and not math.isnan(x) and x > 100

    # 1. find the time-label column and its data rows
    best = None
    for c in range(min(3, df.shape[1])):
        rows = [i for i in range(len(df))
                if to_minutes(df.iat[i, c]) is not None
                and sum(is_value(x) for x in df.iloc[i, c + 1:]) >= 3]
        if best is None or len(rows) > len(best[1]):
            best = (c, rows)
    tcol, rows = best
    if len(rows) != n_per:
        raise ValueError(f"expected {n_per} period rows, found {len(rows)}")
    first = rows[0]

    # 2. check the timestamp convention (period-ending: 00:30 ... 00:00)
    labels = [to_minutes(df.iat[i, tcol]) for i in rows]
    expected = [(30 * (p + 1)) % 1440 for p in range(n_per)]
    if labels != expected:
        raise ValueError("time labels are not period-ending 00:30..00:00 "
                         f"(first labels: {labels[:3]}); check convention with `inspect`")

    # 3. data columns = numeric in at least half of the period rows
    dcols = [c for c in range(df.shape[1]) if c != tcol and
             sum(is_value(df.iat[i, c]) for i in rows) >= n_per // 2]
    if not dcols:
        raise ValueError("no numeric data columns")

    # 4. header info above the first period row
    head = df.iloc[:first]
    col_series, col_date = {}, {}
    for c in dcols:
        txt = " ".join(str(x) for x in head.iloc[:, c] if isinstance(x, str))
        col_series[c] = series_from_text(txt)
        ds = [to_date(x) for x in head.iloc[:, c] if to_date(x)]
        col_date[c] = ds[-1] if ds else None
    if any(v is None for v in col_series.values()):
        if len(dcols) % 3 == 0:
            warns.append("series names not found in header; assumed repeating system/NEM/forecast order")
            for k, c in enumerate(dcols):
                col_series[c] = SERIES[k % 3]
        else:
            raise ValueError(f"cannot identify series for {len(dcols)} data columns")
    prev = None                      # merged date cells: carry the date rightwards
    for c in dcols:
        if col_date[c] is None:
            col_date[c] = prev
        prev = col_date[c]

    # 5. day index by repeated-series rule, used as fallback and cross-check
    seen, day_idx, idx = set(), 0, {}
    for c in dcols:
        if col_series[c] in seen:
            seen, day_idx = set(), day_idx + 1
        seen.add(col_series[c])
        idx[c] = day_idx
    for c in dcols:
        derived = monday + dt.timedelta(days=idx[c])
        if col_date[c] is None:
            col_date[c] = derived
        elif col_date[c] != derived:
            warns.append(f"header date {col_date[c]} != filename-derived {derived} (header used)")
            break

    # 6. build long -> wide
    recs = []
    for c in dcols:
        for p, i in enumerate(rows, start=1):
            v = df.iat[i, c]
            recs.append((col_date[c], p, col_series[c], float(v) if is_value(v) else float("nan")))
    long = pd.DataFrame(recs, columns=["date", "period", "series", "mw"])
    wide = long.pivot_table(index=["date", "period"], columns="series", values="mw", aggfunc="first", dropna=False)
    wide = wide.reset_index()
    wide.columns.name = None
    for s in SERIES:
        if s not in wide:
            wide[s] = float("nan")
    # drop days that are entirely empty (partial current week)
    day_has = wide.groupby("date")[list(SERIES)].apply(lambda g: g.notna().any().any())
    empty_days = [d for d, ok in day_has.items() if not ok]
    if empty_days:
        warns.append(f"{len(empty_days)} empty day(s) dropped (partial week?)")
        wide = wide[~wide["date"].isin(empty_days)]
    n_days = wide["date"].nunique()
    if n_days != cfg["days_per_file"] and not empty_days:
        warns.append(f"{n_days} days found, expected {cfg['days_per_file']}")
    return wide, warns


def parse_file(path: Path, cfg):
    
    monday = dt.datetime.strptime(path.stem, "%Y%m%d").date()
    sheets = pd.read_excel(path, sheet_name=None, header=None)
    errs = []
    
    for name, df in sheets.items():
        try:
            wide, warns = parse_grid(df, monday, cfg)
            return wide, warns
        except ValueError as e:
            errs.append(f"sheet {name!r}: {e}")
    
    raise ValueError("; ".join(errs))


def cmd_parse(a, cfg, root):
    raw = root / cfg["raw_dir"]
    issues_path = root / cfg["issues"]
    if issues_path.exists():
        issues_path.unlink()
    lo, hi = cfg["plausible_mw"]
    frames, bad = [], 0
    files = sorted(p for p in raw.glob("*/*") if re.fullmatch(r"\d{8}\.xlsx?", p.name))
    
    for p in files:
        try:
            wide, warns = parse_file(p, cfg)
        except Exception as e:                 # report and continue
            bad += 1
            append_csv(issues_path, ["file", "level", "message"], dict(file=p.name, level="ERROR", message=str(e)))
            continue
        for w in warns:
            append_csv(issues_path, ["file", "level", "message"], dict(file=p.name, level="WARN", message=w))
        
        vals = wide[list(SERIES)].stack()
        n_out = int(((vals < lo) | (vals > hi)).sum())
        if n_out:
            append_csv(issues_path, ["file", "level", "message"],
                       dict(file=p.name, level="WARN", message=f"{n_out} values outside {lo}-{hi} MW"))
        wide["source_file"] = p.name
        wide["file_hash"] = sha256(p.read_bytes())
        frames.append(wide)
    
    if not frames:
        print("nothing parsed; check issues file: ", issues_path)
        sys.exit(1) 
    
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out["ts_end_singaporetime"] = (out["date"] + pd.to_timedelta(30 * out["period"], unit="m")).dt.tz_localize(cfg["timezone"])
    out = (out.sort_values(["ts_end_singaporetime", "source_file"]).drop_duplicates("ts_end_singaporetime", keep="last"))      # later file wins on overlap
    cols = ["ts_end_singaporetime", "date", "period", *SERIES, "source_file", "file_hash"]
    out = out[cols]
    dest = root / cfg["processed"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False, compression="gzip")
    
    print(f"parsed {len(files) - bad}/{len(files)} files -> {len(out):,} rows  ({dest})")
    print(f"range: {out['ts_end_singaporetime'].min()}  ->  {out['ts_end_singaporetime'].max()}")
    if bad or issues_path.exists():
        print(f"see issues: {issues_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="project root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--start")
    d.add_argument("--end")
    i = sub.add_parser("inspect")
    i.add_argument("file")
    sub.add_parser("parse")
    a = ap.parse_args()
    root = Path(a.root)
    cfg = load_cfg(root)
    {"download": cmd_download, "parse": cmd_parse}[a.cmd](a, cfg, root)


if __name__ == "__main__":
    main()
