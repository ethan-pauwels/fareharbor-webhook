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

def detect_boat_type(booking):
    combined = ""
    # Try both custom field containers just in case
    for field in booking.get("custom_field_values", []) + booking.get("availability", {}).get("custom_field_instances", []):
        if isinstance(field, dict):
            combined += " " + field.get("value", "").lower()

    if "single" in combined:
        return "Single"
    elif "double" in combined:
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

    booking = booking_data.get("booking", {})
    item_name = booking.get("availability", {}).get("item", {}).get("name", "")
    if item_name not in TARGET_ITEMS:
        print(f"ℹ️ Skipping item: {item_name}")
        return

    start_at = booking.get("availability", {}).get("start_at")
    if not start_at:
        print("⚠️ Missing booking date")
        return

    try:
        date = datetime.fromisoformat(start_at.replace("Z", "+00:00")).astimezone(pytz.UTC)
    except ValueError:
        print("⚠️ Invalid date format:", start_at)
        return
    month = date.strftime("%b %Y")

    boat_type = detect_boat_type(booking)

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
    print("📦 Full Payload:\n", json.dumps(payload, indent=2))
    booking_id = payload.get("booking", {}).get("pk", "No ID")
    print("📦 Incoming Booking:", booking_id)
    update_google_sheet(payload)
    return {"status": "received"}
