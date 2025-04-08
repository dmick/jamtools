#!/usr/bin/python3

from lyrics import do_fetch_setlist
import os
import sys
from urllib.parse import parse_qs
from lyrics_utils import input_form
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
    if date:
        date = date[0]
    if setlist:
        setlist_str = setlist[0]

    if not date and not setlist:
        print_form()
        return 0

    if isinstance(setlist, list):
        setlist_str = '\n'.join(setlist)
    else:
        setlist_str = setlist
    if date:
        setlist_str = ''
        rows = find_set(None, None, None, date)
        for row in rows:
            setlist_str += f'{row["song"]},{row["artist"]}\n'

    print(do_fetch_setlist(setlist_str, html is None))


if __name__ == "__main__":
    sys.exit(main())