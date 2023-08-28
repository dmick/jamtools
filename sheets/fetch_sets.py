#!v/bin/python3

import argparse
import csv
import itertools
import os.path
import re
import sqlite3

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# svcacct
from google.oauth2 import service_account

import sys
import time

SERVICE_ACCOUNT_FILE = 'serviceaccount.key'
# If modifying these scopes in AppFlow, delete token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

def get_and_retry_on_rate_limit(sheet, sheetid, rng):
    sleeptime = 1
    fail = True
    while fail:
        try:
            result = sheet.values().get(spreadsheetId=sheetid, range=rng).execute()
            fail = False
        except HttpError as err:
            if '429' not in str(err):
                raise(err)
            sleeptime *= 2
            print(f'{sheetid} {rng} ratelimited; pausing for {sleeptime} seconds', file=sys.stderr)
            time.sleep(sleeptime)
            continue

    result = result.get('values', [])

    return result


def get_creds_service_account():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return creds


ALL_SETLISTS_SHEETID = '1hxuvHuYAYcxQlOE4KCaeoiSrgZC95OOk3B2Ciu6LAiM'
FIELDS = {
        'Default': ['SONG', 'ARTIST', 'VOCAL', 'GUITAR 1', 'GUITAR 2', 'BASS', 'DRUMS', 'KEYS',],
        '11/21/2022': ['SONG', 'ARTIST', 'VOCAL', 'KEYS', 'KEYS 2', ('GUITAR', 'GUITAR 1'), 'BASS', 'DRUMS',],
        '1/16/2023': ['SONG', 'ARTIST', 'VOCAL', ('GUITAR 1a', 'GUITAR 1'), ('GUITAR 1b', 'GUITAR 2'), 'BASS', 'DRUMS', 'KEYS',],
}

SYNTHESIZED_FIELDS = ['DATE', 'SONGNUM']
# you might think "set", but these need to stay ordered, and it's simpler to just declare them
ALLFIELDS = SYNTHESIZED_FIELDS + ['SONG', 'ARTIST', 'VOCAL', 'GUITAR 1', 'GUITAR 2', 'BASS', 'DRUMS', 'KEYS', 'KEYS 2',]


def stripws(l):
    return [s.strip() for s in l]

def cleanfields(fields):
    return [f.lower().replace(' ', '')  for f in fields]

def date_to_int(datestr):
    m, d, y = (int(f) for f in datestr.split('/'))
    return f'{y}{m:02d}{d:02d}'

def get_rows(sheetservice, sheetdate, sheetid):

    # may throw HttpError
    colnames = get_and_retry_on_rate_limit(sheetservice, sheetid, 'C1:L1')[0]

    # there was at least one setlist with "GUITAR 2 (Elec)".
    # Strip any parenthesized phrases
    colnames = [re.sub('\(.*\)', '', s) for s in colnames]
    colnames = stripws(colnames)

    # allow columns to be in a different order or have
    # column names we're ignoring
    #
    # rename some fields (noted in tuples in FIELDS
    fields = FIELDS.get(sheetdate, FIELDS['Default'])
    colnums = []
    ofields = SYNTHESIZED_FIELDS.copy()

    for f in fields:
        if isinstance(f, tuple):
            fieldname, fieldnewname = f
            print(f'{sheetdate}: renaming {fieldname} to {fieldnewname}', file=sys.stderr)
        else:
            fieldname, fieldnewname = f, None
        try:
            colnum = colnames.index(fieldname)
        except ValueError:
            print(f'no {fieldname} on {sheetdate}??', file=sys.stderr)
            continue
        colnums.append(colnames.index(fieldname))
        ofields.append(fieldnewname or fieldname)

    ofields = cleanfields(ofields)

    rows = []
    songs = get_and_retry_on_rate_limit(sheetservice, sheetid, 'C3:L')
    for i, s in enumerate(songs):

        # make sentinel "no title, artist, or vocalist"
        if not s or (len(s) >= 2 and not s[0] and not s[1] and not s[2]):
            break

        # ..or Tune to recorded tuning
        if s and s[0].startswith('Tune to'):
            break

        s = stripws(s)
        values = [date_to_int(sheetdate), i+1]
        for colnum in colnums:
            try:
                values.append(s[colnum])
            except IndexError:
                pass

        # do some domain-specific value cleanup
        ovalues = []
        for v in values:
            if isinstance(v, str):
                v = v.replace('XX', '')
            ovalues.append(v)

        # row will have a subset of all the fields in ALLFIELDS
        rows.append(dict(t for t in zip(ofields, ovalues)))

    return rows


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--id', help='Google spreadsheet ID to read and csv-ize')
    parser.add_argument('-d', '--date', help='Date corresponding to ID')
    parser.add_argument('-s', '--start', help='Date to start from "all sheets" list')
    return parser.parse_args()

def main():
    """Shows basic usage of the Sheets API.
    Prints values from a sample spreadsheet.
    """
    args = parse_args()

    # hook up to the Google API
    creds = get_creds_service_account()
    service = build('sheets', 'v4', credentials=creds)
    sheetservice = service.spreadsheets()

    rows = []

    id_and_date_args = int(args.id != None) + int(args.date != None)
    if (id_and_date_args == 1):
        print("Must supply both or neither of --date and --id", file=sys.stderr)
        return 1

    if (id_and_date_args and args.start):
        print("--id/--date and --start are mutually exclusive", file=sys.stderr)
        return 1

    if args.id and args.date:
        rows = (get_rows(sheetservice, args.date, args.id))
    else:
        # get the cross-reference of dates/setlist sheets
        date_and_ids = get_and_retry_on_rate_limit(sheetservice, ALL_SETLISTS_SHEETID, 'A:B')

        output = args.start is None
        for idrow in date_and_ids:
            if len(idrow) != 2:
                break
            sheetdate, sheetid = idrow[0], idrow[1]
            sheetmonth, sheetday, sheetyear = map(int, sheetdate.split('/'))
            startmonth, startday, startyear = map(int, args.start.split('/'))
            sheetdate_int = int(f'{sheetyear}{sheetmonth:02d}{sheetday:02d}')
            startdate_int = int(f'{startyear}{startmonth:02d}{startday:02d}')
            if sheetdate_int > startdate_int:
                output = True
            if output:
                rows += get_rows(sheetservice, sheetdate, sheetid)

    cw = csv.DictWriter(sys.stdout, cleanfields(ALLFIELDS))
    cw.writeheader()
    cw.writerows(rows)


if __name__ == '__main__':
    main()
