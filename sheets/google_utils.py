from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# svcacct
from google.oauth2 import service_account
import os
import functools
import config

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

@functools.cache
def _get_creds_service_account(credfile):
    return service_account.Credentials.from_service_account_file(
        credfile, scopes=SCOPES)

@functools.cache
def get_sheetservice(credfile=config.DEFAULT_CREDFILE):
    # hook up to the Google API
    creds = _get_creds_service_account(os.path.expanduser(credfile))
    service = build('sheets', 'v4', credentials=creds)
    return service.spreadsheets()



