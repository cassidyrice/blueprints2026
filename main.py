#!/usr/bin/env python3
"""
Card Blueprint API
Handles payment → reading generation → email delivery
"""

import os
import json
import re
import time
import traceback
import html
import stripe
import resend
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
import pathlib
from pydantic import BaseModel, field_validator
from datetime import date
from google.oauth2 import service_account
from googleapiclient.discovery import build

from generate_reading import generate_reading

# --- Config ---
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
resend.api_key = os.environ["RESEND_API_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
STRIPE_PRICE_ID = os.environ["STRIPE_PRICE_ID"]
STRIPE_UPSELL_PRICE_ID = os.environ.get("STRIPE_UPSELL_PRICE_ID", STRIPE_PRICE_ID)
FROM_EMAIL = os.environ.get("FROM_EMAIL", "readings@cardblueprints.com")
BASE_URL = os.environ.get("BASE_URL", "https://cardblueprints.com").rstrip("/")
SUCCESS_URL = os.environ.get("SUCCESS_URL", f"{BASE_URL}/thank-you")
CANCEL_URL = os.environ.get("CANCEL_URL", BASE_URL)
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
GOOGLE_DOC_SHARE_MODE = os.environ.get("GOOGLE_DOC_SHARE_MODE", "customer").lower()
GOOGLE_DOC_FALLBACK_INLINE = os.environ.get("GOOGLE_DOC_FALLBACK_INLINE", "true").lower() == "true"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReadingRequest(BaseModel):
    email: str
    birth_month: int
    birth_day: int
    birth_year: int
    question: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v

    @field_validator("birth_month")
    @classmethod
    def validate_month(cls, v):
        if not 1 <= v <= 12:
            raise ValueError("Month must be 1-12")
        return v

    @field_validator("birth_day")
    @classmethod
    def validate_day(cls, v):
        if not 1 <= v <= 31:
            raise ValueError("Day must be 1-31")
        return v

    @field_validator("birth_year")
    @classmethod
    def validate_year(cls, v):
        if not 1900 <= v <= date.today().year:
            raise ValueError("Invalid birth year")
        return v

    @field_validator("question")
    @classmethod
    def validate_question(cls, v):
        v = v.strip()
        if len(v) < 5:
            raise ValueError("Question is too short")
        if len(v) > 2000:
            raise ValueError("Question is too long")
        return v


@app.get("/thank-you", response_class=HTMLResponse)
def thank_you():
    return pathlib.Path("thank-you.html").read_text()


@app.get("/", response_class=HTMLResponse)
def index():
    return pathlib.Path("index.html").read_text()


@app.get("/health")
def health():
    return {"status": "ok"}


def _success_url(extra_query: str = "") -> str:
    separator = "&" if "?" in SUCCESS_URL else "?"
    return f"{SUCCESS_URL}{separator}session_id={{CHECKOUT_SESSION_ID}}{extra_query}"


@app.post("/create-checkout")
async def create_checkout(req: ReadingRequest):
    """Create a Stripe checkout session with reading details in metadata."""
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="payment",
            customer_email=req.email,
            success_url=_success_url(),
            cancel_url=CANCEL_URL,
            metadata={
                "email": req.email,
                "birth_month": str(req.birth_month),
                "birth_day": str(req.birth_day),
                "birth_year": str(req.birth_year),
                "question": req.question,
            },
        )
        return {"checkout_url": session.url}
    except Exception as e:
        print(f"Checkout error: {e}")
        raise HTTPException(status_code=500, detail="Unable to create checkout session")


@app.post("/create-upsell-checkout")
async def create_upsell_checkout(request: Request):
    """Create a follow-up reading checkout from an existing paid session."""
    try:
        body = await request.json()
        session_id = (body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="Missing session_id")

        original = stripe.checkout.Session.retrieve(session_id)
        metadata = original.get("metadata") or {}
        email = original.get("customer_email") or metadata.get("email")

        if not email:
            raise HTTPException(status_code=400, detail="Original session is missing an email")

        upsell_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_UPSELL_PRICE_ID, "quantity": 1}],
            mode="payment",
            customer_email=email,
            success_url=_success_url("&upsell=1"),
            cancel_url=f"{SUCCESS_URL}?session_id={html.escape(session_id)}",
            metadata={
                "email": email,
                "birth_month": str(metadata.get("birth_month", "")),
                "birth_day": str(metadata.get("birth_day", "")),
                "birth_year": str(metadata.get("birth_year", "")),
                "question": "What should I focus on over the next 90 days?",
                "offer": "year-ahead-follow-up",
                "source_session_id": session_id,
            },
        )
        return {"checkout_url": upsell_session.url}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Upsell checkout error: {e}")
        raise HTTPException(status_code=500, detail="Unable to create upsell checkout")


@app.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe calls this after payment. Generate reading and send email."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata") or {}

        email = meta.get("email")
        question = meta.get("question")
        month = _safe_int(meta.get("birth_month"))
        day = _safe_int(meta.get("birth_day"))
        year = _safe_int(meta.get("birth_year"))

        if not all([email, question, month, day, year]):
            print(f"MISSING METADATA — paid but no reading sent. email={email} month={month} day={day} year={year}")
            return JSONResponse({"status": "missing metadata"}, status_code=200)

        print(f"Generating reading for {email}, {month}/{day}/{year}")
        try:
            reading = _retry_operation(
                "reading generation",
                lambda: generate_reading(month, day, year, question),
            )
            doc_url = None
            try:
                doc_url = _retry_operation(
                    "google doc creation",
                    lambda: _create_reading_doc(email, month, day, year, question, reading),
                )
            except Exception as doc_error:
                print(f"GOOGLE DOC CREATION FAILED for {email}: {doc_error}")
                if not GOOGLE_DOC_FALLBACK_INLINE:
                    raise

            print(f"Reading generated, sending email to {email}")
            _retry_operation(
                "email delivery",
                lambda: _send_reading_email(email, question, reading=reading, doc_url=doc_url),
            )
            print(f"Email sent successfully to {email}")
        except Exception as e:
            print(f"FAILED TO DELIVER READING — customer paid but got nothing. email={email} error={e}")
            print(traceback.format_exc())
            # Send a fallback notification to the business owner
            try:
                resend.Emails.send({
                    "from": FROM_EMAIL,
                    "to": FROM_EMAIL,
                    "subject": f"FAILED READING: {email}",
                    "html": f"<p>Payment received but reading failed for <b>{email}</b>.</p>"
                           f"<p>Birthday: {month}/{day}/{year}</p>"
                           f"<p>Question: {question}</p>"
                           f"<p>Error: {e}</p>",
                })
            except Exception:
                print(f"ALERT EMAIL ALSO FAILED for {email}")
            return JSONResponse({"status": "retry"}, status_code=500)

    return JSONResponse({"status": "ok"})


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _retry_operation(name: str, fn, attempts: int = 3, base_delay: float = 0.75):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            print(f"{name.upper()} FAILED attempt {attempt}/{attempts}: {exc}")
            if attempt < attempts:
                time.sleep(base_delay * attempt)
    raise last_error


def _clean_reading(text: str) -> str:
    """Strip markdown artifacts and convert to clean HTML paragraphs."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'^#{1,4}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'---+', '', text)
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    return ''.join(f'<p style="margin: 0 0 20px 0;">{p}</p>' for p in paragraphs)


def _google_credentials():
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        return service_account.Credentials.from_service_account_info(
            json.loads(GOOGLE_SERVICE_ACCOUNT_JSON),
            scopes=GOOGLE_SCOPES,
        )
    if GOOGLE_SERVICE_ACCOUNT_FILE:
        return service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=GOOGLE_SCOPES,
        )
    raise RuntimeError("Google Docs credentials are not configured")


def _google_services():
    credentials = _google_credentials()
    return (
        build("docs", "v1", credentials=credentials, cache_discovery=False),
        build("drive", "v3", credentials=credentials, cache_discovery=False),
    )


def _doc_text(month: int, day: int, year: int, question: str, reading: str) -> str:
    return (
        "Cardology Reading\n\n"
        f"Birth date: {month:02d}/{day:02d}/{year}\n"
        f"Question: {question}\n\n"
        f"{reading.strip()}\n"
    )


def _create_reading_doc(to_email: str, month: int, day: int, year: int, question: str, reading: str) -> str:
    docs_service, drive_service = _google_services()
    title = f"Card Blueprints Reading - {to_email} - {month:02d}-{day:02d}-{year}"
    created = docs_service.documents().create(body={"title": title}).execute()
    document_id = created["documentId"]

    docs_service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": _doc_text(month, day, year, question, reading),
                    }
                }
            ]
        },
    ).execute()

    if GOOGLE_DRIVE_FOLDER_ID:
        drive_service.files().update(
            fileId=document_id,
            addParents=GOOGLE_DRIVE_FOLDER_ID,
            fields="id,webViewLink",
        ).execute()

    if GOOGLE_DOC_SHARE_MODE == "public":
        permission = {"type": "anyone", "role": "reader"}
    else:
        permission = {"type": "user", "role": "reader", "emailAddress": to_email}

    drive_service.permissions().create(
        fileId=document_id,
        body=permission,
        sendNotificationEmail=False,
    ).execute()

    file_data = drive_service.files().get(fileId=document_id, fields="webViewLink").execute()
    return file_data["webViewLink"]


def _send_reading_email(to_email: str, question: str, reading: str, doc_url: str | None = None):
    """Send the completed reading via Resend."""
    escaped_question = html.escape(question)
    if doc_url:
        body_html = (
            '<p style="margin: 0 0 18px 0;">Your reading is ready.</p>'
            f'<p style="margin: 0 0 18px 0;"><a href="{html.escape(doc_url)}" style="color: #0a0a0a; background: #c8a96e; text-decoration: none; padding: 12px 18px; border-radius: 999px; display: inline-block; font-weight: bold;">Open your reading</a></p>'
            '<p style="margin: 18px 0 0 0; color: #666;">If the button does not work, reply to this email and we will resend it.</p>'
        )
        text_body = f"Your reading is ready.\n\nOpen it here: {doc_url}\n\nQuestion: {question}"
    else:
        body_html = _clean_reading(reading)
        text_body = f"Your Cardology Reading\n\nQuestion: {question}\n\n{reading.strip()}"

    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": "Your Cardology Reading",
        "text": text_body,
        "html": f"""
        <div style="margin: 0; padding: 24px; background: #f4efe6; font-family: Georgia, serif; color: #17130d;">
            <div style="max-width: 680px; margin: 0 auto; background: #fffdfa; border: 1px solid #eadfcd; border-radius: 18px; overflow: hidden;">
                <div style="padding: 28px 28px 20px; background: #111; color: #f5ede0;">
                    <div style="font-size: 12px; letter-spacing: 3px; text-transform: uppercase; color: #c8a96e; margin-bottom: 12px;">Card Blueprint</div>
                    <h2 style="font-size: 28px; margin: 0 0 10px 0; font-weight: normal;">Your reading is ready</h2>
                    <p style="margin: 0; color: #cabda8; line-height: 1.6;">A personalized cardology reading based on your birthday and question.</p>
                </div>
                <div style="padding: 24px 28px;">
                    <div style="margin: 0 0 24px 0; padding: 16px 18px; background: #f7f1e6; border-radius: 12px; border: 1px solid #eadfcd;">
                        <div style="font-size: 11px; letter-spacing: 1.8px; text-transform: uppercase; color: #8a7250; margin-bottom: 8px;">Your Question</div>
                        <div style="font-size: 16px; line-height: 1.7;">{escaped_question}</div>
                    </div>
                    <div style="line-height: 1.8; font-size: 16px;">{body_html}</div>
                </div>
                <div style="padding: 18px 28px 28px; color: #7d6a53; font-size: 13px; line-height: 1.6;">
                    You can reply to this email if you need your reading re-sent.
                </div>
            </div>
        </div>
        """,
    })
