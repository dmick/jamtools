#!/usr/bin/python3

import csv
from lyrics import fetch_and_retry, SEPARATOR
import sys

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
        lyrics = fetch_and_retry(song, artist)
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
