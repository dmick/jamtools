"""
Shared Google Sheets helpers for jam_solver.py and jam_merge.py.

  * authentication (service-account key; read-only unless you ask to write)
  * finding the live setlist sheet, from a spreadsheet id/URL or from a date
  * fetching the live setlist as a header + rows, remembering each row's
    sheet row number so cells can be written back precisely
  * exporting that to the same CSV layout jam_solver.py reads (songs.csv)

Credentials: a service-account key file, looked up in this order:
  --creds FILE, $JAM_GOOGLE_CREDS, ~/.config/googleserviceaccount.key
The spreadsheet must be shared with the service account's client_email
(Viewer is enough to read; jam_merge.py --apply needs Editor, and is the only thing that writes).
"""
import csv
import datetime
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_CREDS = "~/.config/googleserviceaccount.key"
# Sheet listing "date, spreadsheet id" pairs (same one the lyrics server uses)
DEFAULT_INDEX_SHEET = "1hxuvHuYAYcxQlOE4KCaeoiSrgZC95OOk3B2Ciu6LAiM"

SCOPE_READ  = "https://www.googleapis.com/auth/spreadsheets.readonly"
SCOPE_WRITE = "https://www.googleapis.com/auth/spreadsheets"

# Same meaning as in jam_solver.py: columns that are not player slots.
SKIP_COLUMNS = {"index", "starttime", "time", "song", "artist", "notes"}


def norm_col(c):
    """'GUITAR 2' / 'Guitar2' / ' guitar 2 ' -> 'guitar2'"""
    return re.sub(r"\s+", "", (c or "").strip().lower())


# ---------------------------------------------------------------------------
# Auth / service
# ---------------------------------------------------------------------------

def _creds_path(creds=None):
    return Path(creds or os.environ.get("JAM_GOOGLE_CREDS") or DEFAULT_CREDS).expanduser()


def get_service(creds=None, write=False):
    """Return a Sheets `spreadsheets()` resource (read-only unless write=True). Imports Google libs lazily."""
    path = _creds_path(creds)
    if not path.is_file():
        sys.exit(f"Error: Google credentials file not found: {path}\n"
                 f"Use --creds FILE or set JAM_GOOGLE_CREDS (service-account key JSON).")
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("Error: google-api-python-client / google-auth are not installed "
                 "(run via `uv run`, which installs them from the script header).")
    cred = service_account.Credentials.from_service_account_file(
        str(path), scopes=[SCOPE_WRITE if write else SCOPE_READ])
    return build("sheets", "v4", credentials=cred, cache_discovery=False).spreadsheets()


def service_account_email(creds=None):
    try:
        return json.loads(_creds_path(creds).read_text()).get("client_email", "?")
    except Exception:
        return "?"


def explain_http_error(e, creds=None, writing=False):
    """Turn a googleapiclient HttpError into a short actionable message."""
    status = getattr(getattr(e, "resp", None), "status", None)
    who = service_account_email(creds)
    if status in (403, 404):
        return (f"Google says {status}: the spreadsheet wasn't found or isn't shared with "
                f"{who}. Share it with that address as {'Editor' if writing else 'Viewer (or Editor)'}.")
    return f"Google Sheets API error: {e}"


# ---------------------------------------------------------------------------
# Locating the sheet
# ---------------------------------------------------------------------------

def sheet_id_from(arg):
    """Accept a bare spreadsheet id or a docs.google.com URL."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", arg)
    return m.group(1) if m else arg.strip()


def parse_date(d):
    """'2026-11-09' or '11/9/2026' -> (2026, 11, 9)"""
    d = d.strip()
    try:
        if "-" in d:
            a, b, c = map(int, d.split("-"))
        else:
            b, c, a = map(int, d.split("/"))      # M/D/Y
            return (a, b, c)
        if a <= 12:                                # M-D-Y typed with dashes
            a, b, c = c, a, b
        return (a, b, c)
    except ValueError:
        raise ValueError(f"unrecognised date: {d!r} (use YYYY-MM-DD or MM/DD/YYYY)")


def sheet_id_for_date(svc, date, index_sheet=DEFAULT_INDEX_SHEET):
    want = parse_date(date)
    rows = svc.values().get(spreadsheetId=index_sheet, range="A:B").execute().get("values", [])
    for r in rows:
        if len(r) >= 2:
            try:
                if parse_date(r[0]) == want:
                    return r[1].strip()
            except ValueError:
                continue
    sys.exit(f"Error: no spreadsheet listed for {date} in the index sheet.")


def sidecar_path(songs_path):
    """songs.csv -> songs.sheet.json (remembers which sheet it came from)."""
    return Path(songs_path).with_suffix(".sheet.json")


def read_sidecar(songs_path):
    try:
        return json.loads(sidecar_path(songs_path).read_text())
    except (OSError, ValueError):
        return None


def write_sidecar(songs_path, sheet_id, tab, date=None):
    sidecar_path(songs_path).write_text(json.dumps({
        "sheet_id": sheet_id, "tab": tab, "date": date,
        "fetched": datetime.datetime.now().isoformat(timespec="seconds"),
    }, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Fetching the live setlist
# ---------------------------------------------------------------------------

def col_letter(i):
    """0 -> A, 25 -> Z, 26 -> AA"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def quote_tab(tab):
    return "'" + tab.replace("'", "''") + "'"


def first_tab_title(svc, sheet_id):
    meta = svc.get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    return meta["sheets"][0]["properties"]["title"]


class LiveSheet:
    """
    header:     raw header cells, trimmed after the last non-empty one
                (column A, usually unlabeled, is called 'index')
    header_row: 1-based sheet row number of the header
    rows:       [{"row": 1-based sheet row, "cells": [str, ...] padded to len(header)}]
    cols:       normalised header name -> 0-based column index (first one wins)
    """
    def __init__(self, sheet_id, tab, header, header_row, rows, raw=None):
        self.sheet_id, self.tab, self.raw = sheet_id, tab, raw or []
        self.header, self.header_row, self.rows = header, header_row, rows
        self.cols = {}
        for i, h in enumerate(header):
            n = norm_col(h)
            if n and n not in self.cols:
                self.cols[n] = i

    def slot_cols(self):
        return [n for n in self.cols if n not in SKIP_COLUMNS]

    def by_song(self):
        """normalised song title -> [row, ...]  (more than one = duplicate title)"""
        out = {}
        si = self.cols["song"]
        for r in self.rows:
            out.setdefault(r["cells"][si].strip(), []).append(r)
        return out

    def cell(self, row, col):
        return row["cells"][self.cols[col]].strip()

    def a1(self, row, col):
        return f"{quote_tab(self.tab)}!{col_letter(self.cols[col])}{row['row']}"

    def raw_cell(self, rownum, colidx):
        """Cell as currently in the sheet (1-based row, 0-based col); '' if beyond the data."""
        r = self.raw[rownum - 1] if rownum - 1 < len(self.raw) else []
        return r[colidx].strip() if colidx < len(r) else ""

    def write_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index" if i == 0 and not h.strip() else h
                        for i, h in enumerate(self.header)])
            for r in self.rows:
                w.writerow(r["cells"])


def fetch_live(svc, sheet_id, tab=None):
    tab = tab or first_tab_title(svc, sheet_id)
    values = svc.values().get(spreadsheetId=sheet_id, range=f"{quote_tab(tab)}!A:Z").execute().get("values", [])

    # header = first of the top rows that has both SONG and VOCAL
    hdr_i = None
    for i, row in enumerate(values[:15]):
        names = {norm_col(c) for c in row}
        if "song" in names and "vocal" in names:
            hdr_i = i
            break
    if hdr_i is None:
        sys.exit(f"Error: couldn't find a header row with SONG and VOCAL in the top rows of tab {tab!r}.")

    header = [c.strip() for c in values[hdr_i]]
    while header and not header[-1]:
        header.pop()
    width = len(header)
    si = next(i for i, h in enumerate(header) if norm_col(h) == "song")
    ai = next((i for i, h in enumerate(header) if norm_col(h) == "artist"), None)

    def cell(row, i):
        return row[i].strip() if i is not None and i < len(row) else ""

    body = values[hdr_i + 1:]
    rows, started = [], False
    for k, row in enumerate(body):
        song, artist = cell(row, si), cell(row, ai)
        nxt = body[k + 1] if k + 1 < len(body) else []
        if started:
            if song.startswith("Tune to") or "Original Key" in song:
                break
            if not song and not artist and not cell(nxt, si) and not cell(nxt, ai):
                break                       # two blank rows in a row = end of the set
        if not song:
            if not started:
                continue                    # blank/sub-header rows before the first song
            continue                        # lone blank row inside the set
        started = True
        cells = [(row[i].strip() if i < len(row) else "") for i in range(width)]
        rows.append({"row": hdr_i + 1 + 1 + k, "cells": cells})
    return LiveSheet(sheet_id, tab, header, hdr_i + 1, rows, raw=values)
