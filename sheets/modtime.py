#!v/bin/python3

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# svcacct
from google.oauth2 import service_account

import sys
import time

SERVICE_ACCOUNT_FILE = 'serviceaccount.key'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def get_creds_service_account():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return creds


def main():
    creds = get_creds_service_account()

    try:
        service = build('drive', 'v3', credentials=creds)

        files = service.files()
        resp = files.list(q='name="All musicians"', fields='files(id,name,modifiedTime)').execute()
        f = resp['files'][0]
        print(f"{f['name']} last modified {f['modifiedTime']}")

    except HttpError as err:
        print(err)


if __name__ == '__main__':
    main()
