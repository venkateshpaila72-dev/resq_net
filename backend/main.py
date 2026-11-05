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
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# ----------------- Configuration & Env Setup -----------------
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip("'").strip('"')
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# 1. Initialize FastAPI & CORS
app = FastAPI(title="ResQ-Net SMS & AI Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. MongoDB Setup
mongo_client = None
incoming_col = None
outgoing_col = None

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    # Ping database
    mongo_client.admin.command("ping")
    db = mongo_client["resq_net_db"]
    incoming_col = db["incoming_messages"]
    outgoing_col = db["outgoing_replies"]
    print("[*] MongoDB successfully connected and ready.")
except Exception as e:
    print(f"[!] Warning: MongoDB connection failed ({e}). Running with local CSV persistence.")
    incoming_col = None
    outgoing_col = None

# CSV Fallbacks
INCOMING_CSV = "incoming_sms.csv"
REPLIES_CSV = "outgoing_replies.csv"
PROCESSED_MESSAGE_IDS = {}

for filename, headers in [
    (INCOMING_CSV, ["Message_ID", "Timestamp", "Sender", "Location", "Query"]),
    (REPLIES_CSV, ["Reply_ID", "Reference_Message_ID", "Timestamp", "Recipient", "Reply_Text", "Status"])
]:
    if not os.path.exists(filename):
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)


# ----------------- Offline Emergency Knowledge Base -----------------
def get_offline_first_aid(query: str) -> str:
    """Delivers exact emergency first aid protocols when offline or when API quota is hit."""
    q = query.lower()
    
    if any(k in q for k in ["burn", "cracker", "firework", "blister"]):
        return "ResQ-Net Burn Aid: Cool burn under running water 10-20 mins. Do not use ice, paste, or oil. Cover with clean, non-stick cloth. Do not burst blisters."
    
    if any(k in q for k in ["bleed", "cut", "wound"]):
        return "ResQ-Net Bleeding Aid: Apply firm, direct pressure with clean cloth. Elevate wound above heart level. Do not remove soaked cloths, add more on top."
    
    if any(k in q for k in ["fracture", "broken bone", "sprain"]):
        return "ResQ-Net Fracture Aid: Immobilize area. Do not try to realign bone. Apply cold pack wrapped in cloth. Support with makeshift splint or rolled cloth."
        
    if any(k in q for k in ["flood", "drown", "water rising", "cyclone"]):
        return "ResQ-Net Flood Alert: Move to highest floor or solid elevated ground. Switch off main electricity. Do not walk/drive through floodwaters. Keep phone in waterproof bag."
        
    if any(k in q for k in ["snake", "bite"]):
        return "ResQ-Net Snakebite: Keep victim calm and still. Immobilize bitten limb below heart level. Do NOT cut, suck venom, or use tourniquet. Seek antivenom immediately."

    if any(k in q for k in ["cpr", "unconscious", "breathing"]):
        return "ResQ-Net CPR: Place hands center of chest. Push hard and fast (100-120 bpm, 2 inches deep) to tune of Stayin Alive. Continue till medical help arrives."

    if any(k in q for k in ["hello", "hi", "test"]):
        return "ResQ-Net Active: State your emergency or first-aid query (e.g. burn aid, flood shelter, wound dressing) with your location."

    return "ResQ-Net Emergency: Conserve battery. If in physical danger, seek elevated sturdy shelter and alert local rescue. Awaiting field dispatch."


# ----------------- Helpers -----------------
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
    # CSV
    with open(INCOMING_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([msg_id, timestamp, sender, location, query])

    # MongoDB
    if incoming_col is not None:
        try:
            incoming_col.insert_one({
                "_id": msg_id,
                "message_id": msg_id,
                "timestamp": timestamp,
                "sender": sender,
                "location": location,
                "query": query,
                "created_at": datetime.now(timezone.utc)
            })
        except Exception as pe:
            print(f"[MONGODB ERROR] Insert incoming: {pe}")


def log_outgoing_reply(reply_id: str, ref_msg_id: str, timestamp: str, recipient: str, reply: str, status: str):
    # CSV
    with open(REPLIES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([reply_id, ref_msg_id, timestamp, recipient, reply, status])

    # MongoDB
    if outgoing_col is not None:
        try:
            outgoing_col.insert_one({
                "_id": reply_id,
                "reply_id": reply_id,
                "reference_message_id": ref_msg_id,
                "timestamp": timestamp,
                "recipient": recipient,
                "reply_text": reply,
                "status": status,
                "created_at": datetime.now(timezone.utc)
            })
        except Exception as pe:
            print(f"[MONGODB ERROR] Insert outgoing: {pe}")


def ask_gemini(user_prompt: str, location: str = "Unspecified") -> str:
    """Tries primary models with fallback. Falls back to offline emergency rules if quota hit."""
    system_instruction = (
        "You are an offline disaster and first-aid assistant. Answer directly and factually. "
        "No markdown, bullets, or asterisks. Keep the answer strictly under 200 characters for SMS."
    )
    formatted_input = f"{system_instruction}\n\nSender Location: {location}\nQuestion: {user_prompt}"

    payload = {
        "contents": [{"parts": [{"text": formatted_input}]}]
    }

    # Model hierarchy: try gemini-1.5-flash first (much higher quota limits), then gemini-2.5-flash
    models = ["gemini-1.5-flash", "gemini-2.5-flash"]

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=8)
            if res.status_code == 200:
                data = res.json()
                candidates = data.get("candidates", [])
                if candidates:
                    text = candidates[0]["content"]["parts"][0]["text"]
                    return text.replace("*", "").replace("#", "").strip()[:240]
            elif res.status_code == 429:
                print(f"[QUOTA WARNING] Model {model} hit rate limit / quota exceeded.")
                continue
            else:
                print(f"[GEMINI ERROR {res.status_code}] {res.text}")
        except Exception as exc:
            print(f"[GEMINI EXCEPTION on {model}]: {exc}")

    # If all API calls fail or quota is exhausted, use deterministic emergency aid engine
    print("[FAILSAFE] Using local offline first-aid engine.")
    return get_offline_first_aid(user_prompt)


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
        clean_prompt = "hello"

    print(f"\n[BACKGROUND PROCESSING] Query: '{clean_prompt}' | Loc: '{location}'")
    ai_answer = ask_gemini(clean_prompt, location)
    print(f"[AI RESPONSE READY]: {ai_answer}")

    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    sms_sent = send_sms_via_gateway(sender, ai_answer)
    status_label = "SENT" if sms_sent else "FAILED"

    reply_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_outgoing_reply(reply_id, message_id, reply_timestamp, sender, ai_answer, status_label)


# ----------------- Routes -----------------
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

    # Deduplication
    now = time.time()
    to_delete = [k for k, v in PROCESSED_MESSAGE_IDS.items() if now - v > 300]
    for k in to_delete:
        del PROCESSED_MESSAGE_IDS[k]

    dedup_key = gateway_msg_id if gateway_msg_id else f"{sender}_{cleaned_query}"
    if dedup_key in PROCESSED_MESSAGE_IDS:
        print(f"[DUPLICATE BLOCKED] Ignored retry for {dedup_key}")
        return {"status": "ignored", "reason": "duplicate_retry"}

    PROCESSED_MESSAGE_IDS[dedup_key] = now

    detected_loc = extract_location(cleaned_query)
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"
    log_incoming_message(message_id, timestamp, sender, detected_loc, cleaned_query)
    print(f"\n[{message_id}] From: {sender} | Loc: {detected_loc} | Query: {cleaned_query}")

    if not is_valid_mobile_number(sender):
        print(f"[SKIPPED AUTO-REPLY] Sender '{sender}' is not a valid mobile number.")
        return {"status": "saved", "note": "Operator SMS ignored"}

    # Execute non-blocking in background
    background_tasks.add_task(process_query_in_background, message_id, sender, cleaned_query, detected_loc)
    return {"status": "accepted", "message_id": message_id}


class BroadcastRequest(BaseModel):
    recipient: str
    message: str


@app.post("/api/broadcast")
async def send_manual_broadcast(item: BroadcastRequest):
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    success = send_sms_via_gateway(item.recipient, item.message)
    status_label = "SENT" if success else "FAILED"
    
    log_outgoing_reply(reply_id, "MANUAL_BROADCAST", timestamp, item.recipient, item.message, status_label)
    return {"status": status_label, "reply_id": reply_id}


@app.get("/api/messages")
async def get_all_messages():
    """Returns stored messages from MongoDB, with automatic CSV fallback."""
    if incoming_col is not None and outgoing_col is not None:
        try:
            incoming = list(incoming_col.find().sort("created_at", -1).limit(50))
            outgoing = list(outgoing_col.find().sort("created_at", -1).limit(50))

            for doc in incoming + outgoing:
                doc["_id"] = str(doc["_id"])
                if "created_at" in doc and doc["created_at"]:
                    doc["created_at"] = doc["created_at"].isoformat()

            return {
                "source": "mongodb",
                "incoming": incoming,
                "outgoing": outgoing
            }
        except Exception as e:
            print(f"[MONGODB READ ERROR] {e}")

    # Fallback: Read from CSV
    inc_list = []
    out_list = []

    if os.path.exists(INCOMING_CSV):
        with open(INCOMING_CSV, "r", encoding="utf-8") as f:
            inc_list = list(csv.DictReader(f))[::-1][:50]

    if os.path.exists(REPLIES_CSV):
        with open(REPLIES_CSV, "r", encoding="utf-8") as f:
            out_list = list(csv.DictReader(f))[::-1][:50]

    formatted_in = [
        {
            "_id": r.get("Message_ID", ""),
            "message_id": r.get("Message_ID", ""),
            "timestamp": r.get("Timestamp", ""),
            "sender": r.get("Sender", ""),
            "location": r.get("Location", "Unspecified"),
            "query": r.get("Query", "")
        } for r in inc_list
    ]

    formatted_out = [
        {
            "_id": r.get("Reply_ID", ""),
            "reply_id": r.get("Reply_ID", ""),
            "reference_message_id": r.get("Reference_Message_ID", ""),
            "timestamp": r.get("Timestamp", ""),
            "recipient": r.get("Recipient", ""),
            "reply_text": r.get("Reply_Text", ""),
            "status": r.get("Status", "UNKNOWN")
        } for r in out_list
    ]

    return {
        "source": "csv_fallback",
        "incoming": formatted_in,
        "outgoing": formatted_out
    }


if __name__ == "__main__":
    # Disable reload=True to prevent Windows spawn re-import deadlocks
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)