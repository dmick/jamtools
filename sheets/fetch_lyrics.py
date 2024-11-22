#!/usr/bin/python3

import csv
import re
import requests
import sys
import urllib.parse

SEPARATOR = f'\n{"=" * 30}\n'

re_subs_artist = [
    # remove any comment
    (r'\(.*\)', ''),
    # common abbreviations/misspellings
    (r'&', 'and'),
    (r'Zep$', 'Zeppelin'),
    (r'GnR', 'Guns n Roses'),
    (r'Elvis$', 'Elvis Presley'),
    (r"in'", 'ing'),
    (r'Bros.', 'Brothers'),
    (r'(.*), The', r'The \1'),
    (r'Morrissette', 'Morissette'),
    (r'NIN', 'Nine Inch Nails'),
    (r'AIC', 'Alice In Chains'),
    (r'RHCP', 'Red Hot Chili Peppers'),
    (r'Paparoach', 'Papa Roach'),
    (r'Bad Co.', 'Bad Company'),
    (r'Lovecats', 'The Lovecats'),
]

re_subs_song = [
    # remove any comment
    (r'\(.*\)', ''),
    # common abbreviations/misspellings
    (r'&', 'and'),
    (r"in'", 'ing'),
    (r'Lovecats', 'The Lovecats'),
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


def change_fetch_and_retry(song, artist):
    # various heuristics to try to cope with special situations
    #
    # 1) try prepending 'The ' to the artist name
    new_artist = 'The ' + artist
    print(f'Looking for {song} {new_artist}', file=sys.stderr)
    if (lyrics := fetch_lyrics(song, new_artist)):
        return lyrics

    # 2) look for '/' in title, try two fetches for two titles
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




def fetch_api_path(path):
    url = f'https://lrclib.net/api/{path}'
    resp = requests.get(url)
    if resp.status_code == 404:
        return None
    j = resp.json()
    if resp.status_code == 400:
        print(f'{j["name"]}: {j["message"]}', file=sys.stderr)
        return None
    return resp


def fetch_lyrics(song, artist):
    quoted_artist = urllib.parse.quote_plus(artist)
    quoted_song = urllib.parse.quote_plus(song)
    resp = fetch_api_path(f'get?artist_name={quoted_artist}&track_name={quoted_song}')

    if resp:
        return resp.json()['plainLyrics']

def main():
    if len(sys.argv) > 1:
        infile = open(sys.argv[1], newline='')
    else:
        infile = sys.stdin
    reader = csv.DictReader(infile)
    for row in reader:
        artist = cleanup('artist', row['artist'])
        song = cleanup('song', row['song'])
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
