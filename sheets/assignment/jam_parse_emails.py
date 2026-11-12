#!/usr/bin/env python3
"""
Jam Email Parser
Reads emails from an mbox file (exported from Thunderbird etc.) or a plain
text file with emails separated by '---' lines, calls the Claude API to
extract structured submission data, and outputs rows ready to append to a
per-instrument submissions file.

Usage:
  python jam_parse_emails.py --emails bass_emails.mbox [--songs songs.csv] \
                             --instrument bass \
                             [--output parsed_bass.csv] \
                             [--append submissions_bass.csv]

--songs accepts the same wide-format CSV as jam_solver.py
(index,starttime,song,artist,...). Only the song and artist columns are
used here; slot columns and XX markers are ignored.

--instrument sets the default instrument for this email thread.
The model still flags if an email appears to be for a different instrument.

Input formats (auto-detected):
  mbox       -- standard mbox file as exported by Thunderbird
  plain text -- emails separated by lines containing only '---'

Output columns: musician, instrument, weight, pref1, pref2, ...
weight defaults to 3; fill in correct values before running the solver.

The solver accepts multiple --submissions files:
  python jam_solver.py --submissions submissions_bass.csv \
                       --submissions submissions_guitar1.csv ...
"""

import argparse
import csv
import email as emaillib
import email.header
import email.utils
import difflib
import json
import mailbox
import os
import re
import sys
import unicodedata
import urllib.request
import urllib.error
from collections import defaultdict
from pathlib import Path


API_URL   = "https://api.anthropic.com/v1/messages"
MODEL     = "claude-sonnet-5-5"
SKIP_COLS = {"index", "time", "song", "artist", "notes"}
NOT_NEEDED = "XX"

VALID_INSTRUMENTS = {"vocal", "guitar", "guitar1", "guitar2", "bass", "drums", "keys"}


# ---------------------------------------------------------------------------
# 1. Load canonical song catalog
# ---------------------------------------------------------------------------

def load_song_catalog(path):
    """
    Returns list of dicts: {index, title, artist, vocal}
    index and vocal may be empty if not present or not applicable.
    """
    def norm_col(c):
        return re.sub(r"\s+", "", c.strip().lower())
    catalog = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row    = {norm_col(k): (v or "").strip() for k, v in row.items()}
            title  = row.get("song",   "")
            artist = row.get("artist", "")
            idx    = row.get("index",  "")
            vocal  = row.get("vocal",  "")
            if vocal.upper() == "XX":
                vocal = ""
            if title:
                catalog.append({
                    "index":  idx,
                    "title":  title,
                    "artist": artist,
                    "vocal":  vocal,
                })
    return catalog


# ---------------------------------------------------------------------------
# 2. Load emails (mbox or plain delimited text)
# ---------------------------------------------------------------------------

def html_to_text(html):
    """Minimal HTML-to-text: strip tags, decode common entities."""
    html = re.sub(r"(?is)<(head|style|script)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<(br|p|div|tr|li|blockquote)[^>]*>", "\n", html)
    html = re.sub(r"<[^>]+>", "", html)
    entities = {"&amp;": "&", "&lt;": "<", "&gt;": ">",
                "&quot;": '"', "&#39;": "'", "&nbsp;": " "}
    for ent, ch in entities.items():
        html = html.replace(ent, ch)
    lines = [l.strip() for l in html.splitlines()]
    squeezed = []
    prev_blank = False
    for l in lines:
        blank = (l == "")
        if blank and prev_blank:
            continue
        squeezed.append(l)
        prev_blank = blank
    return "\n".join(squeezed).strip()


def extract_body(msg):
    """
    Extract plain-text body from an email.Message object.
    Prefers text/plain; falls back to converting text/html.
    Skips attachments.
    """
    def decode_part(part):
        raw = part.get_payload(decode=True)
        charset = part.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")

    plain_parts = []
    html_parts  = []

    if msg.is_multipart():
        for part in msg.walk():
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ct = part.get_content_type()
            if ct == "text/plain":
                plain_parts.append(decode_part(part))
            elif ct == "text/html":
                html_parts.append(decode_part(part))
    else:
        ct = msg.get_content_type()
        if ct == "text/plain":
            plain_parts.append(decode_part(msg))
        elif ct == "text/html":
            html_parts.append(decode_part(msg))

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        return html_to_text("\n\n".join(html_parts)).strip()
    return ""


def sender_info(msg):
    """
    Extract (display_name, address) from the From header.
    The display name is decoded from RFC 2047 (=?utf-8?...?=) if needed.
    Either may be "".
    """
    from_hdr = str(email.header.make_header(
        email.header.decode_header(msg.get("From") or "")))
    name, addr = email.utils.parseaddr(from_hdr)
    return name.strip().strip('"\''), addr.strip().lower()


def load_emails(path):
    """
    Auto-detect format:
      mbox       -- first non-empty line starts with 'From '
      plain text -- split on '---' delimiter lines

    Returns list of (sender_name, sender_addr, body_text) tuples.
    sender_name / sender_addr come from mbox headers, "" for plain text.
    """
    text = Path(path).read_text(errors="replace")
    first_line = next((l for l in text.splitlines() if l.strip()), "")

    if first_line.startswith("From "):
        results = []
        mbox = mailbox.mbox(path)
        for msg in mbox:
            body       = extract_body(msg)
            name, addr = sender_info(msg)
            if body:
                results.append((name, addr, body))
        mbox.close()
        return results
    else:
        parts = re.split(r"(?m)^---\s*$", text)
        return [("", "", p.strip()) for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# 3. Build system prompt
# ---------------------------------------------------------------------------

def build_system_prompt(default_instrument):
    if default_instrument:
        guitar_family = default_instrument in ("guitar", "guitar1", "guitar2")
        mismatch_note = (
            "NOTE: guitar, guitar1, guitar2, lead guitar, and rhythm guitar are all "
            "the SAME family for mismatch purposes — do NOT flag these as mismatches."
            if guitar_family else ""
        )
        instrument_rule = f"""\
- DEFAULT INSTRUMENT: these emails come from the '{default_instrument}' thread.
  Assign instrument="{default_instrument}" to every submission UNLESS the email
  clearly indicates the person plays a *completely different* instrument
  (e.g. bass, drums, keys, vocals — not a guitar variant).
  {mismatch_note}
  If you detect a genuine mismatch, still extract the submission but set
  "instrument_warning": "appears to be <detected instrument>, not {default_instrument}".
- Do NOT infer instrument from song choices or playing style descriptions.
- GUITAR SPECIAL CASE (applies when default instrument is guitar, guitar1, or guitar2):
  "lead guitar", "lead", or "G1" → guitar1; "rhythm guitar", "rhythm", or "G2" → guitar2.
  If neither is specified for a song, use instrument="guitar" (unspecified);
  the post-processor will fan it out to both guitar1 and guitar2 rows."""
    else:
        instrument_rule = """\
- Infer instrument from what the musician says they play.
  Treat "lead guitar" / "lead" as guitar1; "rhythm guitar" / "rhythm" as guitar2.
  If guitar is mentioned without lead/rhythm/1/2 qualification, use "guitar" (unspecified).
  Use one of: vocal, guitar1, guitar2, guitar, bass, drums, keys, or UNKNOWN."""

    return f"""\
You are a data extraction assistant for a music jam session organiser.
Your job is to read an email from a musician and extract their song preferences.

Return ONLY a JSON array. No explanation, no preamble, no markdown fences — raw JSON starting with "[" and ending with "]", even if you detect an instrument mismatch or anything else unusual. All notes go in the appropriate fields. Each element is:

{{
  "musician": "canonical name from the known-musicians list, or First Last as given",
  "name_as_written": "the name exactly as it appeared (Player: line, sign-off, or header)",
  "instrument": "one of: vocal, guitar1, guitar2, bass, drums, keys",
  "preferences": ["Song Title 1", "Song Title 2", ...],
  "unmatched": ["song references not found in the canonical list"],
  "instrument_warning": "optional — only if a thread/instrument mismatch is detected",
  "proxy": false
}}

Rules:
{instrument_rule}
- MUSICIAN NAME (in priority order):
  1. If the email body contains a line matching "Player: <name>" (case-insensitive),
     use that name. This is a proxy submission on behalf of another musician.
     Set "proxy": true in the output object.
  2. Otherwise use a name or sign-off found in the body.
  3. Otherwise use the sender name or address from email headers (provided below).
  Then, if a list of known musicians is provided and the name clearly refers to one
  of them (nickname, first name only, initial, alias, or email address listed),
  put that musician's canonical name EXACTLY as listed in "musician".
  If it is ambiguous or not on the list, put the best "First Last" you can.
  Always put the original, unmodified name in "name_as_written".
Match song references in the email to the canonical list using these methods,
in descending priority:
  1. SONG TITLE: direct or fuzzy match to the title field.
  2. ARTIST: "I'll take the BB King songs" / "anything by SRV" → expand to all songs
     by that artist in list order at that rank position.
     Fuzzy-match artist names ("Aretha" → "Aretha Franklin", "SRV" → "Stevie Ray Vaughan").
  3. VOCALIST NAME: if a name is mentioned that is not an artist name in the catalog,
     check whether it matches the vocalist field of any song. If so, treat it as a
     reference to that song (or all songs with that vocalist).
     Example: "I want to do the Carlos numbers" → if Carlos is listed as vocalist
     on certain songs, include those songs.
  4. INDEX NUMBER: if a number is mentioned (e.g. "songs 3 and 7" or "number 12"),
     match against the index field in the catalog.
     Only use this if no title/artist/vocalist match applies first.

- Preserve preference order exactly as given (first mentioned = rank 1).
  If no explicit order, use order of appearance in the text.
- If a musician submits for multiple instruments, emit one object per instrument.
- Put song references not in the canonical list in "unmatched" (not in "preferences").
- If you cannot determine the musician's name, use "UNKNOWN".
- Return [] if no useful data can be extracted.
"""


# ---------------------------------------------------------------------------
# 4. Call Claude API
# ---------------------------------------------------------------------------

# Running totals of token usage across every API call in this run.
STATS = {"calls": 0, "input": 0, "output": 0, "cache_write": 0, "cache_read": 0}


def print_stats():
    """Summarize API usage for the run (called after all emails are parsed)."""
    n = STATS["calls"]
    if not n:
        return
    total_in = STATS["input"] + STATS["cache_write"] + STATS["cache_read"]
    hit = 100.0 * STATS["cache_read"] / total_in if total_in else 0.0
    print(f"\n── API USAGE ({MODEL}) ──────────────────────────────")
    print(f"  API calls:                 {n:>10,}")
    print(f"  Input tokens (uncached):   {STATS['input']:>10,}")
    print(f"  Cache write tokens:        {STATS['cache_write']:>10,}")
    print(f"  Cache read tokens:         {STATS['cache_read']:>10,}")
    print(f"  Output tokens:             {STATS['output']:>10,}")
    print(f"  Total input tokens:        {total_in:>10,}  ({hit:.0f}% served from cache)")
    print(f"  Avg per call:              {total_in / n:>10,.0f} in / {STATS['output'] / n:,.0f} out")


def call_api(api_key, messages, system_prompt):
    """Make one API call; return raw text response."""
    payload = json.dumps({
        "model":      MODEL,
        "max_tokens": 1000,
        # cache_control caches the whole prefix (system + the song list/roster block marked below)
        "system":     [{"type": "text", "text": system_prompt,
                        "cache_control": {"type": "ephemeral"}}],
        "messages":   messages,
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"API error {e.code}: {e.read().decode()}")
    u = data.get("usage") or {}
    STATS["calls"]       += 1
    STATS["input"]       += u.get("input_tokens", 0) or 0
    STATS["output"]      += u.get("output_tokens", 0) or 0
    STATS["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
    STATS["cache_read"]  += u.get("cache_read_input_tokens", 0) or 0
    return "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")


def clean_json(raw):
    """Strip markdown fences and return cleaned string."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$",       "", raw.strip())
    return raw.strip()


def parse_email_text(sender_name, sender_addr, body_text, catalog, default_instrument,
                     roster=None):
    """
    roster: {canonical_name: [aliases...]} of known musicians, shown to the
    model so it can map nicknames / addresses to canonical names.
    """
    # Key file wins if present; otherwise fall back to the environment.
    key_file = Path("~/keys/claude.apikey").expanduser()
    api_key = key_file.read_text().strip() if key_file.is_file() else ""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        sys.exit("Put the key in ~/keys/claude.apikey or set ANTHROPIC_API_KEY before running.")

    def catalog_line(entry):
        parts = [f"[{entry['index']}]"] if entry["index"] else []
        parts.append(entry["title"])
        if entry["artist"]:
            parts.append(f"by {entry['artist']}")
        if entry["vocal"]:
            parts.append(f"(vocalist: {entry['vocal']})")
        return "  - " + " ".join(parts)

    canonical   = "\n".join(catalog_line(e) for e in catalog)
    sender_hint = " ".join(filter(None, [sender_name, f"<{sender_addr}>" if sender_addr else ""]))
    sender_line = f"Sender (from email headers): {sender_hint}\n\n" if sender_hint else ""
    roster_block = ""
    if roster:
        lines = [f"  - {name}" + (f" (aka {', '.join(aliases)})" if aliases else "")
                 for name, aliases in sorted(roster.items(), key=lambda kv: kv[0].lower())]
        roster_block = "Known musicians (canonical name, aka aliases):\n" + "\n".join(lines) + "\n\n"
    # Song list + roster are identical for every email in a run, so they go in their own block
    # marked cacheable; only the per-email part after it changes.
    user_content = [
        {"type": "text",
         "text": (f"Canonical song list (index, title, artist, vocalist):\n{canonical}\n\n"
                  f"{roster_block}"),
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": f"{sender_line}Email body:\n\"\"\"\n{body_text}\n\"\"\""},
    ]

    system_prompt = build_system_prompt(default_instrument)
    messages      = [{"role": "user", "content": user_content}]

    # First attempt
    raw = clean_json(call_api(api_key, messages, system_prompt))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Retry: show the model what it returned and ask it to fix it
    print("(retrying...)", end=" ", flush=True)
    messages += [
        {"role": "assistant", "content": raw},
        {"role": "user",      "content":
            "Your response was not valid JSON. "
            "Return ONLY the JSON array — no explanation, no preamble. "
            "Start your response with [ and end with ]."},
    ]
    raw2 = clean_json(call_api(api_key, messages, system_prompt))
    try:
        return json.loads(raw2)
    except json.JSONDecodeError as e:
        print(f"  WARNING: could not parse API response after retry: {e}")
        print(f"  Raw: {raw2[:300]}")
        return []


# ---------------------------------------------------------------------------
# 5. Post-process entries
# ---------------------------------------------------------------------------

# Synonyms resolved before the model returns; this catches anything it missed.
GUITAR_SYNONYMS = {
    "guitar":  None,      # unspecified → fan out to both
    "lead":    "guitar1",
    "rhythm":  "guitar2",
}

def resolve_instrument(inst):
    """
    Normalise instrument string.
    Returns "guitar1", "guitar2", "guitar" (unspecified), or the value unchanged.
    """
    if inst is None:
        return None
    i = inst.strip().lower()
    # Exact synonym map
    if i in GUITAR_SYNONYMS:
        return "guitar" if GUITAR_SYNONYMS[i] is None else GUITAR_SYNONYMS[i]
    # Prefix patterns
    if i in ("lead guitar", "guitar lead", "guitar 1", "gtr1", "gtr 1"):
        return "guitar1"
    if i in ("rhythm guitar", "guitar rhythm", "guitar 2", "gtr2", "gtr 2"):
        return "guitar2"
    return inst


def make_row(musician, inst, prefs):
    row = {"musician": musician, "instrument": inst}
    for i, song in enumerate(prefs, 1):
        row[f"pref{i}"] = song
    return row


def process_entries(entries, default_instrument):
    rows                = []
    instrument_warnings = []
    unmatched_warnings  = []

    for entry in entries:
        musician  = entry.get("musician", "UNKNOWN")
        raw_inst  = entry.get("instrument") or default_instrument or "UNKNOWN"
        inst      = resolve_instrument(raw_inst)
        prefs     = entry.get("preferences", [])
        unmatched = entry.get("unmatched", [])
        iw        = entry.get("instrument_warning", "")

        proxy = entry.get("proxy", False)
        if proxy:
            instrument_warnings.append(f"  ℹ {musician}: proxy submission (sender: {entry.get('_sender', '?')})")
        if iw:
            instrument_warnings.append(f"  ⚠ {musician}: {iw}")
        if unmatched:
            unmatched_warnings.append(
                f"  ⚠ {musician} ({inst}): unmatched songs: {', '.join(unmatched)}"
            )

        if inst == "guitar":
            # Unspecified guitar → emit one row for each guitar slot.
            # The solver treats guitar1 and guitar2 as independent slots,
            # so the musician will be assigned to whichever slot is available,
            # subject to the one-slot-per-song constraint.
            rows.append(make_row(musician, "guitar1", prefs))
            rows.append(make_row(musician, "guitar2", prefs))
        else:
            rows.append(make_row(musician, inst, prefs))

    return rows, instrument_warnings, unmatched_warnings


# ---------------------------------------------------------------------------
# 6. Preview
# ---------------------------------------------------------------------------

def print_preview(rows, instrument_warnings, unmatched_warnings, default_weight):
    print("\n── PARSED RESULTS PREVIEW ──────────────────────────────")
    for row in rows:
        prefs = [v for k, v in sorted(row.items()) if k.startswith("pref") and v]
        print(f"  {row['musician']} ({row['instrument']}, w={default_weight}): "
              + " > ".join(prefs))

    if instrument_warnings:
        print("\n  INSTRUMENT MISMATCH WARNINGS (check before appending):")
        for w in instrument_warnings:
            print(w)
    if unmatched_warnings:
        print("\n  UNMATCHED SONG REFERENCES:")
        for w in unmatched_warnings:
            print(w)
    print()


# ---------------------------------------------------------------------------
# 7. CSV I/O
# ---------------------------------------------------------------------------

def write_csv(path, rows, default_weight):
    max_pref  = max((max((int(k[4:]) for k in r if k.startswith("pref")), default=0)
                     for r in rows), default=5)
    pref_cols = [f"pref{i}" for i in range(1, max_pref + 1)]
    fieldnames = ["musician", "instrument", "weight"] + pref_cols

    for row in rows:
        row.setdefault("weight", default_weight)
        for col in pref_cols:
            row.setdefault(col, "")

    # Deduplicate on (musician, instrument), keeping last occurrence
    seen = {}
    for row in rows:
        key = (row.get("musician",""), row.get("instrument",""))
        seen[key] = row
    rows = sorted(seen.values(), key=lambda r: r.get("musician","").lower())

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return pref_cols


def append_to_submissions(path, new_rows, new_pref_cols, default_weight):
    existing        = []
    existing_fields = []
    try:
        with open(path, newline="") as f:
            reader          = csv.DictReader(f)
            existing_fields = list(reader.fieldnames or [])
            existing        = list(reader)
    except FileNotFoundError:
        existing_fields = ["musician", "instrument", "weight"] + new_pref_cols

    existing_prefs = [c for c in existing_fields if c.startswith("pref")]
    all_prefs      = sorted(
        set(existing_prefs) | set(new_pref_cols),
        key=lambda c: int(c[4:])
    )
    fieldnames = ["musician", "instrument", "weight"] + all_prefs

    for row in new_rows:
        row.setdefault("weight", default_weight)

    all_rows = existing + new_rows
    for row in all_rows:
        for col in fieldnames:
            row.setdefault(col, "")

    # Deduplicate on (musician, instrument), keeping last occurrence, sort by name
    seen = {}
    for row in all_rows:
        key = (row.get("musician",""), row.get("instrument",""))
        seen[key] = row
    all_rows = sorted(seen.values(), key=lambda r: r.get("musician","").lower())

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)




def norm_name(s):
    """
    Normalise a name or address for alias lookup:
    lowercase, accents stripped, "Last, First" -> "first last",
    punctuation (except @ . in addresses) dropped, whitespace collapsed.
    """
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.strip().strip('"\'').lower()
    if "@" in s:
        return s
    if s.count(",") == 1:
        last, first = (p.strip() for p in s.split(","))
        s = f"{first} {last}"
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class NameResolver:
    """
    Maps the names found in emails (header display name, header address,
    model-extracted name) to canonical musician names from musicians.csv.

    Resolution order for each parsed entry:
      1. Any raw name/address that is a known alias (normalised match).
      2. The model's own pick, if it is a canonical name (confirmed with
         the user when interactive, since the model can guess wrong).
      3. Fuzzy suggestions; the user picks one, types a new name, or skips.

    Whatever the user confirms is remembered for the rest of the run and
    written back to the aliases column of musicians.csv.
    """

    def __init__(self, path, interactive=None):
        self.alias_map   = {}                 # norm alias -> canonical
        self.roster      = defaultdict(list)  # canonical -> [aliases as written]
        self.new_aliases = defaultdict(set)   # canonical -> aliases learned this run
        self.log         = []                 # resolution notes for the preview
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    row  = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                    name = row.get("musician", "")
                    if not name or name == "UNKNOWN":
                        continue
                    self.roster.setdefault(name, [])
                    self.alias_map[norm_name(name)] = name
                    for alias in row.get("aliases", "").split(","):
                        alias = alias.strip()
                        if alias:
                            self.alias_map[norm_name(alias)] = name
                            if alias not in self.roster[name]:
                                self.roster[name].append(alias)
        except FileNotFoundError:
            pass

    def lookup(self, raw):
        return self.alias_map.get(norm_name(raw)) if raw else None

    def learn(self, canonical, *raws):
        """Record raws as aliases of canonical (skips blanks and existing ones)."""
        self.roster.setdefault(canonical, [])
        self.alias_map.setdefault(norm_name(canonical), canonical)
        for raw in raws:
            key = norm_name(raw)
            if not key or key == "unknown" or self.alias_map.get(key) == canonical:
                continue
            if key in self.alias_map:   # alias already belongs to someone else
                continue
            self.alias_map[key] = canonical
            self.new_aliases[canonical].add(raw.strip())
            self.roster[canonical].append(raw.strip())

    def suggest(self, raws, n=5):
        """Close canonical names for any of raws, best first."""
        keys   = list(self.alias_map)
        scores = {}
        for raw in raws:
            r = norm_name(raw)
            if not r or r == "unknown":
                continue
            if "@" in r:
                r = re.sub(r"[\d._-]+", " ", r.split("@")[0]).strip()
            for k in keys:
                score = difflib.SequenceMatcher(None, r, k).ratio()
                # A lone first name / last name that matches a token counts too
                tokens = k.split()
                if r in tokens or any(t in r.split() for t in tokens if len(t) > 2):
                    score = max(score, 0.7)
                canon = self.alias_map[k]
                scores[canon] = max(scores.get(canon, 0), score)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return [c for c, s in ranked if s >= 0.55][:n]

    def _ask(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    def resolve(self, entry, sender_name, sender_addr, body):
        """Return the canonical name for one parsed entry."""
        model_name = (entry.get("musician") or "").strip()
        written    = (entry.get("name_as_written") or "").strip()
        proxy      = entry.get("proxy", False)
        # For a proxy ("Player: X") the header is the sender, not the player
        # Address first: it is the most reliable identity when it's known.
        raws = [written] if proxy else [sender_addr, written, sender_name]
        raws = [r for r in raws if r]
        shown = written or model_name or sender_name or sender_addr or "UNKNOWN"
        hdr   = " ".join(filter(None, [sender_name, f"<{sender_addr}>" if sender_addr else ""]))

        # 1. Deterministic alias hit
        for raw in raws:
            canon = self.lookup(raw)
            if canon:
                # A header hit only teaches the other header field: a sign-off
                # in the body that didn't match might be someone else entirely.
                # Bare first names ("Rob") are only saved when you confirm them.
                if not proxy and (raw in (sender_name, sender_addr)
                                  or self.lookup(written) == canon):
                    self.learn(canon, sender_addr,
                               sender_name if len(norm_name(sender_name).split()) > 1 else "")
                if norm_name(raw) != norm_name(canon):
                    self.log.append(f"  {raw!r} -> {canon} (alias)")
                return canon

        # 2. Model mapped it to a known musician
        if model_name in self.roster:
            ok = True
            if self.interactive:
                ans = self._ask(f"\n? {shown!r} (from: {hdr or 'n/a'}) looks like "
                                f"{model_name}. Accept? [Y/n] ").lower()
                ok = ans in ("", "y", "yes")
            if ok:
                self.learn(model_name, *raws)
                self.log.append(f"  {shown!r} -> {model_name} (model match"
                                f"{'' if self.interactive else ', NOT confirmed'})")
                return model_name

        # 3. Unknown: offer fuzzy matches
        fallback = model_name if model_name and model_name != "UNKNOWN" else shown
        choices  = self.suggest(raws + [model_name])
        if not self.interactive:
            self.log.append(f"  ⚠ {shown!r} (from: {hdr or 'n/a'}): no known match, kept as "
                            f"{fallback!r}" + (f"; maybe {', '.join(choices)}?" if choices else ""))
            return fallback

        snippet = re.sub(r"\s+", " ", body)[:160]
        print(f"\n? Unknown musician {shown!r}  (from: {hdr or 'n/a'})")
        print(f"  \"{snippet}...\"")
        for i, c in enumerate(choices, 1):
            print(f"   {i}) {c}")
        print(f"   n) new musician    k) keep as {fallback!r}")
        while True:
            ans = self._ask(f"  choice [{'1' if choices else 'n'}]: ").lower() or ("1" if choices else "n")
            if ans.isdigit() and 1 <= int(ans) <= len(choices):
                canon = choices[int(ans) - 1]
                break
            if ans == "n":
                canon = self._ask(f"  canonical First Last [{fallback}]: ") or fallback
                break
            if ans == "k":
                return fallback
        self.learn(canon, *raws)
        self.log.append(f"  {shown!r} -> {canon} (you chose)")
        return canon

def update_musicians_csv(path, new_rows, new_aliases=None):
    """
    Append any (musician, instrument) pairs from new_rows that aren't
    already in musicians.csv. Weight is left blank for new entries.
    new_aliases ({canonical: {alias, ...}}) are merged into the aliases
    column of every row for that musician.
    Creates the file with a header if it doesn't exist.
    """
    existing = set()
    rows     = []
    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row.get("musician","").strip(), row.get("instrument","").strip())
                existing.add(key)
                rows.append(row)
    except FileNotFoundError:
        pass

    added = 0
    for row in new_rows:
        name = row.get("musician","").strip()
        inst = row.get("instrument","").strip()
        # fan-out: guitar rows appear as guitar1 AND guitar2
        insts = ["guitar1","guitar2"] if inst == "guitar" else [inst]
        for i in insts:
            if name and name != "UNKNOWN" and i and (name, i) not in existing:
                rows.append({"musician": name, "instrument": i, "weight": "", "max_songs": ""})
                existing.add((name, i))
                added += 1

    # Preserve any extra columns already in the file (e.g. max_songs)
    all_keys = ["musician", "instrument", "weight", "max_songs", "aliases"]
    for row in rows:
        for k in all_keys:
            row.setdefault(k, "")
        learned = (new_aliases or {}).get(row["musician"].strip())
        if learned:
            current = [a.strip() for a in (row["aliases"] or "").split(",") if a.strip()]
            for a in sorted(learned):
                if a not in current:
                    current.append(a)
            row["aliases"] = ",".join(current)
    rows = sorted(rows, key=lambda r: r.get("musician","").lower())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys)
        writer.writeheader()
        writer.writerows(rows)

    return added

# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Parse jam session preference emails")
    p.add_argument("--emails",     required=True,
                   help="mbox file or plain text file (emails separated by --- lines)")
    p.add_argument("--songs",      default="songs.csv",
                   help="songs.csv (same format as jam_solver.py; default: songs.csv)")
    p.add_argument("--instrument", default=None,
                   choices=sorted(VALID_INSTRUMENTS),
                   help="Default instrument for this email thread (e.g. bass)")
    p.add_argument("--output",     default=None,
                   help="Output CSV (default: parsed_<instrument>.csv or parsed.csv)")
    p.add_argument("--append",     default=None,
                   help="Append confirmed rows to this submissions CSV")
    p.add_argument("--weight",     type=float, default=3,
                   help="Default weight for parsed musicians (default 3)")
    p.add_argument("--musicians",  default="musicians.csv",
                   help="Musician registry to update (default: musicians.csv)")
    p.add_argument("--no-prompt",  action="store_true",
                   help="Don't ask about unknown names; keep them as-is and list them")
    args = p.parse_args()

    if args.output is None:
        suffix      = f"_{args.instrument}" if args.instrument else ""
        args.output = f"parsed{suffix}.csv"

    catalog = load_song_catalog(args.songs)
    print(f"Loaded {len(catalog)} songs from {args.songs}")
    if args.instrument:
        print(f"Default instrument: {args.instrument}")

    emails = load_emails(args.emails)
    print(f"Found {len(emails)} email(s) to parse\n")

    resolver = NameResolver(args.musicians,
                            interactive=False if args.no_prompt else None)

    # Pass 1: API calls (no prompts, so this can run unattended)
    parsed = []
    for i, (sender_name, sender_addr, body) in enumerate(emails, 1):
        hdr   = " ".join(filter(None, [sender_name, f"<{sender_addr}>" if sender_addr else ""]))
        label = f" (from: {hdr})" if hdr else ""
        print(f"Parsing email {i}/{len(emails)}{label}...", end=" ", flush=True)
        entries = parse_email_text(sender_name, sender_addr, body, catalog,
                                   args.instrument, roster=resolver.roster)
        print(f"{[(e.get('musician'), e.get('instrument'), e.get('preferences')) for e in entries]}")
        parsed.append((sender_name, sender_addr, body, entries))

    print_stats()

    # Pass 2: resolve names to canonical musicians (may prompt)
    all_rows             = []
    all_instrument_warns = []
    all_unmatched_warns  = []
    for sender_name, sender_addr, body, entries in parsed:
        for e in entries:
            e["musician"] = resolver.resolve(e, sender_name, sender_addr, body)
            e["_sender"]  = sender_name or sender_addr  # for proxy reporting
        rows, iw, uw = process_entries(entries, args.instrument)
        all_rows.extend(rows)
        all_instrument_warns.extend(iw)
        all_unmatched_warns.extend(uw)

    if resolver.log:
        print("\n── NAME RESOLUTION ─────────────────────────────────────")
        print("\n".join(resolver.log))

    print_preview(all_rows, all_instrument_warns, all_unmatched_warns, args.weight)

    pref_cols = write_csv(args.output, all_rows, args.weight)
    print(f"Parsed rows written to {args.output}")

    added = update_musicians_csv(args.musicians, all_rows, resolver.new_aliases)
    if added:
        print(f"Added {added} new musician/instrument row(s) to {args.musicians} (set weights before solving)")
    else:
        print(f"No new entries for {args.musicians}")
    n_alias = sum(len(v) for v in resolver.new_aliases.values())
    if n_alias:
        print(f"Saved {n_alias} new alias(es) to {args.musicians}:")
        for canon, aliases in sorted(resolver.new_aliases.items()):
            print(f"  {canon}: {', '.join(sorted(aliases))}")

    if args.append:
        answer = input(f"\nAppend {len(all_rows)} row(s) to {args.append}? [y/N] ").strip().lower()
        if answer == "y":
            append_to_submissions(args.append, all_rows, pref_cols, args.weight)
            print(f"Appended to {args.append}")
        else:
            print("Skipped. Edit the output CSV manually and append when ready.")


if __name__ == "__main__":
    main()
