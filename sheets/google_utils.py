from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# svcacct
from google.oauth2 import service_account

SERVICE_ACCOUNT_FILE = '/home/dmick/src/sheets/serviceaccount.key'
# If modifying these scopes in AppFlow, delete token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

def get_creds_service_account():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return creds

def get_sheetservice():
    # hook up to the Google API
    creds = get_creds_service_account()
    service = build('sheets', 'v4', credentials=creds)
    sheetservice = service.spreadsheets()
    return sheetservice



