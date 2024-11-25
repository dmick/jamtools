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


def retry(song, artist):
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
    print(f'Looking for {song} {new_artist}', file=sys.stderr)
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


def main():
    if len(sys.argv) > 1:
        infile = open(sys.argv[1], newline='')
    else:
        infile = sys.stdin
    reader = csv.DictReader(infile)
    for row in reader:
        artist = row['artist']
        song = row['song']
        print(f'Looking for {song} {artist}', file=sys.stderr)
        lyrics = fetch_lyrics(song, artist)
        if lyrics is None:
            lyrics = retry(song, artist)
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
