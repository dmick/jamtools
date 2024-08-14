#!/usr/bin/env python3
import argparse
import csv
import email
import hashlib
import requests
import sys

VENUE_TO_CSVURL = {
    'lcl': 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQ-Wh4Xd7n7wuBb0gnOpmoO2GFwTpvXEK0fcXd1dwF8GOrhV7z8vQXjGPKE5Is3UgMNeDOGSqwGmHR2/pub?gid=0&single=true&output=csv',
    'gl': 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQ-Wh4Xd7n7wuBb0gnOpmoO2GFwTpvXEK0fcXd1dwF8GOrhV7z8vQXjGPKE5Is3UgMNeDOGSqwGmHR2/pub?gid=1132700691&single=true&output=csv',
}

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('-d', '--debug', action='store_true')
    ap.add_argument('-V', '--venue', default='lcl', choices=VENUE_TO_CSVURL.keys())
    return ap.parse_args()

def main():

    args = parse_args()

    venue = args.venue
    musicians = requests.get(VENUE_TO_CSVURL[venue])

    # don't do contents comparison in debug mode
    if not args.debug:
        try:
            with open(f'all_musicians.{venue}.csv', 'rb') as f:
                h = hashlib.sha256()
                h.update(f.read())
                old_checksum = h.digest()
                h = hashlib.sha256()
                h.update(musicians.content)
                new_checksum = h.digest()
                if old_checksum == new_checksum:
                    if args.debug:
                        print('Musicians list unchanged, exiting') 
                    exit(1)
        except FileNotFoundError as e:
            pass

        with open(f'all_musicians.{venue}.csv', 'w') as f:
            f.write(musicians.text)

    all_musicians = csv.reader(musicians.iter_lines(decode_unicode=True))

    out_file = None
    mlistname = ''
    mlist = list()

    for musician in all_musicians:
        if args.debug:
            print('>>>', mlistname, musician)
        if len(musician) < 2:
            continue
        if len(musician[0]):
            if mlistname:
                if args.debug:
                    print(f'\n{mlistname}:\n', '\n'.join(mlist), sep='')
                else:
                    with open(f'{mlistname}.{venue}.txt', 'w') as out_file:
                        print('\n'.join(mlist), file=out_file)
            mlistname = musician[0]
            mlist = list()
        if len(musician) > 2 and 'skip' in musician[2].lower():
            print(f'{mlistname} {musician[1]}: {musician[2]}')
            continue
        try:
            mlist.append(musician[1])
        except KeyError:
            print(f'Failed to parse ${musician[1]}')
            pass

    if args.debug:
        print(f'\n{mlistname}:\n', '\n'.join(mlist), sep='')
    else:
        with open(f'{mlistname}.{venue}.txt', 'w') as out_file:
            print('\n'.join(mlist), file=out_file)

if __name__ == "__main__":
    sys.exit(main())
