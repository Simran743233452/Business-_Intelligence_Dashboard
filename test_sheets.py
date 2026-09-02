from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# Google Sheet details
SPREADSHEET_ID = "1bIifUY2LUi5C6is7ZJNzr-F_ov89_ViAdN-RhqoaBFQ"
SHEET_NAME = "Monitoring"

# Google Sheets permission
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# Load the service account credentials
credentials = Credentials.from_service_account_file(
    "credentials.json",
    scopes=SCOPES
)

# Create Google Sheets API connection
service = build(
    "sheets",
    "v4",
    credentials=credentials
)

# Test row
test_row = [
    "2026-09-01",
    "TEST",
    "Test Site",
    "Testing Google Sheets connection",
    1,
    0,
    "Connection test",
    "No action required",
    "TEST",
    "Added by Python"
]

# Add row to Monitoring sheet
result = service.spreadsheets().values().append(
    spreadsheetId=SPREADSHEET_ID,
    range=f"{SHEET_NAME}!A:J",
    valueInputOption="USER_ENTERED",
    insertDataOption="INSERT_ROWS",
    body={
        "values": [test_row]
    }
).execute()

print("SUCCESS!")
print("Test row added to Monitoring sheet.")