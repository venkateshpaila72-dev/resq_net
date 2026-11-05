import os
import csv
import re
import uuid
from pathlib import Path
from datetime import datetime
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
import uvicorn
from google import genai

# ----------------- Configuration & Env Setup -----------------
# Load environment variables from .env file
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is not set. Please add it to your .env file or environment.")

# Initialize Client globally using the environment variable
client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="Offline Disaster SMS Gemini Gateway")

INCOMING_CSV = "incoming_sms.csv"
REPLIES_CSV = "outgoing_replies.csv"

# ----------------- CSV Initialization -----------------
if not os.path.exists(INCOMING_CSV):
    with open(INCOMING_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Message_ID", "Timestamp", "Sender", "Query"])

if not os.path.exists(REPLIES_CSV):
    with open(REPLIES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Reply_ID", "Reference_Message_ID", "Timestamp", "Recipient", "Reply_Text", "Status"])


# ----------------- Helper Functions -----------------
def is_valid_mobile_number(number: str) -> bool:
    """Ignore alphanumeric service/operator sender tags (e.g. AA-AIRTEL-S)."""
    clean = re.sub(r"[\s\-\(\)\+]", "", str(number))
    return clean.isdigit() and len(clean) >= 7


def log_incoming_message(msg_id: str, timestamp: str, sender: str, query: str):
    """Saves incoming query record to incoming_sms.csv."""
    with open(INCOMING_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([msg_id, timestamp, sender, query])


def log_outgoing_reply(reply_id: str, ref_msg_id: str, timestamp: str, recipient: str, reply: str, status: str):
    """Saves outbound reply record to outgoing_replies.csv."""
    with open(REPLIES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([reply_id, ref_msg_id, timestamp, recipient, reply, status])


def ask_gemini(user_prompt: str) -> str:
    """Queries Gemini using client.interactions.create with SMS formatting constraints."""
    system_instruction = (
        "your are an multipurpose assistant that also tells weather. so answer the queries in short"
    )
    formatted_input = f"{system_instruction}\n\nUser Question: {user_prompt}"

    try:
        # Reuses the globally configured client
        interaction = client.interactions.create(
            model="gemini-2.5-flash",
            input=formatted_input
        )
        output = interaction.output_text
        return output
    except Exception as exc:
        print(f"[GEMINI ERROR] {exc}")
        return "ResQ-Net: Unable to fetch AI response right now."


def send_sms_via_gateway(recipient: str, message: str) -> bool:
    """Dispatches outbound SMS back to recipient via SMS Gate Cloud API."""
    auth = (GATEWAY_USERNAME, GATEWAY_PASSWORD)
    payload = {
        "deviceId": DEVICE_ID,
        "phoneNumbers": [recipient],
        "message": message
    }
    try:
        response = requests.post(GATEWAY_API_URL, json=payload, auth=auth, timeout=12)
        if response.status_code in (200, 201, 202):
            print(f"[SMS DISPATCHED] Response delivered to {recipient}")
            return True
        else:
            print(f"[SMS GATEWAY ERROR] HTTP {response.status_code}: {response.text}")
            return False
    except Exception as exc:
        print(f"[SMS GATEWAY EXCEPTION] {exc}")
        return False


# ----------------- Webhook Route -----------------
@app.post("/sms-webhook")
async def receive_sms_webhook(request: Request):
    content_type = request.headers.get("content-type", "")
    sender = "Unknown"
    raw_query = ""

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}

        payload = data.get("payload") or data
        raw_query = payload.get("message") or payload.get("text") or payload.get("body") or ""
        sender = (
            payload.get("sender")
            or payload.get("phone_number")
            or payload.get("from")
            or payload.get("senderNumber")
            or "Unknown"
        )
    else:
        form = await request.form()
        raw_query = form.get("Body") or form.get("message") or ""
        sender = form.get("From") or form.get("from") or "Unknown"

    cleaned_query = str(raw_query).strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"

    # 1. Log incoming SMS
    log_incoming_message(message_id, timestamp, sender, cleaned_query)
    print(f"\n[{message_id}] From: {sender} | Query: {cleaned_query}")

    # 2. Skip auto-reply for non-numeric telecom/service IDs
    if not is_valid_mobile_number(sender):
        print(f"[SKIPPED AUTO-REPLY] Sender '{sender}' is not a mobile number.")
        return {"status": "saved", "note": "Operator message ignored"}

    # 3. Strip optional keyword prefix
    clean_prompt = cleaned_query
    if clean_prompt.upper().startswith("ENET"):
        clean_prompt = clean_prompt[4:].strip()
    if not clean_prompt:
        clean_prompt = "Hello"

    # 4. Fetch Gemini Answer
    ai_answer = ask_gemini(clean_prompt)
    print(f"[AI RESPONSE]: {ai_answer}")

    # 5. Dispatch SMS back to sender
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    sms_sent = send_sms_via_gateway(sender, ai_answer)
    status_label = "SENT" if sms_sent else "FAILED"

    # 6. Log reply to CSV
    reply_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_outgoing_reply(reply_id, message_id, reply_timestamp, sender, ai_answer, status_label)

    return {
        "status": "success",
        "message_id": message_id,
        "reply_id": reply_id,
        "sms_status": status_label
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)