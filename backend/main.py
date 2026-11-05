import os
import re
import uuid
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from pymongo import MongoClient

load_dotenv()

# Environment credentials
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip("'").strip('"')
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

app = FastAPI()

# ----------------- MongoDB Connection -----------------
incoming_col = None
outgoing_col = None

try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    mongo_client.admin.command("ping")
    db = mongo_client["resq_net_db"]
    incoming_col = db["incoming_messages"]
    outgoing_col = db["outgoing_replies"]
    print("[*] MongoDB connected successfully.")
except Exception as e:
    print(f"[!] Warning: MongoDB connection failed: {e}")


def is_valid_mobile_number(number: str) -> bool:
    """Ignore telecom operator sender tags like AA-AIRTEL."""
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
            print(f"[DB] Saved incoming message {msg_id}")
        except Exception as e:
            print(f"[DB ERROR] Failed to save incoming message: {e}")


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
            print(f"[DB] Saved outgoing reply {reply_id}")
        except Exception as e:
            print(f"[DB ERROR] Failed to save outgoing reply: {e}")


def ask_gemini(user_query: str) -> str:
    """Send question to Gemini and get a clean text response under 160 characters for SMS."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = (
        "You are an emergency disaster SMS assistant. Answer the user query directly, concisely, and factually. "
        "Do NOT use markdown, asterisks, bullet points, or emojis. "
        "Keep the entire response under 150 characters so it fits in a single SMS message.\n\n"
        f"User Query: {user_query}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    try:
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=12)
        if res.status_code == 200:
            data = res.json()
            candidates = data.get("candidates", [])
            if candidates:
                raw_text = candidates[0]["content"]["parts"][0]["text"]
                clean_text = raw_text.replace("*", "").replace("#", "").strip()
                return clean_text[:160]
        else:
            print(f"[GEMINI ERROR] HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"[GEMINI EXCEPTION]: {e}")

    return "ResQ-Net: Stay calm and move to safety. Conserve phone battery. Rescue teams are monitoring."


def send_sms(recipient: str, text: str) -> bool:
    """Sends the response back to the sender's mobile number via SMS-Gate."""
    auth = (GATEWAY_USERNAME, GATEWAY_PASSWORD)
    payload = {
        "deviceId": DEVICE_ID,
        "phoneNumbers": [recipient],
        "message": text
    }
    try:
        res = requests.post(GATEWAY_API_URL, json=payload, auth=auth, timeout=12)
        if res.status_code in (200, 201, 202):
            print(f"[SMS SENT SUCCESS] Dispatched to {recipient}: {text}")
            return True
        else:
            print(f"[GATEWAY ERROR] HTTP {res.status_code}: {res.text}")
            return False
    except Exception as e:
        print(f"[GATEWAY EXCEPTION]: {e}")
        return False


def handle_sms_pipeline(message_id: str, sender: str, raw_text: str):
    """Clean query -> Query Gemini -> Send SMS -> Store in MongoDB."""
    clean_query = raw_text
    if clean_query.upper().startswith("ENET"):
        clean_query = clean_query[4:].strip()
    
    if not clean_query:
        clean_query = "Emergency assistance needed"

    print(f"\n[PROCESSING] From: {sender} | Query: {clean_query}")
    
    # 1. Ask Gemini
    reply = ask_gemini(clean_query)
    print(f"[GEMINI REPLY]: {reply}")

    # 2. Dispatch back to sender via SMS-Gate
    sms_sent = send_sms(sender, reply)
    status_label = "SENT" if sms_sent else "FAILED"

    # 3. Store outgoing response in MongoDB
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    save_outgoing_to_db(reply_id, message_id, sender, reply, status_label)


@app.post("/sms-webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    content_type = request.headers.get("content-type", "")
    sender = ""
    message_text = ""

    # Parse incoming payload from SMS Gate
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

    # Ignore operator broadcast messages (Airtel, Vi, Jio promos)
    if not is_valid_mobile_number(sender):
        return {"status": "ignored", "reason": "Operator SMS"}

    # 1. Store incoming SMS immediately in MongoDB
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"
    save_incoming_to_db(message_id, sender, message_text)

    # 2. Process Gemini & dispatch response in background
    background_tasks.add_task(handle_sms_pipeline, message_id, sender, message_text)

    return {"status": "ok", "message_id": message_id}


@app.get("/")
async def health():
    return {
        "status": "ResQ-Net relay is online",
        "mongo_connected": incoming_col is not None
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)