import os
import csv
import re
import uuid
import time
from pathlib import Path
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(title="Offline Disaster SMS Gemini Gateway with MongoDB")

# Enable CORS for React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for local Vite & deployed URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BroadcastRequest(BaseModel):
    recipient: str
    message: str

@app.post("/api/broadcast")
async def send_manual_broadcast(item: BroadcastRequest):
    """Allows responders to manually dispatch emergency SMS directly from the dashboard."""
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    success = send_sms_via_gateway(item.recipient, item.message)
    status_label = "SENT" if success else "FAILED"
    
    log_outgoing_reply(reply_id, "MANUAL_BROADCAST", timestamp, item.recipient, item.message, status_label)
    
    return {"status": status_label, "reply_id": reply_id}

# ----------------- Configuration & Env Setup -----------------
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY").strip().strip("'").strip('"')
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# ----------------- MongoDB Initialization -----------------
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    db = mongo_client["resq_net_db"]
    incoming_col = db["incoming_messages"]
    outgoing_col = db["outgoing_replies"]

    # Test connection
    mongo_client.admin.command("ping")
    print("[*] MongoDB successfully connected.")
except Exception as e:
    print(f"[!] Warning: MongoDB connection failed: {e}. Falling back to CSV only.")
    incoming_col = None
    outgoing_col = None


INCOMING_CSV = "incoming_sms.csv"
REPLIES_CSV = "outgoing_replies.csv"

# Cache recently processed message IDs (gateway messageId -> timestamp)
PROCESSED_MESSAGE_IDS = {}

# ----------------- CSV Initialization -----------------
for filename, headers in [
    (INCOMING_CSV, ["Message_ID", "Timestamp", "Sender", "Location", "Query"]),
    (REPLIES_CSV, ["Reply_ID", "Reference_Message_ID", "Timestamp", "Recipient", "Reply_Text", "Status"])
]:
    if not os.path.exists(filename):
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)


# ----------------- Helper Functions -----------------
def is_valid_mobile_number(number: str) -> bool:
    clean = re.sub(r"[\s\-\(\)\+]", "", str(number))
    return clean.isdigit() and len(clean) >= 7


def extract_location(text: str) -> str:
    gps_match = re.search(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", text)
    if gps_match:
        return f"{gps_match.group(1)},{gps_match.group(2)}"
    tag_match = re.search(r"(?:LOC|LOCATION|AT|NEAR)\s*[:\-]?\s*([a-zA-Z0-9\s]+?)(?:[,\.\-]|(?:\s+(?:need|help|water|food|stuck|is)))", text, re.IGNORECASE)
    if tag_match:
        return tag_match.group(1).strip()
    return "Unspecified"


def log_incoming_message(msg_id: str, timestamp: str, sender: str, location: str, query: str):
    """Logs incoming message to CSV and MongoDB."""
    # 1. CSV Backup
    with open(INCOMING_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([msg_id, timestamp, sender, location, query])

    # 2. MongoDB Document
    if incoming_col is not None:
        try:
            doc = {
                "_id": msg_id,
                "message_id": msg_id,
                "timestamp": timestamp,
                "sender": sender,
                "location": location,
                "query": query,
                "created_at": datetime.now(timezone.utc)
            }
            incoming_col.insert_one(doc)
            print(f"[MONGODB] Inserted incoming message {msg_id}")
        except PyMongoError as pe:
            print(f"[MONGODB ERROR] Failed to save incoming message: {pe}")


def log_outgoing_reply(reply_id: str, ref_msg_id: str, timestamp: str, recipient: str, reply: str, status: str):
    """Logs outbound reply to CSV and MongoDB."""
    # 1. CSV Backup
    with open(REPLIES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([reply_id, ref_msg_id, timestamp, recipient, reply, status])

    # 2. MongoDB Document
    if outgoing_col is not None:
        try:
            doc = {
                "_id": reply_id,
                "reply_id": reply_id,
                "reference_message_id": ref_msg_id,
                "timestamp": timestamp,
                "recipient": recipient,
                "reply_text": reply,
                "status": status,
                "created_at": datetime.now(timezone.utc)
            }
            outgoing_col.insert_one(doc)
            print(f"[MONGODB] Inserted outgoing reply {reply_id}")
        except PyMongoError as pe:
            print(f"[MONGODB ERROR] Failed to save outgoing reply: {pe}")


def ask_gemini(user_prompt: str, location: str = "Unspecified") -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    system_instruction = (
        "You are an offline emergency disaster assistant. Provide a direct, concise, factual answer. "
        "No markdown, bullets, or asterisks. Maximum 200 characters."
    )
    formatted_input = (
        f"{system_instruction}\n\n"
        f"Sender Location Context: {location}\n"
        f"User Query: {user_prompt}"
    )

    payload = {
        "contents": [{"parts": [{"text": formatted_input}]}]
    }

    try:
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            candidates = data.get("candidates", [])
            if candidates:
                text = candidates[0]["content"]["parts"][0]["text"]
                return text.replace("*", "").replace("#", "").strip()[:240]
        else:
            print(f"[GEMINI REST ERROR] HTTP {res.status_code}: {res.text}")
    except Exception as exc:
        print(f"[GEMINI EXCEPTION] {exc}")

    loc_str = f" in {location}" if location != "Unspecified" else ""
    return f"ResQ-Net: Seek elevated solid shelter{loc_str}. Disconnect main power grid. Conserve phone battery."


def send_sms_via_gateway(recipient: str, message: str) -> bool:
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


def process_query_in_background(message_id: str, sender: str, query: str, location: str):
    clean_prompt = query
    if clean_prompt.upper().startswith("ENET"):
        clean_prompt = clean_prompt[4:].strip()
    if not clean_prompt:
        clean_prompt = "Emergency assistance needed"

    print(f"\n[BACKGROUND PROCESSING] Query: '{clean_prompt}' | Loc: '{location}'")
    ai_answer = ask_gemini(clean_prompt, location)
    print(f"[AI RESPONSE READY]: {ai_answer}")

    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    sms_sent = send_sms_via_gateway(sender, ai_answer)
    status_label = "SENT" if sms_sent else "FAILED"

    reply_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_outgoing_reply(reply_id, message_id, reply_timestamp, sender, ai_answer, status_label)


# ----------------- Webhook Route -----------------
@app.post("/sms-webhook")
async def receive_sms_webhook(request: Request, background_tasks: BackgroundTasks):
    content_type = request.headers.get("content-type", "")
    sender = "Unknown"
    raw_query = ""
    gateway_msg_id = ""

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
        gateway_msg_id = payload.get("messageId") or data.get("id") or ""
    else:
        form = await request.form()
        raw_query = form.get("Body") or form.get("message") or ""
        sender = form.get("From") or form.get("from") or "Unknown"

    cleaned_query = str(raw_query).strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Deduplication Filter
    now = time.time()
    to_delete = [k for k, v in PROCESSED_MESSAGE_IDS.items() if now - v > 300]
    for k in to_delete:
        del PROCESSED_MESSAGE_IDS[k]

    dedup_key = gateway_msg_id if gateway_msg_id else f"{sender}_{cleaned_query}"
    if dedup_key in PROCESSED_MESSAGE_IDS:
        print(f"[DUPLICATE BLOCKED] Ignored retry for {dedup_key}")
        return {"status": "ignored", "reason": "duplicate_retry"}

    PROCESSED_MESSAGE_IDS[dedup_key] = now

    # 2. Extract Location & Log Incoming to CSV + MongoDB
    detected_loc = extract_location(cleaned_query)
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"
    log_incoming_message(message_id, timestamp, sender, detected_loc, cleaned_query)
    print(f"\n[{message_id}] From: {sender} | Loc: {detected_loc} | Query: {cleaned_query}")

    # 3. Filter Operator Numbers
    if not is_valid_mobile_number(sender):
        print(f"[SKIPPED AUTO-REPLY] Sender '{sender}' is not a valid mobile number.")
        return {"status": "saved", "note": "Operator SMS ignored"}

    # 4. Immediate HTTP 200 + Background Execution
    background_tasks.add_task(process_query_in_background, message_id, sender, cleaned_query, detected_loc)

    return {"status": "accepted", "message_id": message_id}


# ----------------- MongoDB Query Endpoint -----------------
@app.get("/api/messages")
async def get_all_messages():
    """Returns recent messages directly from MongoDB."""
    if incoming_col is None:
        return {"error": "MongoDB is not connected"}

    incoming = list(incoming_col.find().sort("created_at", -1).limit(50))
    outgoing = list(outgoing_col.find().sort("created_at", -1).limit(50))

    # Convert BSON fields for JSON serialization
    for doc in incoming + outgoing:
        doc["_id"] = str(doc["_id"])
        if "created_at" in doc and doc["created_at"]:
            doc["created_at"] = doc["created_at"].isoformat()

    return {
        "incoming_count": len(incoming),
        "outgoing_count": len(outgoing),
        "incoming": incoming,
        "outgoing": outgoing
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)