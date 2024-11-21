#!/usr/bin/python3

import csv
import re
import requests
import sys
import urllib.parse

SEPARATOR = f'\n{"=" * 30}\n'

re_subs = [
    # remove any comment
    (r'\(.*\)', ''),
    (r'&', 'and'),
    # common abbreviations
    (r'Zep$', 'Zeppelin'),
    (r'GnR', 'Guns n Roses'),
    (r'Elvis$', 'Elvis Presley'),
    (r"in'", 'ing'),
    (r'Bros.', 'Brothers'),
    (r'(\S+), The', r'The \1'),
    (r'Morrissette', 'Morissette'),
    (r'NIN', 'Nine Inch Nails'),
    (r'RHCP', 'Red Hot Chili Peppers'),
]


def cleanup(s):
    for search, replace in re_subs:
        s = re.sub(search, replace, s)
    return s


def change_fetch_and_retry(song, artist):
    # various heuristics to try to cope with special situations
    #
    # 1) try prepending 'The ' to the artist name
    new_artist = 'The ' + artist
    print(f'Looking for {song} {new_artist}', file=sys.stderr)
    if (lyrics := fetch_lyrics(song, new_artist)):
        return lyrics

    # 2) look for '/' in title, try two fetches for two titles
    found_all = True
    if '/' in song:
        combined_lyrics = list()
        songs = song.split('/')
        for s in songs:
            if (lyrics := fetch_lyrics(s, artist)) is None:
                found_all = False
            else:
                combined_lyrics.append(lyrics)
    if found_all:
        return SEPARATOR.join(combined_lyrics)


def fetch_lyrics(song, artist):
    quoted_artist = urllib.parse.quote_plus(artist)
    quoted_song = urllib.parse.quote_plus(song)
    url = f'https://lrclib.net/api/get?artist_name={quoted_artist}&track_name={quoted_song}'
    resp = requests.get(url)
    if resp.status_code == 404:
        return None
    j = resp.json()
    if resp.status_code == 400:
        print(f'{j["name"]}: {j["message"]}', file=sys.stderr)
        return ''
    return j['plainLyrics']

def main():
    if len(sys.argv) > 1:
        infile = open(sys.argv[1], newline='')
    else:
        infile = sys.stdin
    print(f'{infile=}', file=sys.stderr)
    reader = csv.DictReader(infile)
    for row in reader:
        artist = cleanup(row['artist'])
        song = cleanup(row['song'])
        print(f'Looking for {song} {artist}', file=sys.stderr)
        lyrics = fetch_lyrics(song, artist)
        if lyrics is None:
            lyrics = change_fetch_and_retry(song, artist)
        if lyrics is None:
            print()
            print(f'*** {song} - {artist}: Lyrics not found ***')
            print()
            continue

        print(SEPARATOR)
        print(f'{song} - {artist}')
        print()
        print(lyrics)
        print()


if __name__ == "__main__":
    sys.exit(main())
