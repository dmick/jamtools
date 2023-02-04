#!v/bin/python3

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


def get_creds_app_flow():
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds


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

def main():
    """Shows basic usage of the Sheets API.
    Prints values from a sample spreadsheet.
    """
    # creds = get_creds_app_flow()
    creds = get_creds_service_account()

    try:
        service = build('sheets', 'v4', credentials=creds)
        sheet = service.spreadsheets()

        # get the cross-reference of dates/setlist sheets
        date_and_ids = get_and_retry_on_rate_limit(sheet, ALL_SETLISTS_SHEETID, 'A:B')

        # output, all dates/songs
        rows = []

        for idrow in date_and_ids:
            sheetdate, sheetid = idrow[0], idrow[1]
            colnames = get_and_retry_on_rate_limit(sheet, sheetid, 'C1:L1')[0]
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

            songs = get_and_retry_on_rate_limit(sheet, sheetid, 'C3:L')
            for i, s in enumerate(songs):
                # dump blank lines or lines with nothing in the first column (song)
                if not s or not s[0]:
                    continue
                if 'Tune to recorded tuning' in s:
                    break
                s = stripws(s)
                values = [sheetdate, i+1]
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

        cw = csv.DictWriter(sys.stdout, cleanfields(ALLFIELDS))
        cw.writeheader()
        cw.writerows(rows)

    except HttpError as err:
        print(err)


if __name__ == '__main__':
    main()
