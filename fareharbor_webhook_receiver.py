from fastapi import FastAPI, Request
from collections import defaultdict
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pytz
import os
import json
import base64

app = FastAPI()

# ======= CONFIG =======
SPREADSHEET_NAME = "Monthly Rentals Equipment Report"
TAB_NAME = "2025 Report"

SCOPE = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

# Load Google service account credentials from base64-encoded env variable
creds_b64 = os.environ.get("GOOGLE_SERVICE_CREDS_B64")
if not creds_b64:
    raise ValueError("GOOGLE_SERVICE_CREDS_B64 environment variable not found.")
creds_json = base64.b64decode(creds_b64).decode("utf-8")
creds_dict = json.loads(creds_json)

CREDS = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
GC = gspread.authorize(CREDS)

TARGET_ITEMS = [
    "Sunset Bat Tours",
    "Downtown to Barton Springs Tour",
    "Kayak and SUP Reservations"
]

def detect_boat_type(notes, custom_fields):
    combined = notes.lower() if notes else ""
    for field in custom_fields:
        if isinstance(field, dict):
            combined += " " + field.get("value", "").lower()

    if "single" in combined:
        return "Single"
    elif "double" in combined or "tandem" in combined:
        return "Double"
    elif "sup" in combined or "paddleboard" in combined:
        return "SUP"
    return "Unlisted"


def update_google_sheet(booking_data):
    try:
        sh = GC.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(TAB_NAME)
    except Exception as e:
        print("🚨 Sheet or tab not found:", e)
        return

    # Parse item and validate
    item_name = booking_data.get("availability", {}).get("item", {}).get("name", "")
    if item_name not in TARGET_ITEMS:
        return

    # Parse start date
    date_str = booking_data.get("availability", {}).get("start_at", "")
    try:
        date = datetime.fromisoformat(date_str)
    except ValueError:
        return
    month = date.strftime("%b %Y")

    # Pull notes and custom fields
    notes = booking_data.get("note", "")
    custom_fields = booking_data.get("custom_field_values", [])
    customers = booking_data.get("customers", [])

    boat_type = detect_boat_type(notes, custom_fields, customers)

    # Update sheet
    data = worksheet.get_all_values()
    for row_idx in range(1, len(data)):
        row = data[row_idx]
        if len(row) < 2:
            continue
        row_month = row[0].strip()
        row_boat = row[1].strip()
        if row_month == month and row_boat == boat_type:
            current = row[3].strip()
            current_val = int(current) if current.isdigit() else 0
            worksheet.update_cell(row_idx + 1, 4, current_val + 1)
            print(f"✅ Logged 1 {boat_type} for {month}")
            return

    print(f"⚠️ No matching row found for {boat_type} in {month}")

@app.post("/fareharbor/webhook")
async def receive_booking(request: Request):
    payload = await request.json()
    booking = payload.get("booking", {})
    print("📦 Full Payload:\n", json.dumps(booking, indent=2))
    print("📦 Incoming Booking:", booking.get("pk", "No ID"))
    update_google_sheet(booking)
    return {"status": "received"}
