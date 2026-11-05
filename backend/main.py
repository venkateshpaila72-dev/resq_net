import os
import csv
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# ----------------- Environment Setup -----------------
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

def log(msg: str):
    """Ensure instant console output in terminals and Render logs."""
    print(msg, flush=True)

GROK_API_KEY = os.getenv("GROK_API_KEY", "").strip().strip("'").strip('"')
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Grok (xAI) API Configuration
GROK_API_URL = "https://api.x.ai/v1/chat/completions"
# Primary and fallback Grok models
GROK_MODELS = ["grok-beta", "grok-2", "grok-2-mini"]

if GROK_API_KEY:
    log("[*] Grok (xAI) API key detected.")
else:
    log("[!] Warning: GROK_API_KEY is not defined in .env.")

# ----------------- FastAPI & CORS -----------------
app = FastAPI(title="ResQ-Net SMS & Grok AI Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- MongoDB Initialization -----------------
incoming_col = None
outgoing_col = None

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    mongo_client.admin.command("ping")
    db = mongo_client["resq_net_db"]
    incoming_col = db["incoming_messages"]
    outgoing_col = db["outgoing_replies"]
    log("[*] MongoDB successfully connected and ready.")
except Exception as e:
    log(f"[!] Warning: MongoDB connection failed: {e}. Falling back to CSV only.")
    incoming_col = None
    outgoing_col = None

# ----------------- CSV Persistence Fallback -----------------
INCOMING_CSV = "incoming_sms.csv"
REPLIES_CSV = "outgoing_replies.csv"

for filename, headers in [
    (INCOMING_CSV, ["Message_ID", "Timestamp", "Sender", "Query"]),
    (REPLIES_CSV, ["Reply_ID", "Reference_Message_ID", "Timestamp", "Recipient", "Reply_Text", "Status"])
]:
    if not os.path.exists(filename):
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)


# ----------------- Helper Functions -----------------
def is_valid_mobile_number(number: str) -> bool:
    """Filter out non-numeric service/operator IDs like AA-AIRTEL-S."""
    clean = re.sub(r"[\s\-\(\)\+]", "", str(number))
    return clean.isdigit() and len(clean) >= 7


def save_incoming(msg_id: str, timestamp: str, sender: str, query: str):
    """Stores incoming record to both CSV and MongoDB."""
    with open(INCOMING_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([msg_id, timestamp, sender, query])

    if incoming_col is not None:
        try:
            incoming_col.insert_one({
                "_id": msg_id,
                "message_id": msg_id,
                "timestamp": timestamp,
                "sender": sender,
                "query": query,
                "created_at": datetime.now(timezone.utc)
            })
            log(f"[DB] Inserted incoming message: {msg_id}")
        except PyMongoError as pe:
            log(f"[DB ERROR] Failed to save incoming message: {pe}")


def save_outgoing(reply_id: str, ref_msg_id: str, timestamp: str, recipient: str, reply_text: str, status: str):
    """Stores outgoing reply record to both CSV and MongoDB."""
    with open(REPLIES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([reply_id, ref_msg_id, timestamp, recipient, reply_text, status])

    if outgoing_col is not None:
        try:
            outgoing_col.insert_one({
                "_id": reply_id,
                "reply_id": reply_id,
                "reference_message_id": ref_msg_id,
                "timestamp": timestamp,
                "recipient": recipient,
                "reply_text": reply_text,
                "status": status,
                "created_at": datetime.now(timezone.utc)
            })
            log(f"[DB] Inserted outgoing reply: {reply_id}")
        except PyMongoError as pe:
            log(f"[DB ERROR] Failed to save outgoing reply: {pe}")


def get_emergency_failsafe(query: str) -> str:
    """Offline first-aid fallback if API key or network is down."""
    q = query.lower()
    if any(k in q for k in ["fire", "smoke", "flame", "burning"]):
        return "ResQ-Net: Evacuate immediately! Crawl low under smoke. Feel doors before opening. Once out, call 101/112."
    if any(k in q for k in ["burn", "blister", "cracker"]):
        return "ResQ-Net: Cool burn under running water 10-20 mins. Do not use ice, paste, or oil. Cover with clean cloth. Seek medical aid."
    if any(k in q for k in ["bleed", "cut", "wound"]):
        return "ResQ-Net: Apply firm, direct pressure with clean cloth. Elevate wound above heart level. Do not remove soaked cloth, add more on top."
    if any(k in q for k in ["flood", "drown", "water rising"]):
        return "ResQ-Net: Move to higher floor or solid ground. Disconnect main power grid. Avoid walking or driving in floodwaters."
    if any(k in q for k in ["fracture", "broken bone"]):
        return "ResQ-Net: Immobilize injured limb. Do not try to realign bone. Apply cold pack wrapped in cloth. Seek medical transport."

    return "ResQ-Net: Emergency assistance acknowledged. Stay in a safe, elevated location. Conserve battery. Help is being notified."


def ask_grok(user_prompt: str) -> str:
    """Sends query to xAI Grok API with SMS character constraints."""
    if not GROK_API_KEY:
        log("[AI PIPELINE] GROK_API_KEY missing. Using failsafe.")
        return get_emergency_failsafe(user_prompt)

    system_instruction = (
        "You are an offline emergency disaster SMS assistant. Answer the user query directly, concisely, and factually. "
        "Do NOT use markdown, bold text, asterisks, bullet points, or emojis. "
        "Keep the entire response under 150 characters so it fits in a single cellular SMS."
    )

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }

    for model in GROK_MODELS:
        try:
            log(f"[AI PIPELINE] Querying Grok model '{model}' with: '{user_prompt}'")
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 80
            }

            res = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=12)

            if res.status_code == 200:
                data = res.json()
                raw_text = data["choices"][0]["message"]["content"]
                clean_text = raw_text.replace("*", "").replace("#", "").strip()
                log(f"[AI SUCCESS] Grok model '{model}' replied.")
                return clean_text[:160]
            else:
                log(f"[AI HTTP ERROR] Grok '{model}' returned HTTP {res.status_code}: {res.text}")
                continue

        except Exception as exc:
            log(f"[AI EXCEPTION] Error calling Grok model '{model}': {exc}")
            continue

    log("[AI FAILED] All Grok models failed. Using local rule engine.")
    return get_emergency_failsafe(user_prompt)


def send_sms_via_gateway(recipient: str, message: str) -> bool:
    """Dispatches outbound SMS back to recipient via SMS-Gate API."""
    auth = (GATEWAY_USERNAME, GATEWAY_PASSWORD)
    payload = {
        "deviceId": DEVICE_ID,
        "phoneNumbers": [recipient],
        "message": message
    }
    try:
        log(f"[GATEWAY] Sending outbound SMS to {recipient}...")
        response = requests.post(GATEWAY_API_URL, json=payload, auth=auth, timeout=12)
        if response.status_code in (200, 201, 202):
            log(f"[SMS DISPATCHED] Response delivered to {recipient}")
            return True
        else:
            log(f"[SMS GATEWAY ERROR] HTTP {response.status_code}: {response.text}")
            return False
    except Exception as exc:
        log(f"[SMS GATEWAY EXCEPTION] {exc}")
        return False


def process_query_in_background(message_id: str, sender: str, raw_text: str):
    """Background task: Parse -> Query Grok -> Dispatch SMS -> Save to MongoDB/CSV."""
    try:
        clean_prompt = raw_text
        if clean_prompt.upper().startswith("ENET"):
            clean_prompt = clean_prompt[4:].strip()

        if not clean_prompt:
            clean_prompt = "Emergency assistance needed"

        log(f"\n==========================================")
        log(f"[PIPELINE START] ID: {message_id} | Sender: {sender} | Prompt: '{clean_prompt}'")

        # 1. Query Grok
        ai_answer = ask_grok(clean_prompt)
        log(f"[GROK RESPONSE READY]: {ai_answer}")

        # 2. Dispatch SMS
        reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
        sms_sent = send_sms_via_gateway(sender, ai_answer)
        status_label = "SENT" if sms_sent else "FAILED"

        # 3. Log to MongoDB and CSV
        reply_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_outgoing(reply_id, message_id, reply_timestamp, sender, ai_answer, status_label)
        log(f"[PIPELINE COMPLETE] Task {message_id} finished with status: {status_label}")
        log(f"==========================================\n")
    except Exception as err:
        log(f"[PIPELINE CRITICAL ERROR]: {err}")


# ----------------- API Endpoints -----------------
@app.get("/")
@app.head("/")
async def root():
    return {
        "service": "ResQ-Net SMS & Grok AI Gateway",
        "status": "online",
        "mongo_connected": incoming_col is not None,
        "grok_ready": bool(GROK_API_KEY)
    }


@app.post("/sms-webhook")
async def receive_sms_webhook(request: Request, background_tasks: BackgroundTasks):
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

    # Filter out telecommunication carrier notices (Airtel, Vi, Jio)
    if not is_valid_mobile_number(sender):
        log(f"[SKIPPED AUTO-REPLY] Sender '{sender}' is not a mobile number.")
        return {"status": "saved", "note": "Operator message ignored"}

    # Save incoming record to MongoDB and CSV
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"
    save_incoming(message_id, timestamp, sender, cleaned_query)
    log(f"\n[{message_id}] Received from: {sender} | Query: {cleaned_query}")

    # Offload Grok query & SMS dispatch to background task
    background_tasks.add_task(process_query_in_background, message_id, sender, cleaned_query)

    return {"status": "accepted", "message_id": message_id}


class BroadcastRequest(BaseModel):
    recipient: str
    message: str


@app.post("/api/broadcast")
async def send_manual_broadcast(item: BroadcastRequest):
    """Allows manual SMS dispatch directly from the dashboard."""
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    success = send_sms_via_gateway(item.recipient, item.message)
    status_label = "SENT" if success else "FAILED"

    save_outgoing(reply_id, "MANUAL_BROADCAST", timestamp, item.recipient, item.message, status_label)
    return {"status": status_label, "reply_id": reply_id}


@app.get("/api/messages")
async def get_all_messages():
    """Returns recent messages from MongoDB (with automatic CSV fallback)."""
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
            log(f"[MONGODB READ ERROR] {e}. Falling back to CSV.")

    # CSV Fallback
    inc_list = []
    out_list = []

    if os.path.exists(INCOMING_CSV):
        with open(INCOMING_CSV, "r", encoding="utf-8") as f:
            inc_list = list(csv.DictReader(f))[::-1][:50]

    if os.path.exists(REPLIES_CSV):
        with open(REPLIES_CSV, "r", encoding="utf-8") as f:
            out_list = list(csv.DictReader(f))[::-1][:50]

    return {
        "source": "csv_fallback",
        "incoming": [
            {
                "_id": r.get("Message_ID", ""),
                "message_id": r.get("Message_ID", ""),
                "timestamp": r.get("Timestamp", ""),
                "sender": r.get("Sender", ""),
                "query": r.get("Query", "")
            } for r in inc_list
        ],
        "outgoing": [
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
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)