#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pulp<4",   # 4.x changed the LpVariable API (no cat=) and wants numpy
#   "google-api-python-client",
#   "google-auth",
# ]
# ///
"""
Jam Session Assignment Solver
Assigns musicians to song slots using Integer Linear Programming.

Inputs:
  songs.csv       -- wide format: index,starttime,song,artist,vocal,guitar1,guitar2,bass,drums,keys
                     Cell = ""   -> open slot
                     Cell = "XX" -> slot not needed for this song
                     Cell = name -> pre-assigned (locked)
  submissions.csv -- musician preferences per instrument

Outputs:
  assignments.csv -- same wide format as songs.csv, with slots filled in
  residual.txt    -- unfilled slots (with reason), shutouts, low-satisfaction picks
"""

import csv
import re
import os
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pulp
except ImportError:
    sys.exit("PuLP not found. Run: pip install pulp")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOT_NEEDED   = "XX"
SKIP_COLUMNS = {"index", "starttime", "time", "song", "artist", "notes"}


# ---------------------------------------------------------------------------
# 1. Load data
# ---------------------------------------------------------------------------

def load_songs(path):
    """
    Wide format: one row per song, slot columns are everything except
    index / starttime / song / artist.

    Cell values:
      ""   -> open slot (needs filling)
      "XX" -> slot not needed (excluded from model entirely)
      else -> pre-assigned musician (locked)

    Returns:
      slots:     [(song, slot), ...] open slots for the solver
      locked:    {(song, slot): musician}  pre-assigned, not touched by solver
      all_slots: [(song, slot), ...]  every active slot in song order (no XX)
      song_rows: [raw dict, ...]  original rows for round-trip output
      slot_cols: [str, ...]  slot column names in file order
    """
    slots     = []
    locked    = {}
    all_slots = []
    song_rows = []

    with open(path, newline="") as f:
        reader    = csv.DictReader(f)
        # Normalise header names: lowercase + collapse spaces
        # so "GUITAR 2", "Guitar2", "guitar 2" all become "guitar2"
        def norm_col(c):
            return re.sub(r"\s+", "", c.strip().lower())
        raw_headers = reader.fieldnames or []
        col_map     = {h: norm_col(h) for h in raw_headers}  # original -> normalised
        headers     = [norm_col(h) for h in raw_headers]
        slot_cols   = [c for c in headers if c and c not in SKIP_COLUMNS]

        for row in reader:
            # normalise keys: strip, lowercase, collapse spaces
            row = {norm_col(k): (v or "").strip() for k, v in row.items()}
            song = row["song"]
            song_rows.append(row)

            for col in slot_cols:
                val = row.get(col, "")
                if val.upper() == NOT_NEEDED:
                    continue
                all_slots.append((song, col))
                if val:
                    locked[(song, col)] = val
                else:
                    slots.append((song, col))

    return slots, locked, all_slots, song_rows, slot_cols


def load_submissions(path):
    """
    CSV: musician, instrument, weight, pref1, pref2, ...

    Returns:
      musicians:   {name: {"weight": float, "instruments": set}}
      submissions: [{"musician", "instrument", "song", "rank"}, ...]
    """
    musicians   = {}
    submissions = []

    with open(path, newline="") as f:
        reader    = csv.DictReader(f)
        pref_cols = [c for c in (reader.fieldnames or []) if c.startswith("pref")]

        for row in reader:
            row  = {k.strip(): (v or "").strip() for k, v in row.items()}
            name = row["musician"]
            inst = row["instrument"]
            w    = float(row.get("weight") or 3)

            if name not in musicians:
                musicians[name] = {"weight": w, "instruments": set()}
            musicians[name]["instruments"].add(inst)
            musicians[name]["weight"] = max(musicians[name]["weight"], w)

            for rank_0, col in enumerate(pref_cols):
                song = row.get(col, "")
                if song:
                    submissions.append({
                        "musician":   name,
                        "instrument": inst,
                        "song":       song,
                        "rank":       rank_0 + 1,
                    })

    return musicians, submissions



def load_musicians(path):
    """
    Load musicians.csv: musician, instrument, weight, max_songs, aliases
    weight and max_songs may be blank.
    aliases is a comma-separated list of alternate names/handles.

    Returns:
      registry:   {(musician, instrument): {"weight": float|None, "max_songs": int|None}}
      alias_map:  {alias_lower: canonical_name}  for name normalisation
    Missing file is silently ignored (returns empty dicts).
    """
    registry  = {}
    alias_map = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                row  = {k.strip(): (v or "").strip() for k, v in row.items()}
                name = row.get("musician", "").strip()
                inst = row.get("instrument", "").strip()
                w    = row.get("weight", "").strip()
                ms   = row.get("max_songs", "").strip()
                raw_aliases = row.get("aliases", "")
                if name and inst:
                    registry[(name, inst)] = {
                        "weight":    float(w)  if w  else None,
                        "max_songs": int(ms)   if ms else None,
                    }
                    # Map each alias (and the canonical name itself) -> canonical
                    alias_map[name.lower()] = name
                    for alias in raw_aliases.split(","):
                        alias = alias.strip()
                        if alias:
                            alias_map[alias.lower()] = name
    except FileNotFoundError:
        pass
    return registry, alias_map


# ---------------------------------------------------------------------------
# 2. Solve
# ---------------------------------------------------------------------------

def solve(slots, locked, musicians, submissions, max_songs=2, musician_max_songs=None):
    """
    ILP maximising  sum( weight * (1/rank) )  over all assignments,
    with a heavy shutout-avoidance bonus and unfilled-slot penalty.

    Hard constraints:
      - each open slot filled by at most one musician
      - musician appears at most once per song (any slot)
      - musician total assignments <= max_songs (counting locked)

    Returns:
      assignments: {(song, slot): musician}  (includes locked)
      unfilled:    [(song, slot), ...]
      status:      str
    """
    slot_set = set(slots)

    # bids[(song, slot)] = [(musician, rank, weight), ...]
    bids = defaultdict(list)
    for sub in submissions:
        key = (sub["song"], sub["instrument"])
        if key in slot_set:
            bids[key].append((
                sub["musician"],
                sub["rank"],
                musicians[sub["musician"]]["weight"],
            ))

    prob = pulp.LpProblem("JamAssignment", pulp.LpMaximize)

    # --- Decision variables ---
    x = {}   # (musician, song, slot) -> BinaryVar
    for slot_key in slots:
        for (musician, rank, weight) in bids[slot_key]:
            vk = (musician, slot_key[0], slot_key[1])
            if vk not in x:
                safe = f"x_{'_'.join(vk)}".replace(" ", "_")
                x[vk] = pulp.LpVariable(safe, cat="Binary")

    # active musicians = those with at least one bid on an open slot
    active = {m for (m, _, _) in x}

    # y[m] = 1 if musician m gets at least one song (shutout avoidance)
    y = {m: pulp.LpVariable(f"y_{m}".replace(" ", "_"), cat="Binary")
         for m in active}

    # slack[slot] = 1 if slot ends up unfilled
    slack = {}
    for sk in slots:
        safe = f"u_{'_'.join(sk)}".replace(" ", "_")
        slack[sk] = pulp.LpVariable(safe, cat="Binary")

    SHUTOUT_BONUS    = 5.0
    UNFILLED_PENALTY = 20.0
    # Tie-break only: far smaller than any real score difference, so it never
    # changes who plays, just which guitar slot a flexible guitarist lands in.
    GUITAR1_TIEBREAK = 1e-3

    # A plain "guitar" request is fanned out by the parser into identical
    # guitar1 + guitar2 bids (same song, same rank). Without a tie-break the
    # solver picks between them arbitrarily, so nudge these toward guitar1.
    bid_rank = {(m, song, slot): r
                for (song, slot), bl in bids.items() for (m, r, _) in bl}
    flexible_guitar = {
        (m, song) for (m, song, slot), r in bid_rank.items()
        if slot == "guitar1" and bid_rank.get((m, song, "guitar2")) == r
    }

    # --- Objective ---
    obj = []
    for (musician, song, slot), var in x.items():
        rank   = next(r for (m, r, _) in bids[(song, slot)] if m == musician)
        weight = musicians[musician]["weight"]
        obj.append(weight * (1.0 / rank) * var)
        if slot == "guitar1" and (musician, song) in flexible_guitar:
            obj.append(GUITAR1_TIEBREAK * var)
    for yv in y.values():
        obj.append(SHUTOUT_BONUS * yv)
    for sv in slack.values():
        obj.append(-UNFILLED_PENALTY * sv)
    prob += pulp.lpSum(obj)

    # --- Constraints ---

    # 1. Each open slot: exactly one musician OR marked unfilled
    by_slot = defaultdict(list)
    for (musician, song, slot), var in x.items():
        by_slot[(song, slot)].append(var)

    for sk in slots:
        svars = by_slot[sk]
        if svars:
            prob += pulp.lpSum(svars) + slack[sk] == 1, \
                   f"fill_{'_'.join(sk)}".replace(" ", "_")
        else:
            prob += slack[sk] == 1, \
                   f"nobid_{'_'.join(sk)}".replace(" ", "_")

    # 2. Musician at most once per song (including locked pre-assignments)
    # Build set of (musician, song) pairs already locked — solver must not
    # assign that musician to any other slot on the same song.
    locked_songs = defaultdict(set)  # musician -> set of songs already locked
    for (song, slot), musician in locked.items():
        locked_songs[musician].add(song)

    by_musician_song = defaultdict(list)
    for (musician, song, slot), var in x.items():
        by_musician_song[(musician, song)].append(var)
    for (musician, song), vs in by_musician_song.items():
        if len(vs) > 1:
            # Multiple open slots on same song: at most one
            prob += pulp.lpSum(vs) <= 1, \
                   f"onesong_{musician}_{song}".replace(" ", "_")
        if song in locked_songs.get(musician, set()):
            # Already pre-assigned to this song: solver must not assign any slot
            prob += pulp.lpSum(vs) == 0, \
                   f"locked_onesong_{musician}_{song}".replace(" ", "_")

    # 3. Max songs per musician (locked count against cap)
    # Vocal + instrument on the same song counts as ONE song toward the cap.
    # We implement this with a per-(musician, song) indicator variable z[m,s]
    # that is 1 if the musician is assigned to ANY slot on that song, and
    # constrain sum(z[m,s] for s) <= cap.  This naturally collapses vocal+instrument
    # co-assignments on the same song into a single count.
    musician_max_songs = musician_max_songs or {}

    # locked_count: count distinct songs in locked assignments per musician
    # (vocal+instrument on same locked song still counts once)
    locked_songs_per_musician = defaultdict(set)
    for (song, slot), musician in locked.items():
        locked_songs_per_musician[musician].add(song)
    locked_count = {m: len(songs) for m, songs in locked_songs_per_musician.items()}

    # Group solver vars by (musician, song)
    by_musician = defaultdict(list)           # musician -> all vars
    by_musician_song = defaultdict(list)      # already built above, reuse pattern
    ms_vars = defaultdict(list)               # (musician, song) -> vars
    for (musician, song, slot), var in x.items():
        by_musician[musician].append(var)
        ms_vars[(musician, song)].append(var)

    # z[musician, song] = 1 if musician assigned to any slot on that song
    z = {}
    for (musician, song), vs in ms_vars.items():
        zv = pulp.LpVariable(
            f"z_{musician}_{song}".replace(" ", "_"), cat="Binary"
        )
        z[(musician, song)] = zv
        # z >= each x (if any slot assigned, z must be 1)
        for var in vs:
            prob += zv >= var,                    f"zlo_{musician}_{song}_{id(var)}".replace(" ", "_")
        # z <= sum(x) (z can only be 1 if at least one slot assigned)
        prob += zv <= pulp.lpSum(vs),                f"zhi_{musician}_{song}".replace(" ", "_")

    # Cap constraint on distinct songs
    for musician, vs in by_musician.items():
        per_musician_max = musician_max_songs.get(musician, max_songs)
        locked_distinct  = locked_count.get(musician, 0)
        # Also subtract locked songs that already have a solver slot on the same song
        # (locked vocal + open instrument on same song: locked song already counted)
        locked_song_set  = locked_songs_per_musician.get(musician, set())
        z_for_musician   = [
            zv for (m, song), zv in z.items()
            if m == musician and song not in locked_song_set
        ]
        cap = max(0, per_musician_max - locked_distinct)
        if z_for_musician:
            prob += pulp.lpSum(z_for_musician) <= cap,                    f"cap_{musician}".replace(" ", "_")

    # 4. Shutout indicator: y[m] <= sum(x for m)
    for musician in active:
        vs = by_musician.get(musician, [])
        if vs:
            prob += y[musician] <= pulp.lpSum(vs), \
                   f"shutind_{musician}".replace(" ", "_")
        else:
            prob += y[musician] == 0, \
                   f"shutzero_{musician}".replace(" ", "_")

    # --- Solve ---
    prob.solve(pulp.getSolver("PULP_CBC_CMD", msg=False))
    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal", "Feasible"):
        print(f"WARNING: Solver status: {status}")

    assignments = dict(locked)
    for (musician, song, slot), var in x.items():
        if (pulp.value(var) or 0) > 0.5:
            assignments[(song, slot)] = musician

    unfilled = [sk for sk, sv in slack.items() if (pulp.value(sv) or 0) > 0.5]

    return assignments, unfilled, status


# ---------------------------------------------------------------------------
# 3. Residual report
# ---------------------------------------------------------------------------

def build_report(slots, locked, all_slots, assignments, unfilled,
                 musicians, submissions, status):
    lines = []
    W = 60
    lines += ["=" * W, "JAM ASSIGNMENT RESIDUAL REPORT", "=" * W,
              f"Solver status: {status}", ""]

    # -- Unfilled slots --
    lines.append("── UNFILLED SLOTS " + "─" * (W - 18))
    if not unfilled:
        lines.append("  All slots filled.\n")
    else:
        # Build useful lookups for explaining why bidders weren't used
        bids_for = defaultdict(list)   # (song, slot) -> [musician]
        for sub in submissions:
            bids_for[(sub["song"], sub["instrument"])].append(sub["musician"])

        # Where each musician ended up
        musician_assignments = defaultdict(list)  # musician -> [(song, slot)]
        for (song, slot), musician in assignments.items():
            musician_assignments[musician].append((song, slot))

        # Per-musician cap (global default; we don't have per-musician here
        # but we can infer "at cap" by checking assignment count vs max possible)
        # We detect "at cap" conservatively: assigned count >= 2 (the usual max)
        # A more precise check isn't needed — the explanation is directional.
        # Count distinct songs in locked assignments (vocal+instrument same song = 1)
        locked_songs_by_m = defaultdict(set)
        for (song, slot), m in locked.items():
            locked_songs_by_m[m].add(song)
        locked_count = {m: len(songs) for m, songs in locked_songs_by_m.items()}

        for (song, slot) in sorted(unfilled):
            bidders = bids_for.get((song, slot), [])
            if not bidders:
                lines.append(f"  {song} / {slot}: NO BIDS RECEIVED")
            else:
                lines.append(f"  {song} / {slot}: capacity conflict")
                for bidder in sorted(bidders):
                    got = musician_assignments.get(bidder, [])
                    if not got:
                        # Bidder was shut out entirely — shouldn't happen often
                        lines.append(f"      {bidder} — not assigned anywhere (shutout)")
                    else:
                        elsewhere = ", ".join(f"{s}/{sl}" for s, sl in got)
                        total     = len(got) + locked_count.get(bidder, 0)
                        at_cap    = total >= musicians.get(bidder, {}).get("_cap", 2)
                        cap_note  = " [at cap]" if at_cap else ""
                        lines.append(f"      {bidder}{cap_note} — assigned to {elsewhere}")
        lines.append("")

    # -- Shutouts --
    lines.append("── SHUTOUTS " + "─" * (W - 12))
    open_songs = {s for (s, _) in slots}
    assigned   = set(assignments.values())
    shutouts   = [
        name for name in musicians
        if name not in assigned
        and name not in locked.values()
        and any(sub["song"] in open_songs
                for sub in submissions if sub["musician"] == name)
    ]
    if not shutouts:
        lines.append("  No shutouts.\n")
    else:
        for name in sorted(shutouts):
            bid_songs = sorted({s["song"] for s in submissions if s["musician"] == name})
            lines.append(f"  {name}: bid on {', '.join(bid_songs)}")
        lines.append("")

    # -- Satisfaction --
    lines.append("── SATISFACTION SUMMARY " + "─" * (W - 24))
    rank_lut = {(s["musician"], s["instrument"], s["song"]): s["rank"]
                for s in submissions}
    low = []
    for (song, slot), musician in sorted(assignments.items()):
        if (song, slot) in locked:
            continue
        rank = rank_lut.get((musician, slot, song))
        if isinstance(rank, int) and rank >= 3:
            low.append((musician, song, slot, rank))
    if low:
        lines.append("  Rank 3+ assignments (may warrant manual review):")
        for (musician, song, slot, rank) in low:
            lines.append(f"    {musician} → {song} / {slot}  (rank {rank})")
    else:
        lines.append("  All assignments within top-2 preferences.")
    lines.append("")

    # -- Per-musician --
    lines.append("── PER-MUSICIAN SUMMARY " + "─" * (W - 24))
    got = defaultdict(list)
    for (song, slot), musician in assignments.items():
        got[musician].append((song, slot))
    for name in sorted(musicians):
        w       = musicians[name]["weight"]
        entries = got.get(name, [])
        detail  = ", ".join(f"{s}/{sl}" for s, sl in entries) if entries else "— no assignment"
        lines.append(f"  {name} (w={w:.0f}): {detail}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Write output (same wide format as input)
# ---------------------------------------------------------------------------


def download_songs(args):
    """Fetch the live setlist into args.songs (see jam_sheets.py); remembers the sheet for jam_merge.py."""
    import shutil
    import jam_sheets as js
    svc = js.get_service(creds=args.creds)
    try:
        sheet_id = (js.sheet_id_from(args.sheet) if args.sheet else
                    js.sheet_id_for_date(svc, args.date, args.index_sheet or js.DEFAULT_INDEX_SHEET))
        live = js.fetch_live(svc, sheet_id, args.tab)
    except Exception as e:
        if e.__class__.__name__ == "HttpError":
            sys.exit("Error: " + js.explain_http_error(e, args.creds))
        raise
    if os.path.exists(args.songs):
        shutil.copyfile(args.songs, args.songs + ".prev")
    live.write_csv(args.songs)
    js.write_sidecar(args.songs, sheet_id, live.tab, args.date)
    print(f"Downloaded live setlist ({len(live.rows)} songs, tab {live.tab!r}) to {args.songs}")


def write_assignments_table(path, song_rows, slot_cols, assignments):
    """
    Write an ASCII table of assignments, one row per song, preserving
    index order from songs.csv.  Columns: index, song, then each slot.
    """
    SLOT_ORDER = ["vocal", "guitar1", "guitar2", "bass", "drums", "keys"]

    def slot_sort(c):
        try:    return SLOT_ORDER.index(c)
        except: return len(SLOT_ORDER)

    ordered_slots = sorted(slot_cols, key=slot_sort)

    # Build display rows
    display_cols = ["#", "song"] + ordered_slots
    rows = []
    for row in song_rows:
        song = row.get("song", "")
        idx  = row.get("index", "")
        r    = {"#": idx, "song": song}
        for col in ordered_slots:
            val = row.get(col, "")
            if val.upper() == NOT_NEEDED:
                r[col] = NOT_NEEDED
            else:
                r[col] = assignments.get((song, col), val or "")
        rows.append(r)

    # Column widths
    widths = {c: len(c) for c in display_cols}
    for r in rows:
        for c in display_cols:
            widths[c] = max(widths[c], len(str(r.get(c, ""))))

    def fmt_row(r):
        return "| " + " | ".join(str(r.get(c,"")).ljust(widths[c]) for c in display_cols) + " |"

    sep = "+-" + "-+-".join("-" * widths[c] for c in display_cols) + "-+"
    hdr = "| " + " | ".join(c.ljust(widths[c]) for c in display_cols) + " |"

    lines = [sep, hdr, sep] + [fmt_row(r) for r in rows] + [sep]
    Path(path).write_text("\n".join(lines) + "\n")

def write_assignments(path, song_rows, slot_cols, assignments):
    """Write back in the same wide format as songs.csv input."""
    out_fields = []
    # preserve original column order including index/starttime/artist if present
    if song_rows:
        out_fields = list(song_rows[0].keys())

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for row in song_rows:
            out = dict(row)
            song = row["song"]
            for col in slot_cols:
                orig = (row.get(col) or "").strip()
                if orig.upper() == NOT_NEEDED:
                    out[col] = NOT_NEEDED          # preserve XX
                elif orig:
                    out[col] = assignments.get((song, col), orig)  # pre-assignment, canonical name
                else:
                    out[col] = assignments.get((song, col), "")
            writer.writerow(out)



def write_assignments_flat(path, all_slots, assignments, locked):
    """
    Write a flat sorted view of assignments:
      instrument, musician, song
    Sorted by instrument then musician name.
    Useful for review; complements the wide setlist format.
    """
    SLOT_ORDER = ["vocal", "guitar1", "guitar2", "bass", "drums", "keys"]

    def slot_key(slot):
        try:
            return SLOT_ORDER.index(slot)
        except ValueError:
            return len(SLOT_ORDER)

    rows = []
    for (song, slot), musician in assignments.items():
        if musician:
            rows.append({"instrument": slot, "musician": musician, "song": song})

    rows.sort(key=lambda r: (slot_key(r["instrument"]), r["musician"].lower()))

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["instrument", "musician", "song"])
        writer.writeheader()
        writer.writerows(rows)


def write_player_notes(path, song_rows, assignments):
    """
    Write a plain-text file suitable for emailing to each player.
    Format per player:
      ---
      Canonical Player Name

      Song Title [instrument]
      Song Title [instrument]
      ...
    Songs appear in setlist index order.
    """
    # Build ordered song list from song_rows (preserves index order)
    song_order = [row.get("song", "") for row in song_rows]

    # Invert assignments: musician -> [(song, slot), ...]
    by_musician = defaultdict(list)
    for (song, slot), musician in assignments.items():
        if musician:
            by_musician[musician].append((song, slot))

    # Sort each musician's songs by setlist order
    song_index = {song: i for i, song in enumerate(song_order)}
    for musician in by_musician:
        by_musician[musician].sort(key=lambda ss: song_index.get(ss[0], 999))

    def slot_label(slot):
        labels = {
            "guitar1": "G1", "guitar2": "G2",
            "vocal": "vocal", "bass": "bass",
            "drums": "drums", "keys": "keys",
        }
        label = labels.get(slot, slot)
        return f" [{label}]"

    lines = []
    for musician in sorted(by_musician):
        lines.append("---")
        lines.append(musician)
        lines.append("")
        for (song, slot) in by_musician[musician]:
            lines.append(f"{song}{slot_label(slot)}")
        lines.append("")

    Path(path).write_text("\n".join(lines) + "\n")

# ---------------------------------------------------------------------------
# 5. Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(description="Jam session slot assignment solver")
    p.add_argument("--songs",       default="songs.csv",
                   help="songs.csv to read (and to write, if --sheet/--date downloads it)")
    p.add_argument("--sheet",       help="download the live setlist first: spreadsheet id or URL "
                                         "(overwrites --songs; the old copy is kept as .prev)")
    p.add_argument("--date",        help="download the live setlist for this date (YYYY-MM-DD or MM/DD/YYYY), "
                                         "looked up in the index sheet")
    p.add_argument("--tab",         help="tab to download (default: first tab)")
    p.add_argument("--index-sheet", help="sheet mapping dates to spreadsheet ids (default: built-in)")
    p.add_argument("--creds",       help="Google service-account key (default: $JAM_GOOGLE_CREDS or "
                                         "~/.config/googleserviceaccount.key)")
    p.add_argument("--musicians",   default="musicians.csv",
                   help="musician/instrument/weight registry (default: musicians.csv)")
    p.add_argument("--submissions", nargs="+", action="extend", default=None,
                   metavar="FILE",
                   help="Submissions CSV (repeatable: --submissions bass.csv "
                        "--submissions guitar1.csv ...); defaults to submissions.csv")
    p.add_argument("--output",      default="assignments.csv")
    p.add_argument("--output-notes", default="assignment_acks.txt",
                   help="Per-player email notes (default: assignment_acks.txt)")
    p.add_argument("--output-table", default="assignments_table.txt",
                   help="ASCII table of assignments (default: assignments_table.txt)")
    p.add_argument("--output-flat", default="assignments_by_musician.csv",
                   help="Flat instrument/musician/song view, sorted (default: assignments_by_musician.csv)")
    p.add_argument("--report",      default="residual.txt")
    p.add_argument("--max-songs",   type=int, default=2)
    args = p.parse_args()

    if args.submissions is None:
        args.submissions = ["submissions.csv"]

    if args.sheet or args.date:
        download_songs(args)

    for label, path in [("songs file", args.songs)] + [("submissions file", f) for f in args.submissions]:
        if not os.path.isfile(path):
            sys.exit(f"Error: {label} not found: {path}\n(looked relative to {os.getcwd()})")

    print(f"Loading songs from {args.songs}...")
    slots, locked, all_slots, song_rows, slot_cols = load_songs(args.songs)
    print(f"  {len(all_slots)} active slots, {len(slots)} open, {len(locked)} pre-assigned")

    # Load weight registry
    registry, alias_map = load_musicians(args.musicians)
    if registry:
        print(f"Loaded {len(registry)} musician/instrument entries from {args.musicians}")
    else:
        print(f"No musicians.csv found at {args.musicians} — using submission weights (default 3)")

    all_musicians   = {}
    all_submissions = []
    for path in args.submissions:
        print(f"Loading submissions from {path}...")
        musicians, submissions = load_submissions(path)
        print(f"  {len(musicians)} musicians, {len(submissions)} preference entries")
        for name, info in musicians.items():
            if name not in all_musicians:
                all_musicians[name] = info
            else:
                all_musicians[name]["weight"] = max(
                    all_musicians[name]["weight"], info["weight"]
                )
                all_musicians[name]["instruments"] |= info["instruments"]
        all_submissions.extend(submissions)

    # Normalise musician names in submissions using alias map
    for sub in all_submissions:
        sub["musician"] = alias_map.get(sub["musician"].lower(), sub["musician"])
    # Rebuild all_musicians with normalised names
    all_musicians = {}
    for sub in all_submissions:
        name = sub["musician"]
        if name not in all_musicians:
            all_musicians[name] = {"weight": 3.0, "instruments": set()}
        all_musicians[name]["instruments"].add(sub["instrument"])
    # Pre-assigned names in songs.csv go through the same alias map, so that
    # output, once-per-song and max-songs checks all use the canonical name
    locked = {k: alias_map.get(m.lower(), m) for k, m in locked.items()}
    for musician in locked.values():
        if musician not in all_musicians:
            all_musicians[musician] = {"weight": 3.0, "instruments": set()}

    # Flag names that musicians.csv doesn't know (no weight / alias applied)
    if registry:
        known   = {name for (name, _) in registry}
        unknown = sorted({m for m in all_musicians if m not in known})
        if unknown:
            print(f"  WARNING: not in {args.musicians} (check spelling or add an alias): "
                  + ", ".join(unknown))

    # Apply weights and per-musician max_songs from registry.
    DEFAULT_WEIGHT = 3.0
    musician_max_songs = {}
    for name, info in all_musicians.items():
        entries = [
            registry[(name, inst)]
            for inst in info["instruments"]
            if (name, inst) in registry
        ]
        weights  = [e["weight"]    for e in entries if e["weight"]    is not None]
        max_vals = [e["max_songs"] for e in entries if e["max_songs"] is not None]
        if weights:
            info["weight"] = max(weights)
        elif not weights and registry:
            info["weight"] = DEFAULT_WEIGHT
        if max_vals:
            musician_max_songs[name] = max(max_vals)

    # Store effective cap on each musician for use in reporting
    for name, info in all_musicians.items():
        info["_cap"] = musician_max_songs.get(name, args.max_songs)

    print(f"  Total: {len(all_musicians)} musicians, {len(all_submissions)} entries")

    print("Solving...")
    assignments, unfilled, status = solve(
        slots, locked, all_musicians, all_submissions, args.max_songs,
        musician_max_songs=musician_max_songs
    )
    print(f"  Solver: {status} | filled: {len(assignments)} | unfilled: {len(unfilled)}")

    write_assignments(args.output, song_rows, slot_cols, assignments)
    print(f"Assignments written to {args.output}")

    flat_path = getattr(args, "output_flat")
    write_assignments_flat(flat_path, all_slots, assignments, locked)
    print(f"Flat view written to {flat_path}")

    table_path = getattr(args, "output_table")
    write_assignments_table(table_path, song_rows, slot_cols, assignments)
    print(f"ASCII table written to {table_path}")

    notes_path = getattr(args, "output_notes")
    write_player_notes(notes_path, song_rows, assignments)
    print(f"Assignment acks written to {notes_path}")

    report = build_report(slots, locked, all_slots, assignments, unfilled,
                          all_musicians, all_submissions, status)
    Path(args.report).write_text(report)
    print(f"Report written to {args.report}\n")
    print(report)


if __name__ == "__main__":
    main()
