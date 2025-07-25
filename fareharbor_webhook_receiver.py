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
BACKUP_TAB_NAME = "Webhook Log"

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

# ======= BOAT TYPE DETECTION =======
SINGLE_KEYWORDS = ["single"]
DOUBLE_KEYWORDS = ["double", "tandem"]
SUP_KEYWORDS = ["sup", "paddleboard"]

def detect_boat_type(notes, custom_fields, customers):
    combined = (notes or "").lower()
    
    for field in custom_fields:
        if isinstance(field, dict):
            val = field.get("value", "")
            display_val = field.get("display_value", "")
            combined += f" {val.lower()} {display_val.lower()}"

    for customer in customers:
        try:
            type_name = customer["customer_type_rate"]["customer_type"]["singular"].lower()
            combined += " " + type_name
        except (KeyError, TypeError):
            continue

    if any(word in combined for word in SINGLE_KEYWORDS):
        return "Single"
    elif any(word in combined for word in DOUBLE_KEYWORDS):
        return "Double"
    elif any(word in combined for word in SUP_KEYWORDS):
        return "SUP"
    return "Unlisted"

# ======= BACKUP LOGGING =======
def log_to_backup_sheet(data):
    try:
        sh = GC.open(SPREADSHEET_NAME)
        backup_ws = sh.worksheet(BACKUP_TAB_NAME)
    except Exception as e:
        print("🚨 Backup log sheet not found:", e)
        return

    headers = [
        "Timestamp (UTC)", "Product Name", "Start Date", "Detected Boat Type",
        "Notes", "Custom Field Values", "Logged?", "Failure Reason"
    ]

    existing = backup_ws.get_all_values()
    if not existing or existing[0] != headers:
        backup_ws.resize(rows=1)
        backup_ws.insert_row(headers, index=1)

    backup_ws.append_row([
        data.get("timestamp", ""),
        data.get("product_name", ""),
        data.get("start_date", ""),
        data.get("boat_type", ""),
        data.get("notes", ""),
        json.dumps(data.get("custom_fields", [])),
        data.get("logged", ""),
        data.get("error", "")
    ])

# ======= SHEET UPDATE =======
def update_google_sheet(booking_data):
    log_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "start_date": booking_data.get("availability", {}).get("start_at", ""),
        "notes": booking_data.get("note", ""),
        "custom_fields": booking_data.get("custom_field_values", [])
    }

    item = booking_data.get("availability", {}).get("item", {})
    item_name = item.get("name", "")
    log_data["product_name"] = item_name

    print(f"🧪 Incoming Item Name: '{item_name}'")

    try:
        sh = GC.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(TAB_NAME)
    except Exception as e:
        log_data["logged"] = "No"
        log_data["boat_type"] = "N/A"
        log_data["error"] = f"Sheet error: {e}"
        log_to_backup_sheet(log_data)
        print("🚨 Sheet or tab not found:", e)
        return

    if item_name not in TARGET_ITEMS:
        log_data["logged"] = "No"
        log_data["boat_type"] = "N/A"
        log_data["error"] = f"Item not in TARGET_ITEMS: {item_name}"
        log_to_backup_sheet(log_data)
        print(f"⚠️ Logging skipped: item not in TARGET_ITEMS → '{item_name}'")
        return

    try:
        date = datetime.fromisoformat(log_data["start_date"])
    except ValueError:
        log_data["logged"] = "No"
        log_data["boat_type"] = "N/A"
        log_data["error"] = "Invalid start date"
        log_to_backup_sheet(log_data)
        return
    month = date.strftime("%b %Y")

    notes = log_data["notes"]
    custom_fields = log_data["custom_fields"]
    customers = booking_data.get("customers", [])

    boat_type = detect_boat_type(notes, custom_fields, customers)
    log_data["boat_type"] = boat_type

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
            log_data["logged"] = "Yes"
            log_data["error"] = ""
            log_to_backup_sheet(log_data)
            print(f"✅ Logged 1 {boat_type} for {month}")
            return

    log_data["logged"] = "No"
    log_data["error"] = f"No matching row for {boat_type} in {month}"
    log_to_backup_sheet(log_data)
    print(f"⚠️ No matching row found for {boat_type} in {month}")

# ======= ENDPOINT =======
@app.post("/fareharbor/webhook")
async def receive_booking(request: Request):
    payload = await request.json()
    booking = payload.get("booking", {})
    print("📦 Full Payload:\n", json.dumps(booking, indent=2))
    print("📦 Incoming Booking:", booking.get("pk", "No ID"))
    update_google_sheet(booking)
    return {"status": "received"}
