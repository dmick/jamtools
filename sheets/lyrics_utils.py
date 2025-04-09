import csv
import re
import httpx
import sys
import time
import urllib.parse
from subprocess import Popen, PIPE
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Tuple

SEPARATOR = f'\n{"=" * 30}\n'


re_subs_artist = [
    # remove any comment
    (r'\(.*\)', ''),
    # common abbreviations/misspellings
    (r'&', 'and'),
    (r'Zep$', 'Zeppelin'),
    (r'GnR', 'Guns n Roses'),
    (r'Elvis$', 'Elvis Presley'),
    (r'Bros.', 'Brothers'),
    (r'(.*), The', r'The \1'),
    (r'Morrissette', 'Morissette'),
    (r'NIN', 'Nine Inch Nails'),
    (r'AIC', 'Alice In Chains'),
    (r'RHCP', 'Red Hot Chili Peppers'),
    (r'STP', 'Stone Temple Pilots'),
    (r'Paparoach', 'Papa Roach'),
    (r'Bad Co.', 'Bad Company'),
    (r'Wonderstuff', 'The Wonder Stuff'),
    (r'Three Eleven', '311'),
    (r'Jesus and the Mary Chain', 'The Jesus and Mary Chain'),
    (r'CandC Dance', 'C+C Music'),
    (r'Three Doors Down', '3 Doors Down'),
]

re_subs_song = [
    # remove any comment
    (r'\(.*\)', ''),
    # common abbreviations/misspellings
    (r'&', 'and'),
    # (r"([^Aa])in'", '\1ing'), questionable, should only be for a retry
    ('Lovecats', 'The Lovecats'),
    ('Mean Streets', 'Mean Street'),
    ('DOA', 'D.O.A.'),
    ('H2H', 'Highway To Hell'),
    ('Pushing Forward Back', 'Pushin Forward Back'),
    ('Stickshifts and Safety Belts', 'Stickshifts and Safetybelts'),
    ('Arrested for Driving', 'Arrested for Driving While Blind'),
    ('Can\'t Stand', 'Can\'t Stand Losing You'),
    ('Dead An Bloated', 'Dead And Bloated'),
]

# if you need to customize the api path, map title/artist to a tuple. Tuple
# can be "(id, <id>)" to override the normal search with api/get/<id>, or
#  ('field', 'value') to add &field=value to the normal api get
extra_params = [
    (r'Hard [Tt]o Handle', 'Black Crowes', ('id', 15855138)),
]


def cleanup(which, s):
    if which == 'song':
        re_subs = re_subs_song
    elif which == 'artist':
        re_subs = re_subs_artist
    else:
        return s

    for search, replace in re_subs:
        s = re.sub(search, replace, s)
    return s


def fetch_and_retry(song, artist):
    # first add any extra params if we know it'll help isolate the
    # particular song we want

    extra = None
    for extra_entry in extra_params:
        if re.match(extra_entry[0], song) and re.match(extra_entry[1], artist):
            extra = extra_entry[2]

    if lyrics := fetch_lyrics(song, artist, extra):
        return lyrics

    #
    # various heuristics to try to cope with special situations
    #
    # 0) try cleanup(song) and try adding cleanup(artist)
    song = cleanup('song', song)
    if lyrics := fetch_lyrics(song, artist):
        return lyrics

    artist = cleanup('artist', artist)
    if lyrics := fetch_lyrics(song, artist):
        return lyrics

    # 1) try prepending 'The ' to the artist name
    new_artist = 'The ' + artist
    if (lyrics := fetch_lyrics(song, new_artist)):
        return lyrics

    # 2) try removing 'The ' from the song
    new_song = re.sub('^The ', '', song)
    if (lyrics := fetch_lyrics(new_song, artist)):
        return lyrics

    # 3) look for '/' in title, try two fetches for two titles
    found = 0
    if '/' in song:
        combined_lyrics = list()
        songs = song.split('/')
        for s in songs:
            if (lyrics := fetch_lyrics(s, artist)):
                found += 1
                combined_lyrics.append(lyrics)
        if found and found == len(songs):
            return SEPARATOR.join(combined_lyrics)

    # 4) look for '/' in artist, try each artist
    if '/' in artist:
        artists = artist.split('/')
        for a in artists:
            if (lyrics := fetch_lyrics(song, a)):
                return lyrics

    # 5) look for 'and' in artist, truncate before (for songs like
    # 'artist and guest artist' (rather than bands like Sam and Dave))
    if 'and' in artist:
        new_artist = re.sub(r'(.*) and.*', r'\1', artist)
        if lyrics := fetch_lyrics(song, new_artist):
            return lyrics

    # 6) look for '/' in both title and artist, try splitting both
    found = 0
    if '/' in song and '/' in artist:
        combined_lyrics = list()
        songs = song.split('/')
        artists = artist.split('/')
        if len(songs) == len(artists):
            for s, a in zip(songs, artists):
                if (lyrics := fetch_lyrics(s, a)):
                    found += 1
                    combined_lyrics.append(lyrics)
            if found and found == len(songs):
                return SEPARATOR.join(combined_lyrics)

    # 7) long shot: try api/search for the song string, and look for a
    # matching artist in the returned JSON, like for "(The Angels Wanna
    # Wear My) Red Shoes", which will end up having been cleaned up to
    # "Red Shoes".  Also, when matching artist, first look for exact
    # match, then look for 'artist' as substring of db artist.
    lyrics = search_song(song, artist)

    return lyrics


def fetch_override(song, artist, extra=None):
    '''
    resp = httpx.get(f'https://lastcalllive.rocks/lyrics-override/{artist}-{song}.txt')
    if resp.status_code != 200:
        return None
    return resp.text
    '''
    try:
        f = open(f'/var/www/html/lyrics-override/{artist}-{song}.txt', 'r')
        return f.read()
    except FileNotFoundError:
        pass


def fetch_api_path(path):
    url = f'https://lrclib.net/api/{path}'
    resp = httpx.get(url)
    if resp.status_code == 404:
        return None
    j = resp.json()
    if resp.status_code == 400:
        print(f'{j["name"]}: {j["message"]}', file=sys.stderr)
        return None
    return resp


def fetch_lyrics(song, artist, extra=None):
    if not song or not artist:
        return f'<incomplete request {song=} {artist=}>'

    # look for local override for custom/unfindable lyrics
    resp = fetch_override(song, artist, extra)
    if resp:
        return resp

    quoted_artist = urllib.parse.quote_plus(artist)
    quoted_song = urllib.parse.quote_plus(song)

    # look for local override for custom/unfindable lyrics
    resp = fetch_override(song, artist, extra)
    if resp:
        return resp

    api_path = f'get?artist_name={quoted_artist}&track_name={quoted_song}'
    if extra:
        if extra[0] == 'id':
            api_path=f'get/{extra[1]}'
        else:
            api_path += f'&{extra[0]}={urllib.parse.quote_plus(extra[1])}'
    resp = fetch_api_path(api_path)

    if resp:
        j = resp.json()
        if j['instrumental']:
            return '<Instrumental>'
        return j['plainLyrics']


def search_song(song, artist):
    quoted_search = urllib.parse.quote_plus(song)
    resp = fetch_api_path(f'search?track_name={quoted_search}')
    if resp:
        matches = resp.json()
        for m in matches:
            if m['artistName'] == artist:
                return m['plainLyrics']
        for m in matches:
            if artist in m['artistName']:
                return m['plainLyrics']
    return None


""" def run_command(args, data=None):
    # print(f'{args=} {data=}', file=open('/tmp/tmp', 'w'))
    if isinstance(args, str):
        args = args.split(' ')

    p = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    if data:
        out, err = p.communicate(data.encode())
    else:
        out, err = p.communicate()
    # print(f'{out=} {err=}', file=open('/tmp/output', 'w'))
    return p.returncode, out, err

LISTCMD = '/home/dmick/src/git/jamtools/sheets/fetch_sets.py -l -d %s' """

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
HEADER = '<html><body>'
FOOTER = '</body></html>'


def format_lyrics(song, artist, lyrics, html):
    if lyrics:
        lyrics = f'{SEPARATOR}\n{song} - {artist}\n\n{lyrics}'
    else:
        lyrics = f'{SEPARATOR}\n*** {song} - {artist}: Lyrics not found ***'

    if html:
        html_lyrics = ''
        for l in lyrics.split('\n'):
            l = l.strip()
            if not len(l):
                l = '&nbsp'
            html_lyrics += f'<p>{l}</p>\n'
        return html_lyrics
    else:
        return lyrics


def do_fetch_seq_setlist(rows, html):
    if html:
        retstr = f'{HEADER}\n{CSS}\n{SCROLL_SCRIPT}\n'
    else:
        retstr = ''

    for row in rows:
        song = row['song']
        artist = row['artist']
        start = time.time()
        lyr = fetch_and_retry(song, artist)
        print(f'*** {time.time() - start} to fetch {song},{artist}')
        retstr += format_lyrics(song, artist, lyr, html) + '\n'

    if html:
        retstr += f'{FOOTER}\n'

    return retstr


def do_fetch_setlist(setlist:str|list[str], html=False):

    retstr = ''
    if html:
        retstr = f'{HEADER}\n{CSS}\n{SCROLL_SCRIPT}\n'

    if isinstance(setlist, list):
        songs = setlist
    else:
        if setlist.split('\n')[0].strip() != 'song,artist':
            setlist = 'song,artist\n' + setlist
        set_list = [line.strip() for line in setlist.split('\n')]
        csvreader = csv.DictReader(set_list)
        songs = [(r['song'], r['artist']) for r in csvreader]

    print(f'{songs=}')
    futures = []
    with ThreadPoolExecutor(max_workers=20) as e:
        for song, artist in songs:
            print(f'*** Starting search for {song} {artist}')
            futures.append(e.submit(do_fetch_song, song, artist, html))

    failures: list[str] = []
    for f in futures:
        found, song, artist, res = f.result()
        print(f'result: {res}')
        retstr += res + '\n'
        if not found:
            failures.append(f'{song} - {artist}')

    if html:
        retstr += f'{FOOTER}\n'

    return failures, retstr

def do_fetch_song(song:str, artist:str, html:bool) -> Tuple[bool, str, str, str]:
    # returns song, artist so that when used concurrently one can tie
    # the return value to the request
    lyrics = fetch_and_retry(song, artist)
    return bool(lyrics), song, artist, format_lyrics(song, artist, lyrics, html)


def input_form(action, dateonly=False):
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

    lyrics_form_guts = '''
<label id="datelabel" for="date">Date</label>
<input type="date" id="date" name="date">
<button type="button" onclick="document.getElementById('date').value=0">Clear date</button>
<p></p>
<label for="setlist" id="setlistlabel">-- OR -- enter a setlist here (lines of song,artist)<br>Date must be cleared:</label>
<p></p>
<textarea id="setlist" name="setlist" rows="5" cols="40">One,U2\nTwo Hearts Beat As One,U2</textarea>
<p></p>
<input type=checkbox id="html" name="html" value="true">
<label for="html" id="htmllabel">Output HTML (run show from browser)</label>
<p></p>
<button type="submit" id="go">Go</button>
'''

    dateonly_form_guts = '''
  <label id="datelabel" for="date">Date</label>
  <input type="date" id="date" name="date">
  <p></p>
  <button type="submit" id="go">Go</button>
'''

    if dateonly:
        guts = dateonly_form_guts
    else:
        guts = lyrics_form_guts

    formstr = (f'''
<html>
<head>
{style}
</head>
<body>
<form method=GET action="{action}">

  {guts}

</form>
</body>
</html>''')
    return formstr
