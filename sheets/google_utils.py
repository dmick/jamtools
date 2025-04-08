from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
# from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# svcacct
from google.oauth2 import service_account
import os
import functools

DEFAULT_CREDFILE = os.path.expanduser('~/.config/googleserviceaccount.key')
# If modifying these scopes in AppFlow, delete token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

def _get_creds_service_account(credfile):
    creds = service_account.Credentials.from_service_account_file(
        credfile, scopes=SCOPES)
    return creds

@functools.cache
def get_sheetservice(credfile=DEFAULT_CREDFILE):
    # hook up to the Google API
    creds = _get_creds_service_account(credfile)
    service = build('sheets', 'v4', credentials=creds)
    sheetservice = service.spreadsheets()
    return sheetservice



