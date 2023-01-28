#!/usr/bin/env python3
import csv
import email
import requests
import sys

if len(sys.argv) > 1 and sys.argv[1] == '-d':
    debug = True
else:
    debug = False

EXPORTED_CSV='https://docs.google.com/spreadsheets/d/e/2PACX-1vQ-Wh4Xd7n7wuBb0gnOpmoO2GFwTpvXEK0fcXd1dwF8GOrhV7z8vQXjGPKE5Is3UgMNeDOGSqwGmHR2/pub?gid=0&single=true&output=csv'
musicians = requests.get(EXPORTED_CSV)
print(musicians.iter_lines(decode_unicode=True))

all_musicians = csv.reader(musicians.iter_lines(decode_unicode=True))
out_file = None
mlistname = ''
mlist = list()

for musician in all_musicians:
    print(mlistname, musician)
    if len(musician) == 0:
        continue
    if len(musician[0]):
        if mlistname:
            print(mlistname, mlist)
        mlistname = musician[0]
        mlist = list()
    try:
        mlist.append(email.utils.parseaddr(musician[1])[1])
    except KeyError:
        print(f'Failed to parse ${musician[1]}')
        pass

print(mlistname, mlist)
