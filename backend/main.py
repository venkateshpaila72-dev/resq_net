import os
import csv
import re
import uuid
from datetime import datetime
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from google import genai
from google.genai import types

# ----------------- Configuration & Env Setup -----------------
load_dotenv()

# Ensure this is an AI Studio API key starting with 'AIzaSy...'
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY").strip()
GATEWAY_USERNAME = os.getenv("GATEWAY_USERNAME")
GATEWAY_PASSWORD = os.getenv("GATEWAY_PASSWORD")
DEVICE_ID = os.getenv("DEVICE_ID")
GATEWAY_API_URL = "https://api.sms-gate.app/3rdparty/v1/messages"

# Initialize Client
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
    """Queries Gemini using the official models.generate_content API."""
    system_instruction = (
        "You are an offline emergency assistant. Answer the user's question directly, accurately, and concisely. "
        "Do not use markdown formatting, bold text, asterisks, or bullet points. "
        "Keep the entire response under 150 characters so it fits inside a single standard SMS."
    )

    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]

    for model_name in models_to_try:
        try:
            print(f"[GEMINI] Sending query to {model_name}: '{user_prompt}'")
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    max_output_tokens=100
                )
            )

            if response and response.text:
                clean_text = response.text.replace("*", "").replace("#", "").strip()
                return clean_text[:160]

        except Exception as exc:
            print(f"[GEMINI ERROR on {model_name}]: {exc}")
            continue

    # Fallback if API keys fail, quota is exceeded (429), or network is unreachable
    q = user_prompt.lower()
    if any(k in q for k in ["fire", "smoke", "flame"]):
        return "ResQ-Net: Evacuate immediately. Crawl low under smoke. Feel doors before opening. Once outside, call emergency services."
    if any(k in q for k in ["burn", "blister", "cracker"]):
        return "ResQ-Net: Cool burn under clean running water for 10-20 mins. Do not use ice or oil. Cover with clean cloth."
    if any(k in q for k in ["flood", "water"]):
        return "ResQ-Net: Move to higher ground. Disconnect main power grid. Avoid walking or driving through floodwater."

    return "ResQ-Net Alert: Move to a safe, elevated location. Conserve battery. Emergency response is monitoring."


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


def process_and_reply_task(message_id: str, sender: str, clean_prompt: str):
    """Background task to query Gemini and dispatch the reply without delaying the webhook."""
    # 1. Fetch Gemini Answer
    ai_answer = ask_gemini(clean_prompt)
    print(f"[AI RESPONSE]: {ai_answer}")

    # 2. Dispatch SMS back to sender
    reply_id = f"REP_{uuid.uuid4().hex[:8].upper()}"
    sms_sent = send_sms_via_gateway(sender, ai_answer)
    status_label = "SENT" if sms_sent else "FAILED"

    # 3. Log reply to CSV
    reply_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_outgoing_reply(reply_id, message_id, reply_timestamp, sender, ai_answer, status_label)


# ----------------- Webhook Route -----------------
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

    # 4. Offload AI query & outbound dispatch to background
    background_tasks.add_task(process_and_reply_task, message_id, sender, clean_prompt)

    return {
        "status": "accepted",
        "message_id": message_id
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)