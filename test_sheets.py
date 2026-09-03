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

# Test row - matches the unified Monitoring sheet schema (see
# summary_parser.MONITORING_HEADERS):
# Date | Section | Site | Description | Days Open | Action Taken |
# What's Needed Next | New Issues | Issues Resolved | Total Open Issues |
# Vendor | Notes
# A clearly synthetic date (year 2099) keeps this from ever being mistaken
# for a real report.
test_row = [
    "2099-01-01",
    "Daily Summary",
    "",
    "Testing Google Sheets connection",
    "",
    "",
    "",
    0,
    0,
    0,
    "",
    "Added by test_sheets.py"
]

# Add row to Monitoring sheet
result = service.spreadsheets().values().append(
    spreadsheetId=SPREADSHEET_ID,
    range=f"{SHEET_NAME}!A:L",
    valueInputOption="USER_ENTERED",
    insertDataOption="INSERT_ROWS",
    body={
        "values": [test_row]
    }
).execute()

print("SUCCESS!")
print("Test row added to Monitoring sheet.")
