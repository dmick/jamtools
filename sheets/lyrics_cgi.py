#!/usr/bin/python3

import csv
from lyrics import fetch_and_retry, SEPARATOR
import os
import sys
import subprocess
from subprocess import PIPE
from urllib.parse import parse_qs

LISTCMD = '/home/dmick/src/sheets/fetch_sets.py -l -d %s'


def run_command(args, data=None):
    # print(f'{args=} {data=}', file=open('/tmp/tmp', 'w'))
    if isinstance(args, str):
        args = args.split(' ')

    p = subprocess.Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    if data:
        out, err = p.communicate(data.encode())
    else:
        out, err = p.communicate()
    # print(f'{out=} {err=}', file=open('/tmp/output', 'w'))
    return p.returncode, out, err


def print_form():
    # define this to avoid having to use {{ everywhere in the f-string 
    style = '''
<style>
  body, form, input, textarea, button {
    font-size: 18px; 
  }
  #setlistlabel, #setlist {
    display: block;
  } 
    
</style>
'''
 
    print(f'''Content-Type: text/html
<html>
<head>
{style}
</head>
<body>
<form method=GET action="{os.environ['SCRIPT_NAME']}">

  <label id="datelabel" for="date">Date (Use Last Call setlist from this date)</label>
  <input type="date" id="date" name="date">
  <button type="button" onclick="document.getElementById('date').value=0">Clear date</button>
  <label for="setlist" id="setlistlabel">Or enter a setlist here (lines of song,artist, Date must be cleared):</label>
  <textarea id="setlist" name="setlist" rows="25" cols="40">One,U2\nTwo Hearts Beat As One,U2</textarea>

  <input type=checkbox id="html" name="html" value="true">
  <label for="html" id="htmllabel">Output HTML</label>
  <button type="submit" id="go">Go</button>

</form>
</body>
</html>''')


HEADER = '<html><body>'
FOOTER = '</body></html>'

SCROLL_SCRIPT = '''
<body onkeydown="scrollfunc(event)">
<script>
function scrollfunc(e) {
        let scrolldist = window.innerHeight / 2;
        if (e.key == "PageDown") {
                window.scrollBy(0, scrolldist);
                e.preventDefault();
        } else if (e.key == "PageUp") {
                window.scrollBy(0, -scrolldist);
                e.preventDefault();
        }
}
</script>'''

CSS = '''
<style>
html {
        scroll-behavior: smooth;
}
p {
        margin: 0px 10px 0px 0px;
        color:white;
        background-color: black;
        font-family: sans-serif;
        font-size: 65px;
        font-weight: 500;
        font-style: sans;
        letter-spacing: 0px;
        text-align: center;
}
</style>'''


def format_lyrics(song, artist, lyrics, html):
    if lyrics:
        lyrics = f'{SEPARATOR}\n{song} - {artist}\n\n{lyrics}'
    else:
        if html:
            print(f'&nbsp\n*** {song} - {artist}: Lyrics not found ***\n&nbsp')
        else:
            print(f'\n*** {song} - {artist}: Lyrics not found ***\n')
        return

    if html:
        for l in lyrics.split('\n'):
            l = l.strip()
            if not len(l):
                l = '&nbsp'
            print(f'<p>{l}</p>')
    else:
        print(f'{lyrics}')


def do_fetch(date, setlist=None, html=False):
    if (date):
        retcode, out, err = run_command(LISTCMD % date)
        if retcode:
            print('Content-Type: text/plain\n')
            print(f'ERROR:{err.decode()}')
            return
        setlist = out.decode()

    if html:
        print('Content-Type: text/html; charset=utf-8\n')
        print(HEADER, CSS, SCROLL_SCRIPT)
    else:
        print('Content-Type: text/plain; charset=utf-8\n')

    if setlist.split('\n')[0].strip() != 'song,artist':
        setlist = 'song,artist\n' + setlist
    setlist = [line.strip() for line in setlist.split('\n')]
    csvreader = csv.DictReader(setlist)

    for row in csvreader:
        lyrics = fetch_and_retry(row['song'], row['artist'])
        format_lyrics(row['song'], row['artist'], lyrics, html)
        # get it incrementally to the client
        sys.stdout.flush()

    if html:
        print(FOOTER)
        sys.stdout.flush()

    return 0


def main():
    qs = os.environ.get('QUERY_STRING', '')
    qd = parse_qs(qs)

    date = qd.get('date')
    setlist = qd.get('setlist')
    html = qd.get('html')
    if date:
        date = date[0]
    if setlist:
        setlist = setlist[0]

    if not date and not setlist:
        print_form()
        return 0

    return do_fetch(date, setlist, html)


if __name__ == "__main__":
    sys.exit(main())
