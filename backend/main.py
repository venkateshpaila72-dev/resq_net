import os
import re
import sys
import uuid
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from pymongo import MongoClient

load_dotenv()

# Force unbuffered output so logs print immediately on Render
def log(msg: str):
    print(msg, flush=True)

# ----------------- Credentials & Config -----------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip("'").strip('"')
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Model Fallback Cascade (tries each sequentially if 429 quota or errors occur)
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.0-flash"
]

app = FastAPI()

# ----------------- MongoDB Setup -----------------
incoming_col = None
outgoing_col = None

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    mongo_client.admin.command("ping")
    db = mongo_client["resq_net_db"]
    incoming_col = db["incoming_messages"]
    outgoing_col = db["outgoing_replies"]
    log("[*] MongoDB connected successfully.")
except Exception as e:
    log(f"[!] Warning: MongoDB connection failed: {e}")


def is_valid_mobile_number(number: str) -> bool:
    """Filter out operator tags like AA-AIRTEL-S."""
    clean = re.sub(r"[\s\-\(\)\+]", "", str(number))
    return clean.isdigit() and len(clean) >= 7


def save_incoming_to_db(msg_id: str, sender: str, query: str):
    if incoming_col is not None:
        try:
            incoming_col.insert_one({
                "_id": msg_id,
                "message_id": msg_id,
                "sender": sender,
                "query": query,
                "created_at": datetime.now(timezone.utc)
            })
            log(f"[DB] Saved incoming message {msg_id}")
        except Exception as e:
            log(f"[DB ERROR] Failed to save incoming message: {e}")


def save_outgoing_to_db(reply_id: str, ref_msg_id: str, recipient: str, reply_text: str, status: str):
    if outgoing_col is not None:
        try:
            outgoing_col.insert_one({
                "_id": reply_id,
                "reply_id": reply_id,
                "reference_message_id": ref_msg_id,
                "recipient": recipient,
                "reply_text": reply_text,
                "status": status,
                "created_at": datetime.now(timezone.utc)
            })
            log(f"[DB] Saved outgoing reply {reply_id}")
        except Exception as e:
            log(f"[DB ERROR] Failed to save outgoing reply: {e}")

from google import genai
def ask_gemini_with_fallback(user_query: str) -> str:
    prompt = (
            "You are an emergency disaster SMS assistant. Answer the user query directly, concisely, and factually. "
            "Do NOT use markdown, asterisks, bullet points, or emojis. "
            "Keep the entire response under 150 characters so it fits in a single standard SMS message.\n\n"
            f"User Query: {user_query}"
        )

    client = genai.Client(api_key=GEMINI_API_KEY)

    interaction = client.interactions.create(
        model="gemini-2.5-flash",
        input=prompt
    )
    return interaction.output_text
    
    
    
    
    


def send_sms(recipient: str, text: str) -> bool:
    """Dispatches reply via SMS-Gate API."""
    auth = (GATEWAY_USERNAME, GATEWAY_PASSWORD)
    payload = {
        "deviceId": DEVICE_ID,
        "phoneNumbers": [recipient],
        "message": text
    }
    try:
        log(f"[GATEWAY] Sending outbound SMS to {recipient}...")
        res = requests.post(GATEWAY_API_URL, json=payload, auth=auth, timeout=12)
        if res.status_code in (200, 201, 202):
            log(f"[SMS DELIVERED] Dispatched successfully to {recipient}")
            return True
        else:
            log(f"[GATEWAY ERROR] HTTP {res.status_code}: {res.text}")
            return False
    except Exception as e:
        log(f"[GATEWAY EXCEPTION]: {e}")
        return False


def handle_sms_pipeline(message_id: str, sender: str, raw_text: str):
    """Executes synchronously in background thread: clean -> AI -> dispatch -> DB update."""
    try:
        clean_query = raw_text
        if clean_query.upper().startswith("ENET"):
            clean_query = clean_query[4:].strip()

        if not clean_query:
            clean_query = "Emergency assistance needed"

        log(f"\n==========================================")
        log(f"[PIPELINE START] ID: {message_id} | Sender: {sender} | Query: '{clean_query}'")

        # 1. Ask Gemini (with fallback cascade)
        reply = ask_gemini_with_fallback(clean_query)
        log(f"[REPLY GENERATED]: {reply}")

        # 2. Dispatch SMS back to sender
        sms_sent = send_sms(sender, reply)
        status_label = "SENT" if sms_sent else "FAILED"

        # 3. Save outgoing reply in MongoDB
        reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
        save_outgoing_to_db(reply_id, message_id, sender, reply, status_label)
        log(f"[PIPELINE COMPLETE] Task for {message_id} finished.")
        log(f"==========================================\n")
    except Exception as err:
        log(f"[PIPELINE CRITICAL ERROR]: {err}")


@app.post("/sms-webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    content_type = request.headers.get("content-type", "")
    sender = ""
    message_text = ""

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
        payload = data.get("payload") or data
        message_text = payload.get("message") or payload.get("text") or payload.get("body") or ""
        sender = (
            payload.get("sender")
            or payload.get("phone_number")
            or payload.get("from")
            or payload.get("senderNumber")
            or ""
        )
    else:
        form = await request.form()
        message_text = form.get("Body") or form.get("message") or ""
        sender = form.get("From") or form.get("from") or ""

    sender = str(sender).strip()
    message_text = str(message_text).strip()

    # Ignore operator broadcast messages (e.g., AA-AIRTEL-S)
    if not is_valid_mobile_number(sender):
        log(f"[IGNORED] Non-mobile sender: '{sender}'")
        return {"status": "ignored", "reason": "Operator SMS"}

    # 1. Save incoming message to MongoDB
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"
    save_incoming_to_db(message_id, sender, message_text)

    # 2. Add to background worker
    background_tasks.add_task(handle_sms_pipeline, message_id, sender, message_text)

    return {"status": "accepted", "message_id": message_id}


@app.get("/")
@app.head("/")
async def root():
    return {
        "service": "ResQ-Net Gateway",
        "status": "online",
        "mongo_connected": incoming_col is not None
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)