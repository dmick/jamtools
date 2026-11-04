-- One row per person checked in for an event date.
CREATE TABLE IF NOT EXISTS checkins (
  event_date TEXT NOT NULL,                       -- YYYY-MM-DD
  name       TEXT NOT NULL,
  checked_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (event_date, name)
);

-- Substitutions: who is covering a slot, or "XX" meaning the slot isn't needed.
CREATE TABLE IF NOT EXISTS subs (
  event_date TEXT NOT NULL,
  slot_key   TEXT NOT NULL,                       -- "<song title>|<role>", e.g. "Goo Goo Muck|vox"
  name       TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (event_date, slot_key)
);
