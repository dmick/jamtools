#!/usr/bin/python3

import os
import sys
from urllib.parse import parse_qs
from lyrics_utils import input_form, do_fetch_setlist, format_setlist

from set_utils import find_set

def print_form(action=os.environ['SCRIPT_NAME']):
    print(f'''Content-type: text/html
    {input_form(action)}
    ''')

def main():
    qs = os.environ.get('QUERY_STRING', '')
    qd = parse_qs(qs)

    date = qd.get('date')
    setlist = qd.get('setlist')
    html = qd.get('html')
    rows = list()

    if date:
        date = date[0]

    if setlist:
        setlist_str = setlist[0]
        songs = setlist_str.split('\n')
        for s in songs:
            song, artist = s.split(',')
            rows.append() = {'song':song, 'artist':artist, 'lyrics':None}

    if not date and not setlist:
        print_form()
        return 0

    if date:
        rows = find_set(None, None, None, date)

    lyrics = do_fetch_setlist(setlist_str, html is None)
    print(format_setlist(lyrics, html is not None))


if __name__ == "__main__":
    sys.exit(main())