import google_utils
import re
import sys
import time
import typing

FIELDS: dict[str, list[str|tuple[str, ...]]] = {
        'Default': ['SONG', 'ARTIST', 'VOCAL', 'GUITAR 1', 'GUITAR 2', 'BASS', 'DRUMS', 'KEYS',],
        '11/21/2022': ['SONG', 'ARTIST', 'VOCAL', 'KEYS', 'KEYS 2', ('GUITAR', 'GUITAR 1'), 'BASS', 'DRUMS',],
        '1/16/2023': ['SONG', 'ARTIST', 'VOCAL', ('GUITAR 1a', 'GUITAR 1'), ('GUITAR 1b', 'GUITAR 2'), 'BASS', 'DRUMS', 'KEYS',],
}

SYNTHESIZED_FIELDS = ['DATE', 'SONGNUM']
# you might think "set", but these need to stay ordered, and it's simpler to just declare them
ALLFIELDS = SYNTHESIZED_FIELDS + ['SONG', 'ARTIST', 'VOCAL', 'GUITAR 1', 'GUITAR 2', 'BASS', 'DRUMS', 'KEYS', 'KEYS 2',]

def stripws(l: list[str]) -> list[str]:
    return [s.strip() for s in l]

def cleanfields(fields: list[str]) -> list[str]:
    return [f.lower().replace(' ', '')  for f in fields]

def date_to_int(datestr: str) -> str:
    m, d, y = (int(f) for f in datestr.split('/'))
    return f'{y}{m:02d}{d:02d}'

def get_and_retry_on_rate_limit(sheet, sheetid: str, rng: str) -> list[list[str]]:
    sleeptime = 1
    fail = True
    while fail:
        try:
            result = sheet.values().get(spreadsheetId=sheetid, range=rng).execute()
            fail = False
        except google_utils.HttpError as err:
            if '429' not in str(err):
                raise(err)
            print(f'{sheetid} {rng} ratelimited; pausing for {sleeptime} seconds', file=sys.stderr)
            time.sleep(sleeptime)
            sleeptime *= 2
            continue

    result = result.get('values', [])

    return result


def get_rows(sheetservice, sheetdate: str, sheetid: str) -> list[str]:

    # may throw HttpError
    colnames: list[str]
    colnames = get_and_retry_on_rate_limit(sheetservice, sheetid, 'C1:L1')[0]

    # there was at least one setlist with "GUITAR 2 (Elec)".
    # Strip any parenthesized phrases
    colnames = [re.sub(r'\(.*\)', '', s) for s in colnames]
    colnames = stripws(colnames)

    # allow columns to be in a different order or have
    # column names we're ignoring
    #
    # rename some fields (noted in tuples in FIELDS
    fields: list[str]
    if typing.TYPE_CHECKING:
        reveal_type(fields)
        reveal_type(FIELDS)
        reveal_type(FIELDS['Default'])

    fields = FIELDS.get(sheetdate, FIELDS['Default'])
    colnums = []
    ofields = SYNTHESIZED_FIELDS.copy()

    for f in fields:
        if isinstance(f, tuple):
            fieldname, fieldnewname = f
            print(f'{sheetdate}: renaming {fieldname} to {fieldnewname}', file=sys.stderr)
        else:
            fieldname, fieldnewname = f, None
        try:
            colnum = colnames.index(fieldname)
        except ValueError:
            print(f'no {fieldname} on {sheetdate}??', file=sys.stderr)
            continue
        colnums.append(colnum)
        ofields.append(fieldnewname or fieldname)

    ofields = cleanfields(ofields)

    rows = []
    songs = get_and_retry_on_rate_limit(sheetservice, sheetid, 'C3:L')
    for i, s in enumerate(songs):

        # make sentinel "no title, artist, or vocalist"
        if (i >= 20 and not s) or (len(s) >= 2 and not s[0] and not s[1] and not s[2]):
            break

        # ..or Tune to recorded tuning
        if s and s[0].startswith('Tune to'):
            break

        s = stripws(s)
        values = [date_to_int(sheetdate), i+1]
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

