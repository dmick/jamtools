import datetime
import google_utils
import re
import sys
import time
import typing
import config
import logging

log = logging.getLogger(__name__)

FIELDS: dict[str, list[str|tuple[str, ...]]] = {
        'Default': ['SONG', 'ARTIST', 'VOCAL', 'GUITAR 1', 'GUITAR 2', 'BASS', 'DRUMS', 'KEYS',],
        '11/21/2022': ['SONG', 'ARTIST', 'VOCAL', 'KEYS', 'KEYS 2', ('GUITAR', 'GUITAR 1'), 'BASS', 'DRUMS',],
        '1/16/2023': ['SONG', 'ARTIST', 'VOCAL', ('GUITAR 1a', 'GUITAR 1'), ('GUITAR 1b', 'GUITAR 2'), 'BASS', 'DRUMS', 'KEYS',],
}

SYNTHESIZED_FIELDS:list[str] = ['DATE', 'SONGNUM']
# you might think "set", but these need to stay ordered, and it's simpler to just declare them
ALLFIELDS:list[str] = SYNTHESIZED_FIELDS + ['SONG', 'ARTIST', 'VOCAL', 'GUITAR 1', 'GUITAR 2', 'BASS', 'DRUMS', 'KEYS', 'KEYS 2',]

def stripws(l: list[str]) -> list[str]:
    return [s.strip() for s in l]

def cleanfields(fields: list[str]) -> list[str]:
    return [f.lower().replace(' ', '')  for f in fields]

def date_to_int(d:str) -> int:
    if '-' in d:
        # YYYY-MM-DD, like HTML input type=date
        year, month, day = map(int, d.split('-'))
        if year <= 12:
            month, day, year = year, month, day
    else:
        # MM/DD/YYYY
        month, day, year = map(int, d.split('/'))
    date_int = int(f'{year}{month:02d}{day:02d}')
    return date_int


def get_and_retry_on_rate_limit(sheetid: str, rng: str) -> list[list[str]]:
    sheetservice = google_utils.get_sheetservice()
    sleeptime = 1
    fail = True
    while fail:
        try:
            result = sheetservice.values().get(spreadsheetId=sheetid, range=rng).execute()
            fail = False
        except google_utils.HttpError as err:
            if '429' not in str(err):
                raise(err)
            log.info(f'{sheetid} {rng} ratelimited; pausing for {sleeptime} seconds')
            time.sleep(sleeptime)
            sleeptime *= 2
            continue

    result = result.get('values', []) # type: ignore

    return result


def end_of_songs(songs, i, form):
    # scanning through songs, we're at row i, and want to know
    # if it appears to be the last song.

    def empty(l):
        # defined: list is empty if all elements are empty or whitespace
        ltext = ''.join([i.strip() for i in l])
        return not ltext

    s = songs[i]
    if i+1 < len(songs):
        ns = songs[i+1]
    else:
        ns = []

    if form == 1:
        # no song is the sentinel:
        if not s[0]:
            return True

    elif form == 2:
        # at least at row 20, and row is empty, and the following row is empty too
        if (i >= 20 and empty(s) and empty(ns)):
            return True

        # row is empty in song/artist, and so is next row
        if empty(s[0:2]) and empty(ns[0:2]):
            return True

        # ..or Tune to recorded tuning
        if s and s[0].startswith('Tune to'):
            return True

    return False


def get_rows(sheetdate: str, sheetid: str) -> list[dict[str, str]]:

    # may throw HttpError
    colnames: list[str]

    # deduce form of sheet.  Two variants of header:
    # 1: song | artist | others (freeform setlist)
    # 2: blank | number | time | song | artist | vocal | etc. (Monday Live setlist)
    # look at col A; if it has no header and has a digit 1 in the first nonblank row,
    # assume form 2

    form_to_range: dict[int, list[str, str]] = {
        1: ['A1:B1', 'A3:B',],
        2: ['C1:L1', 'C3:L',],
    }

    colA = get_and_retry_on_rate_limit(sheetid, 'A1:A10')
    if not colA[0] and not colA[1] and colA[2] == ['1']:
        form = 2
    else:
        form = 1

    colrange, datarange = form_to_range[form]

    colnames = get_and_retry_on_rate_limit(sheetid, colrange)[0]

    # there was at least one setlist with "GUITAR 2 (Elec)".
    # Strip any parenthesized phrases
    colnames = [re.sub(r'\(.*\)', '', s) for s in colnames]
    colnames = stripws(colnames)

    # allow columns to be in a different order or have
    # column names we're ignoring
    #
    # rename some fields (noted in tuples in FIELDS
    fields: list[str|tuple[str, ...]]
    fields = FIELDS.get(sheetdate, FIELDS['Default'])
    colnums: list[int] = []
    ofields = SYNTHESIZED_FIELDS.copy()

    for f in fields:
        if isinstance(f, tuple):
            fieldname, fieldnewname = f
            log.info(f'{sheetdate}: renaming {fieldname} to {fieldnewname}')
        else:
            fieldname, fieldnewname = f, None
        try:
            colnum = colnames.index(fieldname)
        except ValueError:
            log.info(f'no {fieldname} on {sheetdate}??')
            continue
        colnums.append(colnum)
        ofields.append(fieldnewname or fieldname)

    ofields = cleanfields(ofields)

    rows = []

    songs = get_and_retry_on_rate_limit(sheetid, datarange)
    for i, s in enumerate(songs):
        if end_of_songs(songs, i, form):
            break
        s = stripws(s)
        values:list[str] = [str(date_to_int(sheetdate)), str(i+1)]
        for colnum in colnums:
            try:
                values.append(s[colnum])
            except IndexError:
                pass

        # do some domain-specific value cleanup
        ovalues = []
        for v in values:
            if isinstance(v, str):
                v = v.replace('XX', '')
            ovalues.append(v)

        # row will have a subset of all the fields in ALLFIELDS
        rows.append(dict(t for t in zip(ofields, ovalues)))

    return rows

def find_set(sheetid, start, date):
    now = datetime.datetime.now(tz=datetime.
        timezone(-datetime.timedelta(hours=6)))
    if sheetid:
        date = date or now.strftime("%Y-%m-%d")
        rows = (get_rows(date, sheetid))
    else:
        # get the cross-reference of dates/setlist sheets
        date_and_ids = get_and_retry_on_rate_limit(config.ALL_SETLISTS_SHEETID, 'A:B')

        startdate_int = -2
        date_int = -2
        if start:
            startdate_int = date_to_int(start)
        if date:
            date_int = date_to_int(date)
        today_int = date_to_int(now.strftime("%Y-%m-%d"))

        rows = []

        log.info(f'{date_int=}')
        for idrow in date_and_ids:
            if len(idrow) == 0:
                break

            # if date, look for date == args.date, and return that if found
            # if start, don't output until date is after args.start
            # if we're here, we didn't have both id and args.date

            output = False
            sheetdate, sheetid = idrow
            sheetdate_int = date_to_int(sheetdate)
            # just return it once found
            if date:
                if (sheetdate_int == date_int):
                    return get_rows(sheetdate, sheetid)
                continue

            if start:
                output = (sheetdate_int >= startdate_int) and (sheetdate_int <= today_int)
            else:
                if sheetdate_int <= today_int:
                    output = True

            if output:
                rows += get_rows(sheetdate, sheetid)

    return rows
