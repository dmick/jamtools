#!/usr/bin/env python3
import csv
import email
import hashlib
import requests
import sys

def main():

    if len(sys.argv) > 1 and sys.argv[1] == '-d':
        debug = True
    else:
        debug = False

    EXPORTED_CSV='https://docs.google.com/spreadsheets/d/e/2PACX-1vQ-Wh4Xd7n7wuBb0gnOpmoO2GFwTpvXEK0fcXd1dwF8GOrhV7z8vQXjGPKE5Is3UgMNeDOGSqwGmHR2/pub?gid=0&single=true&output=csv'
    musicians = requests.get(EXPORTED_CSV)

    # don't do contents comparison in debug mode
    if not debug:
        try:
            with open('all_musicians.csv', 'rb') as f:
                h = hashlib.sha256()
                h.update(f.read())
                old_checksum = h.digest()
                h = hashlib.sha256()
                h.update(musicians.content)
                new_checksum = h.digest()
                if old_checksum == new_checksum:
                    if debug:
                        print('Musicians list unchanged, exiting') 
                    exit(1)
        except FileNotFoundError as e:
            print('Ignoring', e)
            pass

    with open('all_musicians.csv', 'w') as f:
        f.write(musicians.text)

    all_musicians = csv.reader(musicians.iter_lines(decode_unicode=True))

    out_file = None
    mlistname = ''
    mlist = list()

    for musician in all_musicians:
        if debug:
            print(mlistname, musician)
        if len(musician) < 2:
            continue
        if len(musician[0]):
            if mlistname:
                with open(mlistname + '.txt', 'w') as out_file:
                    print('\n'.join(mlist), file=out_file)
            mlistname = musician[0]
            mlist = list()
        try:
            mlist.append(musician[1])
        except KeyError:
            print(f'Failed to parse ${musician[1]}')
            pass

    with open(mlistname + '.txt', 'w') as out_file:
        print('\n'.join(mlist), file=out_file)

if __name__ == "__main__":
    sys.exit(main())
