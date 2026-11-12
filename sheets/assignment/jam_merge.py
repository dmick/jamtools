#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "google-api-python-client",
#   "google-auth",
# ]
# ///
"""
Merge jam_solver.py results into the CURRENT live Google Sheet without losing
anyone's edits (a three-way merge, cell by cell), and write the result as a
CSV you can paste back in, or (with --apply) write just the changed cells into
the sheet. Without --apply the sheet is only ever read.

  base      songs.csv        what the sheet looked like when the solver ran
  solver    assignments.csv  what the solver produced
  live      the sheet right now (fetched fresh)

For every slot cell the solver changed (solver != base):
  live == base     -> APPLY    (nobody touched it; the CSV gets the solver's value)
  live == solver   -> ALREADY  (someone entered the same thing; nothing to do)
  anything else    -> CONFLICT (someone changed it since; the CSV keeps the live value)

Every other cell in the CSV is exactly what the live sheet has right now, so
pasting it back changes only the APPLY cells. Compare the CSV with the sheet
(or diff it against a fresh export) to see precisely what a paste will do.

The CSV covers the contiguous columns SONG .. KEYS only (index, time and
notes are not included), from the first song row to the last, with blank
rows inside the set preserved so rows line up. It has NO header row, so every
line is a song row: paste it with the first song's SONG cell selected (the
report says which cell). --header adds a header row on top (for eyeballing
only; don't paste it over the songs).
Rows are matched by song title, so inserting or sorting rows in the live
sheet since the export is safe.

--apply writes ONLY the APPLY cells (so the sheet's edit history shows exactly
those and nothing else), as plain text, one request per cell (--batch sends
them all in one request; Sheets' history then tends to show a bounding block). First it saves the live sheet to
live-backup-<timestamp>.csv, then re-reads the sheet and aborts if anything
it is about to write (or any conflict status) changed in the meantime, then
writes, then reads everything back and verifies. Needs the service account
to be an Editor on the sheet. merged.csv is still written, as a preview.

Usage (from the directory holding songs.csv / assignments.csv):
  jam_merge.py                      # sheet taken from songs.sheet.json (left by jam_solver.py --sheet/--date)
  jam_merge.py --date 2026-11-09    # or name the sheet explicitly (--sheet ID_OR_URL)
Outputs: merged.csv (--output) and merge_report.txt (--report).
"""
import argparse
import csv
import datetime
import sys
import time
from pathlib import Path

import jam_sheets as js


# ---------------------------------------------------------------------------
# Pure merge logic
# ---------------------------------------------------------------------------

def read_csv_rows(path):
    """-> (normalised column names, [ {col: stripped value} ])"""
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        cols = [js.norm_col(h) for h in (rd.fieldnames or [])]
        rows = [{js.norm_col(k): (v or "").strip() for k, v in r.items() if k is not None}
                for r in rd]
    return cols, rows


def plan_merge(base_rows, solver_rows, live, slot_cols):
    """
    Returns (cells, notes, other_edits)
      cells: [{song, col, base, solver, live, status, row}] status in
             APPLY / ALREADY / CONFLICT
      notes: [str]  problems that stopped individual songs/columns from merging
      other_edits: number of slot cells edited live since export that the
             solver didn't touch (a hint that the export is stale)
    """
    base_by = {r["song"]: r for r in base_rows}
    live_by = live.by_song()
    cells, notes = [], []

    for s in solver_rows:
        song = s["song"]
        b = base_by.get(song, {})
        changed = [c for c in slot_cols if s.get(c, "") != b.get(c, "")]
        if not changed:
            continue
        lrows = live_by.get(song, [])
        if len(lrows) == 0:
            notes.append(f"{song}: not in the live sheet (renamed or removed?) - skipped {len(changed)} change(s)")
            continue
        if len(lrows) > 1:
            notes.append(f"{song}: appears {len(lrows)} times in the live sheet, can't tell which to update - skipped")
            continue
        lrow = lrows[0]
        for c in changed:
            if c not in live.cols:
                notes.append(f"{song} / {c}: no such column in the live sheet - skipped")
                continue
            bv, sv, lv = b.get(c, ""), s.get(c, ""), live.cell(lrow, c)
            status = "ALREADY" if lv == sv else ("APPLY" if lv == bv else "CONFLICT")
            cells.append({"song": song, "col": c, "base": bv, "solver": sv,
                          "live": lv, "status": status, "row": lrow})

    touched = {(c["song"], c["col"]) for c in cells}
    other = 0
    for song, b in base_by.items():
        lrows = live_by.get(song, [])
        if len(lrows) != 1:
            continue
        for c in slot_cols:
            if c in live.cols and (song, c) not in touched and live.cell(lrows[0], c) != b.get(c, ""):
                other += 1
    return cells, notes, other


def merged_block(live, cells, with_header=True):
    """
    The live sheet's SONG..KEYS columns, first song row to last, with the
    APPLY cells filled in. Returns (rows, first_sheet_row, first_col_letter, last_col_letter).
    """
    c0 = live.cols["song"]
    c1 = live.cols.get("keys", max(live.cols[c] for c in live.slot_cols()))
    if c1 < c0:
        sys.exit("Error: expected the SONG column to come before KEYS in the live sheet.")
    first, last = live.rows[0]["row"], live.rows[-1]["row"]

    fills = {(c["row"]["row"], live.cols[c["col"]]): c["solver"] for c in cells if c["status"] == "APPLY"}
    out = []
    if with_header:
        out.append(live.header[c0:c1 + 1] + [""] * (c1 + 1 - c0 - len(live.header[c0:c1 + 1])))
    for rn in range(first, last + 1):
        out.append([fills.get((rn, ci), live.raw_cell(rn, ci)) for ci in range(c0, c1 + 1)])
    return out, first, js.col_letter(c0), js.col_letter(c1)


def render_report(cells, notes, other, live, paste=None):
    def show(v):
        return v if v else "(blank)"
    out = []
    n = {k: sum(c["status"] == k for c in cells) for k in ("APPLY", "ALREADY", "CONFLICT")}
    out.append(f"Live sheet: {live.sheet_id}  tab {live.tab!r}  ({len(live.rows)} songs)")
    out.append(f"Solver changes: {len(cells)}   in merged.csv: {n['APPLY']}   already there: {n['ALREADY']}   "
               f"CONFLICTS: {n['CONFLICT']}")
    if other:
        out.append(f"Note: {other} other slot cell(s) were edited live since songs.csv was exported "
                   f"(carried into the CSV as they are now).")
    if n["CONFLICT"]:
        out.append("\nCONFLICTS (live value kept in the CSV, solver's suggestion NOT applied):")
        for c in cells:
            if c["status"] == "CONFLICT":
                out.append(f"  {c['song']} / {c['col']}: solver wants {show(c['solver'])}, "
                           f"was {show(c['base'])} at export, live now has {show(c['live'])}")
    if notes:
        out.append("\nSKIPPED:")
        out += [f"  {x}" for x in notes]
    if n["APPLY"]:
        out.append("\nCHANGES THE CSV MAKES TO THE SHEET:")
        for c in cells:
            if c["status"] == "APPLY":
                out.append(f"  {c['song']} / {c['col']}: {show(c['base'])} -> {show(c['solver'])}")
    if n["ALREADY"]:
        out.append(f"\nALREADY IN SHEET ({n['ALREADY']}):")
        for c in cells:
            if c["status"] == "ALREADY":
                out.append(f"  {c['song']} / {c['col']}: {show(c['solver'])}")
    if paste:
        out.append(paste)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def apply_changes(svc, sheet_id, live, cells, notes, base_rows, solver_rows, slot_cols, args):
    """Write the APPLY cells (only) to the sheet; returns text for the report."""
    todo = [c for c in cells if c["status"] == "APPLY"]
    if not todo:
        return "\n--apply: nothing to write."
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"live-backup-{stamp}.csv"
    live.write_csv(backup)
    print(f"Saved the live sheet to {backup}")

    try:
        # Re-read right before writing; abort if the picture changed under us.
        live2 = js.fetch_live(svc, sheet_id, live.tab)
        cells2, notes2, other2 = plan_merge(base_rows, solver_rows, live2, slot_cols)
        key = lambda cs: sorted((c["song"], c["col"], c["status"], c["live"]) for c in cs)
        if key(cells2) != key(cells):
            print(render_report(cells2, notes2, other2, live2))
            sys.exit("\nAborted: the sheet changed while merging. Nothing was written; review the report above and rerun.")
        todo2 = [c for c in cells2 if c["status"] == "APPLY"]
        data = [{"range": live2.a1(c["row"], c["col"]), "values": [[c["solver"]]]} for c in todo2]
        if args.batch:
            svc.values().batchUpdate(spreadsheetId=sheet_id, body={
                "valueInputOption": "RAW", "data": data}).execute()
        else:
            # One request per cell, so Sheets' version history records individual cell edits
            # instead of one bounding rectangle. Paced and retried because of the API write quota.
            for i, d in enumerate(data, 1):
                for attempt in range(7):
                    try:
                        svc.values().update(spreadsheetId=sheet_id, range=d["range"],
                                            valueInputOption="RAW", body={"values": d["values"]}).execute()
                        break
                    except Exception as e:
                        if e.__class__.__name__ == "HttpError" and getattr(e.resp, "status", None) == 429 and attempt < 6:
                            time.sleep(2 ** attempt)
                            continue
                        print(f"\nStopped after writing {i - 1} of {len(data)} cell(s) ({d['range']} failed). "
                              f"Rerunning --apply is safe: cells already written show as already there.", file=sys.stderr)
                        raise
                print(f"\r  wrote {i}/{len(data)}", end="", file=sys.stderr, flush=True)
                time.sleep(0.25)
            print(file=sys.stderr)

        live3 = js.fetch_live(svc, sheet_id, live.tab)
    except Exception as e:
        if e.__class__.__name__ == "HttpError":
            sys.exit("Error: " + js.explain_http_error(e, args.creds, writing=True))
        raise
    by3 = live3.by_song()
    bad = [f"{c['song']} / {c['col']}" for c in todo2
           if live3.cell(by3[c["song"]][0], c["col"]) != c["solver"]]
    return (f"\n--apply: wrote {len(todo2)} cell(s) to the sheet (nothing else). Backup: {backup}.\n" +
            (f"VERIFY FAILED for: {', '.join(bad)}" if bad else "Read back from the sheet and verified."))


def main():
    p = argparse.ArgumentParser(description="Merge solver results with the live sheet into a pasteable CSV, reporting conflicts")
    p.add_argument("--songs",       default="songs.csv", help="the songs.csv the solver was run on (the 'base')")
    p.add_argument("--assignments", default="assignments.csv", help="solver output (default: assignments.csv)")
    p.add_argument("--sheet",       help="spreadsheet id or URL (default: the one recorded when songs.csv was downloaded)")
    p.add_argument("--date",        help="setlist date (YYYY-MM-DD or MM/DD/YYYY), looked up in the index sheet")
    p.add_argument("--tab",         help="tab name (default: first tab)")
    p.add_argument("--index-sheet", default=js.DEFAULT_INDEX_SHEET, help="sheet that maps dates to spreadsheet ids")
    p.add_argument("--creds",       help=f"service-account key (default: $JAM_GOOGLE_CREDS or {js.DEFAULT_CREDS})")
    p.add_argument("--output",      default="merged.csv", help="merged CSV to paste (default: merged.csv)")
    p.add_argument("--apply",       action="store_true",
                   help="write the APPLY cells into the live sheet (default: only write merged.csv)")
    p.add_argument("--batch",       action="store_true",
                   help="with --apply: write all cells in one request instead of one request per cell")
    p.add_argument("--header",      action="store_true", help="add a header row to the CSV (for viewing; not for pasting)")
    p.add_argument("--report",      default="merge_report.txt", help="also save the report here")
    args = p.parse_args()

    for label, path in (("base songs file", args.songs), ("assignments file", args.assignments)):
        if not Path(path).is_file():
            sys.exit(f"Error: {label} not found: {path}\n(looked relative to {Path.cwd()})")

    side = js.read_sidecar(args.songs) or {}
    svc  = js.get_service(creds=args.creds, write=args.apply)
    try:
        if args.sheet:
            sheet_id = js.sheet_id_from(args.sheet)
        elif args.date:
            sheet_id = js.sheet_id_for_date(svc, args.date, args.index_sheet)
        elif side.get("sheet_id"):
            sheet_id = side["sheet_id"]
        else:
            sys.exit("Error: say which sheet: --sheet ID_OR_URL or --date YYYY-MM-DD "
                     "(or download songs.csv with jam_solver.py --sheet/--date first).")
        same = sheet_id == side.get("sheet_id")
        if side.get("sheet_id") and not same:
            print(f"WARNING: {args.songs} was downloaded from a different spreadsheet "
                  f"({side['sheet_id']}); expect many conflicts/skips.\n", file=sys.stderr)
        tab = args.tab or (side.get("tab") if same else None)

        live = js.fetch_live(svc, sheet_id, tab)
    except Exception as e:                                  # HttpError etc.
        if e.__class__.__name__ == "HttpError":
            sys.exit("Error: " + js.explain_http_error(e, args.creds))
        raise

    base_cols, base_rows = read_csv_rows(args.songs)
    _, solver_rows       = read_csv_rows(args.assignments)
    slot_cols = [c for c in base_cols if c and c not in js.SKIP_COLUMNS]

    cells, notes, other = plan_merge(base_rows, solver_rows, live, slot_cols)
    block, first_row, c0, c1 = merged_block(live, cells, with_header=args.header)
    with open(args.output, "w", newline="") as f:
        csv.writer(f).writerows(block)

    nrows = len(block) - (1 if args.header else 0)
    paste = (f"\nWrote {args.output}: columns {c0}:{c1}, {nrows} sheet row(s), no header row"
             f"{' except the one --header added on top' if args.header else ''}.\n"
             f"Paste with {c0}{first_row} selected (the cell holding the first song's title). "
             f"The first line of the CSV is row {first_row}.")
    todo = [c for c in cells if c["status"] == "APPLY"]
    if args.apply:
        paste = apply_changes(svc, sheet_id, live, cells, notes, base_rows, solver_rows, slot_cols, args)
    report = render_report(cells, notes, other, live, paste)
    print(report)
    Path(args.report).write_text(report + "\n")
    print(f"\n(report saved to {args.report})")


if __name__ == "__main__":
    main()
