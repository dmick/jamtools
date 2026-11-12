# Jam check-in

Page for organizers to tap musicians in as they arrive. It groups people into half-hour arrival
windows (30 min before their first song), shows which songs are playable vs. waiting on someone,
and lets you assign a sub or mark a part "not needed". State is shared between everyone who opens
the page and kept in SQLite, one set of rows per event date.

Open it as `https://yoursite/checkin?date=2026-11-01` (`/checkin/` and `/checkin/checkin.html` work too). With no `date` it uses today's date (from the
browser's clock). The date picker under the title switches to another event date (it reloads the page with `?date=`
set, keeping any `start`/`mins` overrides); "Today" jumps back.

## Files
| File | Where it goes |
|---|---|
| `checkin.html` | `/var/www/jam-checkin/` (static, served by nginx) |
| `app.py`, `schema.sql` | `/opt/jam-checkin/` (small Flask API; nginx cannot talk to SQLite directly) |
| `nginx-checkin.conf` | merge into the site's server block |
| `jam-checkin.service` | `/etc/systemd/system/` |

## Setup
```
sudo apt install python3-flask gunicorn
sudo mkdir -p /opt/jam-checkin /var/www/jam-checkin
sudo cp app.py schema.sql /opt/jam-checkin/
sudo cp checkin.html /var/www/jam-checkin/
sudo cp jam-checkin.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now jam-checkin
# add nginx-checkin.conf to the server block, then:
sudo nginx -t && sudo systemctl reload nginx
```
The database is created on first start at `/var/lib/jam-checkin/checkin.db` (override with `CHECKIN_DB`).
Back it up by copying that file. Check-ins for a past date stay in it; there is nothing to reset.

## Where the setlist comes from
The page fetches `https://lastcalllive.rocks/setlist_full?date=YYYY-MM-DD` and reads the CSV
(`date,songnum,song,artist,vocal,guitar1,guitar2,bass,drums,keys,keys2`). Rows are sorted by `songnum`, and rows whose
`date` doesn't match are ignored. Change the address with `setlistUrl` in the `CFG` block at the top of `checkin.html`.
- A player cell is a name, empty, or `XX` (not needed). A name must be spelled identically everywhere it appears.
- Empty vocal/guitar1/bass/drums counts as an **open spot** and keeps that song from showing as ready until someone is
  assigned. Empty guitar2/keys/keys2 is treated as not needed.
- The CSV has no times. Song N starts at `startTime` + (N-1) × `minutesPerSong` (defaults 18:00 and 7). Override per link
  with `?start=19:30&mins=6`, or edit the defaults in `CFG`.
- Song titles must be unique within one date; subs are stored against `title|role`.
- The setlist is loaded when the page opens; the "Reload setlist" button re-fetches it without losing check-ins.
- **CORS:** if the page is served from `lastcalllive.rocks` this just works. From any other domain the browser blocks the
  request unless the service sends CORS headers; use the commented-out proxy block in `nginx-checkin.conf` instead.

## Notes
- The API has no login. Anyone who can reach `/api/checkin/` can change check-ins, so put it behind the site's
  existing auth or the commented `auth_basic` lines in the nginx config.
- Devices sync by polling every 3 seconds. If two people tap different names at once both are kept; if they
  tap the same name at once, the last tap wins.
- The page loads two fonts from Google Fonts; delete the `<link>` in `checkin.html` to self-host (it falls back to system fonts).
