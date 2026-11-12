# Jam tools

Scripts for running a jam night's sign-ups: read musicians' emails, solve who plays what, and get the
result back into the live setlist spreadsheet without overwriting anyone's edits. Run them from the
directory holding the night's data (e.g. `~/band/LastCall/assignments`) with `uv run`; each script
declares its own dependencies.

## The process

```
 mbox emails ──jam_parse_emails.py──▶ parsed_<instrument>.csv ─┐
                                                               ├─▶ jam_solver.py ──▶ assignments*.csv, residual.txt, ...
 live sheet ──(--date / --sheet)──▶ songs.csv ─────────────────┘         │
                                                                         ▼
 live sheet (read again) ──────────────────────────────────▶ jam_merge.py ──▶ merged.csv ──(you paste)──▶ sheet
```

1. `jam_parse_emails.py` turns each instrument's emails into a preferences CSV (`parsed_bass.csv`, ...).
2. `jam_solver.py` solves the assignments. Give it `--date` or `--sheet` and it first downloads the
   live setlist into `songs.csv`; without them it uses the `songs.csv` already in the directory.
3. `jam_merge.py` re-reads the live sheet, merges the solver's results into it, and writes `merged.csv`
   for you to paste back.

So: the solver reads the live sheet only when you ask it to (`--date`/`--sheet`); the merge always reads
the live sheet fresh, since the point is to merge against what is in the sheet *now*. Neither script
writes to the spreadsheet unless you give `jam_merge.py` `--apply`; otherwise you paste `merged.csv` yourself.

```
uv run ~/src/jamtools/sheets/assignment/jam_solver.py --date 11/22/2026 --submissions parsed*.csv
uv run ~/src/jamtools/sheets/assignment/jam_merge.py                      # reuses the sheet recorded in songs.sheet.json
```

## Google access (`jam_sheets.py`)

Shared by the solver and merge. It uses a service-account key, looked up in this order: `--creds FILE`,
`$JAM_GOOGLE_CREDS`, `~/.config/googleserviceaccount.key`. The spreadsheet must be shared with the
account's `client_email` (Viewer is enough; access is read-only). The sheet is chosen by:

- `--sheet ID_OR_URL`, or
- `--date YYYY-MM-DD` / `MM/DD/YYYY`, looked up in the index sheet (date -> spreadsheet id; override with `--index-sheet`), or
- for `jam_merge.py` only, with neither given: the sheet recorded in `songs.sheet.json` when `songs.csv` was downloaded.

`--tab NAME` picks a tab (default: the first).

## jam_parse_emails.py

Reads an mbox (or plain text split by `---` lines), asks Claude to extract each person's instrument and
ranked song preferences, resolves names against `musicians.csv`, and writes `parsed_<instrument>.csv`.

```
uv run jam_parse_emails.py --emails bass.mbox --instrument bass [--songs songs.csv] [--musicians musicians.csv]
```

The API key is read from `~/keys/claude.apikey` if it exists, else `ANTHROPIC_API_KEY`. It prints token
usage (including cache hits) after the emails are parsed. Use `--no-prompt` to run unattended (no name
confirmation questions). It may add new people or aliases to `musicians.csv`.

## jam_solver.py

Integer-programming assignment of musicians to open slots. Maximizes `weight / rank` summed over all
assignments, where rank is the song's position in that person's preference list (rank 1 = first choice).

Inputs:
- `songs.csv`: one row per song; `index,time,song,artist,vocal,guitar1,guitar2,bass,drums,keys,notes`. A blank slot
  is open, `XX` means not needed, a name is pre-assigned (locked). Columns with blank headers are ignored.
- `--submissions FILE...`: the `parsed_*.csv` files (default `submissions.csv`).
- `musicians.csv`: `musician,instrument,weight,max_songs,aliases`. Every name in the songs or submissions
  must be here (or be an alias), or the solver warns. Locked names are canonicalized through the aliases.
- `--max-songs N` (default 2): songs per person; a person's `max_songs` overrides it.

Download options (all optional): `--sheet`, `--date`, `--tab`, `--index-sheet`, `--creds`. When it
downloads, an existing `songs.csv` is kept as `songs.csv.prev`, and the source sheet is recorded in
`songs.sheet.json` for `jam_merge.py`.

Outputs (all in the current directory unless overridden):

| File | Contents |
|---|---|
| `assignments.csv` | `songs.csv` layout with slots filled in. The input to `jam_merge.py`. |
| `assignments_table.txt` | ASCII table for reading. `XX` = not needed. |
| `assignments_by_musician.csv` | Flat instrument / musician / song list. |
| `assignment_acks.txt` | Per-player notes for emailing. |
| `residual.txt` | Unfilled slots with reasons, shutouts, and "Rank 3+" picks for manual review. |

Reading `residual.txt`: *capacity conflict* means people did bid but were all already at their song cap;
*NO BIDS RECEIVED* means nobody asked for that slot.

## jam_merge.py

Three-way merge, cell by cell, of the solver's changes into the **current** live sheet:

- **base**: `songs.csv` (the sheet as it was when the solver ran)
- **solver**: `assignments.csv`
- **live**: the sheet right now

For each slot cell the solver changed:

| Live cell is... | Result |
|---|---|
| still equal to base | **APPLY**: `merged.csv` gets the solver's value |
| already equal to the solver's value | already there; nothing to do |
| anything else (someone edited it) | **CONFLICT**: `merged.csv` keeps the live value; listed in the report |

`merged.csv` is the live sheet's `SONG..KEYS` columns (index, time and notes are left out), from the first
song row to the last, with every cell exactly as it is in the sheet now except the APPLY cells. Pasting it
therefore changes only those cells. Rows are matched by song title, so rows inserted or sorted in the sheet
since the export don't matter. A song missing from the sheet, listed twice, or without the column is
reported under SKIPPED. If other slot cells were edited in the sheet since the export, the report notes how many
(they are carried into the CSV as they are now); a large number means `songs.csv` is stale and worth re-downloading.

The report (also saved as `merge_report.txt`) gives the counts, every conflict, every change, and the cell
to paste at (e.g. select `C3`; the CSV has no header row, so its first line is the first song). `--header` adds a
header row on top for viewing only (don't paste it over the songs);
`--output`, `--report`, `--songs`, `--assignments` change the file names.

### Writing straight to the sheet: `--apply`

Pasting a block makes the sheet's version history show the whole block as changed. `jam_merge.py --apply`
instead writes **only the APPLY cells** (as plain text), one request per cell, so the history shows exactly
those cells and nothing else. (`--batch` sends them in one request, but Sheets' history then shows a bounding
block of changes rather than individual cells.) Conflicts and unchanged cells are never touched. It needs the service account to be an
**Editor** on the sheet; without `--apply` the scripts only ever read. Safety steps, in order:

1. saves the live sheet to `live-backup-<timestamp>.csv`;
2. re-reads the sheet and aborts, writing nothing, if any cell it is about to write (or any conflict) changed meanwhile;
3. writes, then reads everything back and reports whether it verified.

`merged.csv` and the report are still produced, so run once without `--apply` to review, then again with it.
A second `--apply` is harmless: everything shows as already there.

To check a paste, compare `merged.csv` with the sheet, or re-run `jam_merge.py` afterwards: it should show
everything as already there and no changes.
