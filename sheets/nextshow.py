#!/usr/bin/env python3
"""Fetch a published setlist spreadsheet from Google Sheets and print a styled HTML table.

Usage: nextshow.py SPREADSHEET_ID [--gid GID] > nextshow.html

SPREADSHEET_ID is the ID of the sheet published to the web (File > Share > Publish
to web), i.e. the 2PACX-... part of the published URL.  Without --gid you get the
first sheet; --gid selects a tab.  A local CSV file path may be given instead of an ID.
"""
import argparse, csv, html, io, os, re, sys, urllib.error, urllib.parse, urllib.request

def csv_url(sheet_id, gid=None):
    sheet_id = urllib.parse.quote(sheet_id, safe="")
    # Published-to-web IDs (2PACX-...) live under /d/e/; regular file IDs under /d/.
    base = "https://docs.google.com/spreadsheets/d/" + ("e/" if sheet_id.startswith("2PACX") else "")
    q = "output=csv" + (f"&gid={urllib.parse.quote(gid)}&single=true" if gid else "")
    return f"{base}{sheet_id}/pub?{q}"

CSS = """
body { font-family: sans-serif; margin: 1.5em; }
h1 { margin-bottom: .2em; }
h2 { margin-top: 0; font-weight: normal; color: #555; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: 4px 10px; text-align: left; }
th { font-weight: bold; background: #fff; border-bottom: 2px solid #888; }
tbody tr:nth-child(odd)  td { background: #ffffff; }
tbody tr:nth-child(even) td { background: #f0f0f0; }
tbody td.empty { background: #ffd6d6 !important; }
"""

def load(src, gid=None):
    if os.path.isfile(src):
        with open(src, encoding="utf-8") as f:
            return f.read()
    try:
        with urllib.request.urlopen(csv_url(src, gid), timeout=30) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        sys.exit(f"nextshow.py: Google returned HTTP {e.code} for ID {src!r}; "
                 "is the sheet published to the web?")
    except urllib.error.URLError as e:
        sys.exit(f"nextshow.py: fetch failed: {e.reason}")

def cell(text):
    t = html.escape(text.strip())
    return re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', t)

def main():
    ap = argparse.ArgumentParser(description="Render a published setlist sheet as styled HTML.")
    ap.add_argument("spreadsheet_id", help="published spreadsheet ID (2PACX-...) or a local CSV file")
    ap.add_argument("--gid", help="sheet tab gid (default: first sheet)")
    args = ap.parse_args()
    rows = list(csv.reader(io.StringIO(load(args.spreadsheet_id, args.gid))))
    rows = [r for r in rows if any(c.strip() for c in r)]
    header = rows[0]
    # columns with a header name (first, unnamed column is the song number);
    # content in any other (headerless) column is folded into NOTES
    keep = [i for i, h in enumerate(header) if h.strip() or i == 0]
    names = [header[0].strip() or "#"] + [header[i].strip() for i in keep[1:]]
    notes = names.index("NOTES") if "NOTES" in names else len(names) - 1
    width = max(len(r) for r in rows)

    banner, data = "", []
    for r in rows[1:]:
        r = r + [""] * (width - len(r))
        if not r[0].strip() and not r[1].strip():          # banner row, e.g. event name
            banner = next((c.strip() for c in r if c.strip()), "")
        elif r[0].strip().isdigit():                       # song rows; skips footer junk
            extra = [r[i].strip() for i in range(width) if i not in keep and r[i].strip()]
            r = [r[i] for i in keep]
            if extra:
                r[notes] = " — ".join(x for x in [r[notes].strip()] + extra if x)
            data.append(r)

    out = ["<!DOCTYPE html>", '<html><head><meta charset="utf-8">',
           "<title>Setlist</title>", f"<style>{CSS}</style></head><body>"]
    if banner:
        out.append(f"<h1>{html.escape(banner)}</h1>")
    out.append("<table>\n<thead><tr>" + "".join(f"<th>{html.escape(n)}</th>" for n in names)
               + "</tr></thead>\n<tbody>")
    for r in data:
        out.append("<tr>" + "".join(
            f'<td class="empty"></td>' if not c.strip() else f"<td>{cell(c)}</td>"
            for c in r) + "</tr>")
    out.append("</tbody></table></body></html>")
    print("\n".join(out))

main()
