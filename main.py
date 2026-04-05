#!/usr/bin/env python3
"""
Card Blueprint API
Handles payment → reading generation → email delivery
"""

import os
import json
import stripe
import resend
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import date

from generate_reading import generate_reading

# --- Config ---
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
resend.api_key = os.environ["RESEND_API_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "price_1TIq5bDgoKThmC0IOtzLQMJd")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "readings@cardblueprint.com")
SUCCESS_URL = os.environ.get("SUCCESS_URL", "https://cardblueprint.com/thank-you")

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
            cancel_url="https://cardblueprint.com",
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe calls this after payment. Generate reading and send email."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata", {})

        email = meta.get("email")
        question = meta.get("question")
        month = int(meta.get("birth_month", 0))
        day = int(meta.get("birth_day", 0))
        year = int(meta.get("birth_year", 0))

        if not all([email, question, month, day, year]):
            return JSONResponse({"status": "missing metadata"}, status_code=200)

        try:
            reading = generate_reading(month, day, year, question)
            _send_reading_email(email, question, reading)
        except Exception as e:
            # Log but return 200 so Stripe doesn't retry endlessly
            print(f"Reading generation error: {e}")

    return JSONResponse({"status": "ok"})


def _send_reading_email(to_email: str, question: str, reading: str):
    """Send the completed reading via Resend."""
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
            <div style="line-height: 1.8; font-size: 16px; white-space: pre-wrap;">{reading}</div>
            <p style="margin-top: 48px; font-size: 13px; color: #999; border-top: 1px solid #eee; padding-top: 16px;">
                cardblueprint.com
            </p>
        </div>
        """,
    })
