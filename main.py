#!/usr/bin/env python3
"""
Card Blueprint API
Handles payment → reading generation → email delivery
"""

import os
import json
import re
import traceback
import stripe
import resend
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
import pathlib
from pydantic import BaseModel, field_validator
from datetime import date

from generate_reading import generate_reading

# --- Config ---
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
resend.api_key = os.environ["RESEND_API_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
STRIPE_PRICE_ID = os.environ["STRIPE_PRICE_ID"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "readings@cardblueprints.com")
SUCCESS_URL = os.environ.get("SUCCESS_URL", "https://web-production-e4017.up.railway.app/thank-you")
CANCEL_URL = os.environ.get("CANCEL_URL", "https://web-production-e4017.up.railway.app")

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


@app.post("/create-checkout")
async def create_checkout(req: ReadingRequest):
    """Create a Stripe checkout session with reading details in metadata."""
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="payment",
            customer_email=req.email,
            success_url=SUCCESS_URL,
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
        raw = json.loads(payload)
        meta = raw["data"]["object"].get("metadata") or {}

        email = meta.get("email")
        question = meta.get("question")
        month = int(meta.get("birth_month", 0))
        day = int(meta.get("birth_day", 0))
        year = int(meta.get("birth_year", 0))

        if not all([email, question, month, day, year]):
            print(f"MISSING METADATA — paid but no reading sent. email={email} month={month} day={day} year={year}")
            return JSONResponse({"status": "missing metadata"}, status_code=200)

        print(f"Generating reading for {email}, {month}/{day}/{year}")
        try:
            reading = generate_reading(month, day, year, question)
            print(f"Reading generated, sending email to {email}")
            _send_reading_email(email, question, reading)
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

    return JSONResponse({"status": "ok"})


def _clean_reading(text: str) -> str:
    """Strip markdown artifacts and convert to clean HTML paragraphs."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'^#{1,4}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'---+', '', text)
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    return ''.join(f'<p style="margin: 0 0 20px 0;">{p}</p>' for p in paragraphs)


def _send_reading_email(to_email: str, question: str, reading: str):
    """Send the completed reading via Resend."""
    reading_html = _clean_reading(reading)
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": "Your Cardology Reading",
        "html": f"""
        <div style="font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 40px 20px; color: #1a1a1a;">
            <h2 style="font-size: 22px; margin-bottom: 8px;">Your Cardology Reading</h2>
            <p style="color: #666; font-size: 14px; margin-bottom: 32px; border-bottom: 1px solid #eee; padding-bottom: 16px;">
                Question: <em>{question}</em>
            </p>
            <div style="line-height: 1.8; font-size: 16px;">{reading_html}</div>
            <p style="margin-top: 48px; font-size: 13px; color: #999; border-top: 1px solid #eee; padding-top: 16px;">
                cardblueprints.com
            </p>
        </div>
        """,
    })
