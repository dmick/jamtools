#!v/bin/python3

import csv
import json
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
            print(f'\n{sheetid} {rng} failed w/ratelimit, pausing for {sleeptime} seconds', file=sys.stderr)
            time.sleep(sleeptime)
            continue

    result = result.get('values', [])

    return result

def cleanup(l):
    # trim whitespace
    for i, s in enumerate(l):
        s = s.strip()
        l[i] = s


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
        'Default': [
            'SONG',
            'ARTIST',
            'VOCAL',
            'GUITAR 1',
            'GUITAR 2',
            'BASS',
            'DRUMS',
            'KEYS'
        ],
        '11/21/2022': [
            'SONG',
            'ARTIST',
            'VOCAL',
            'KEYS',
            'KEYS 2',
            'GUITAR',
            'BASS',
            'DRUMS'],
}


def main():
    """Shows basic usage of the Sheets API.
    Prints values from a sample spreadsheet.
    """
    # creds = get_creds_app_flow()
    creds = get_creds_service_account()

    do_json = False

    try:
        service = build('sheets', 'v4', credentials=creds)

        sheet = service.spreadsheets()

        # get the cross-reference of dates/setlist sheets
        date_and_ids = get_and_retry_on_rate_limit(sheet, ALL_SETLISTS_SHEETID, 'A:B')

        rows = []
        # for row in date_and_ids:
        for row in date_and_ids[-7:]:
            sheetdate, sheetid = row[0], row[1]
            colnames = get_and_retry_on_rate_limit(sheet, sheetid, 'C1:K1')[0]
            # there was at least one setlist with "GUITAR 2 (Elec)".
            # Strip any parenthesized phrases
            colnames = [re.sub('\(.*\)', '', s) for s in colnames]
            cleanup(colnames)

            fields = FIELDS.get(sheetdate, FIELDS['Default']).copy()
            colnums = []
            for colname in fields:
                try:
                    colnum = colnames.index(colname)
                except ValueError:
                    print(f'no {colname} on {sheetdate}', file=sys.stderr)
                    continue
                colnums.append(colnames.index(colname))
            songs = get_and_retry_on_rate_limit(sheet, sheetid, 'C3:K')
            for i, s in enumerate(songs):
                if not s:
                    continue
                if 'Tune to recorded tuning' in s:
                    break
                cleanup(s)
                cols = [i, sheetdate]
                for index in colnums:
                    try:
                        cols.append(s[index])
                    except IndexError:
                    
                        pass
                dbfields = ['songnum', 'date'] + \
                    [f.lower().replace(' ', '')  for f in fields]
                
                if need_quoting:
                    # do some domain-specific cleanup
                    # quote 'em all: double any embedded single-quotes, then 
                    # wrap in single-quotes
                    dbvalues = []
                    for v in cols:
                        if isinstance(v, str):
                            v = v.replace("'","''")
                            v = v.replace('XX', '')
                            v = f"'{v}'"
                        dbvalues.append(v)

                rows.append(dict(t for t in zip(dbfields, dbvalues)))

        if do_json:
            print(json.dumps(rows))

    except HttpError as err:
        print(err)


if __name__ == '__main__':
    main()
