import os
import re
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
from groq import Groq

# ----------------- Environment Setup -----------------
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

def log(msg: str):
    """Ensure instant console output in terminals and Render logs."""
    print(msg, flush=True)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip().strip("'").strip('"')
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME", "WSBOOB")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD", "6zj_aqgr_wthfy")
DEVICE_ID = os.getenv("DEVICE_ID", "2x9j6oWbTb46UJwgX1_KA")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Initialize official Groq client
groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        log("[*] Groq client initialized successfully.")
    except Exception as e:
        log(f"[!] Warning: Failed to initialize Groq client: {e}")
else:
    log("[!] Warning: GROQ_API_KEY is not defined in .env.")

# ----------------- FastAPI & CORS -----------------
app = FastAPI(title="ResQ-Net SMS & Groq Gateway")

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
    log(f"[!] Warning: MongoDB connection failed: {e}")
    incoming_col = None
    outgoing_col = None


# ----------------- Helper Functions -----------------
def is_valid_mobile_number(number: str) -> bool:
    """Filter out non-numeric service/operator IDs like AA-AIRTEL-S."""
    clean = re.sub(r"[\s\-\(\)\+]", "", str(number))
    return clean.isdigit() and len(clean) >= 7


def save_incoming(msg_id: str, sender: str, query: str):
    """Stores incoming record to MongoDB."""
    if incoming_col is not None:
        try:
            incoming_col.insert_one({
                "_id": msg_id,
                "message_id": msg_id,
                "sender": sender,
                "query": query,
                "created_at": datetime.now(timezone.utc)
            })
            log(f"[DB] Inserted incoming message: {msg_id}")
        except PyMongoError as pe:
            log(f"[DB ERROR] Failed to save incoming message: {pe}")


def save_outgoing(reply_id: str, ref_msg_id: str, recipient: str, reply_text: str, status: str):
    """Stores outgoing reply record to MongoDB."""
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
            log(f"[DB] Inserted outgoing reply: {reply_id}")
        except PyMongoError as pe:
            log(f"[DB ERROR] Failed to save outgoing reply: {pe}")


def call_groq(prompt: str) -> str:
    """Sends prompt directly to Groq model without fallback strings."""
    if groq_client is None:
        log("[AI ERROR] Groq client not initialized.")
        return ""

    system_instruction = (
        "hey your are an helpul assistant . helper in emergence and if the situation is seveare then give the respected rescuse centers . each response should be points ."
    )

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": prompt}
    ]

    try:
        log(f"[AI PIPELINE] Querying Groq model '{GROQ_MODEL}' with: '{prompt}'")
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=250,
            temperature=0.2
        )

        message_obj = response.choices[0].message
        raw_text = message_obj.content or getattr(message_obj, "reasoning", "") or ""
        clean_text = raw_text.replace("*", "").replace("#", "").strip()
        log(f"[AI SUCCESS] Groq replied: {clean_text}")
        return clean_text

    except Exception as exc:
        log(f"[AI ERROR] Groq call failed: {exc}")
        return ""


def send_sms_via_gateway(recipient: str, message: str) -> bool:
    """Dispatches outbound SMS back to recipient via SMS-Gate API."""
    if not message:
        log("[GATEWAY ERROR] Empty message received. Skipping SMS dispatch.")
        return False

    auth = (GATEWAY_USERNAME, GATEWAY_PASSWORD)
    payload = {
        "deviceId": DEVICE_ID,
        "phoneNumbers": [str(recipient).strip()],
        "textMessage": {
            "text": message,
            "Text": message
        }
    }

    try:
        log(f"[GATEWAY] Sending outbound SMS to {recipient} ({len(message)} chars)...")
        response = requests.post(
            GATEWAY_API_URL,
            json=payload,
            auth=auth,
            headers={"Content-Type": "application/json"},
            timeout=12
        )
        if response.status_code in (200, 201, 202):
            log(f"[SMS DISPATCHED] Response delivered to {recipient}")
            return True
        else:
            log(f"[SMS GATEWAY ERROR] HTTP {response.status_code}: {response.text}")
            return False
    except Exception as exc:
        log(f"[SMS GATEWAY EXCEPTION] {exc}")
        return False


def process_query_in_background(message_id: str, sender: str, prompt: str):
    """Background task: Calls Groq directly, sends SMS, and saves to MongoDB."""
    try:
        log(f"\n==========================================")
        log(f"[PIPELINE START] ID: {message_id} | Sender: {sender} | Prompt: '{prompt}'")

        # 1. Ask Groq
        ai_answer = call_groq(prompt)

        # 2. Dispatch SMS
        reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
        sms_sent = False
        if ai_answer:
            sms_sent = send_sms_via_gateway(sender, ai_answer)
        status_label = "SENT" if sms_sent else "FAILED"

        # 3. Log to MongoDB
        save_outgoing(reply_id, message_id, sender, ai_answer, status_label)
        log(f"[PIPELINE COMPLETE] Task {message_id} finished with status: {status_label}")
        log(f"==========================================\n")
    except Exception as err:
        log(f"[PIPELINE CRITICAL ERROR]: {err}")


# ----------------- API Endpoints -----------------
@app.get("/")
@app.head("/")
async def root():
    return {
        "service": "ResQ-Net SMS & Groq AI Gateway",
        "status": "online",
        "mongo_connected": incoming_col is not None,
        "groq_ready": groq_client is not None
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

    # Filter out telecommunication carrier notices
    if not is_valid_mobile_number(sender):
        log(f"[SKIPPED AUTO-REPLY] Sender '{sender}' is not a mobile number.")
        return {"status": "saved", "note": "Operator message ignored"}

    # STRICT CHECK: Message MUST start with ENET (case-insensitive)
    if not re.match(r"^ENET\b", cleaned_query, flags=re.IGNORECASE):
        log(f"[SKIPPED] Message does not start with 'ENET': '{cleaned_query}'")
        return {"status": "ignored", "reason": "Message does not start with ENET"}

    # Strip the leading ENET keyword
    clean_prompt = re.sub(r"^ENET\s*", "", cleaned_query, flags=re.IGNORECASE).strip()

    # Save to MongoDB
    message_id = f"MSG_{uuid.uuid4().hex[:8].upper()}"
    save_incoming(message_id, sender, cleaned_query)
    log(f"\n[{message_id}] Received from: {sender} | Prompt: {clean_prompt}")

    # Offload query & SMS dispatch to background task
    background_tasks.add_task(process_query_in_background, message_id, sender, clean_prompt)

    return {"status": "accepted", "message_id": message_id}


class BroadcastRequest(BaseModel):
    recipient: str
    message: str


@app.post("/api/broadcast")
async def send_manual_broadcast(item: BroadcastRequest):
    """Allows manual SMS dispatch directly from the dashboard."""
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"

    success = send_sms_via_gateway(item.recipient, item.message)
    status_label = "SENT" if success else "FAILED"

    save_outgoing(reply_id, "MANUAL_BROADCAST", item.recipient, item.message, status_label)
    return {"status": status_label, "reply_id": reply_id}


@app.get("/api/messages")
async def get_all_messages():
    """Returns recent messages directly from MongoDB."""
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
            log(f"[MONGODB READ ERROR] {e}")

    return {"source": "none", "incoming": [], "outgoing": []}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)