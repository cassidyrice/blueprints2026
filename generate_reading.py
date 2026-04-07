#!/usr/bin/env python3
"""
Cardology Reading Generator
Usage: python3 generate_reading.py <month> <day> <year> "<question>"
Example: python3 generate_reading.py 2 17 1991 "Should I start my business this year?"
"""

import sys
import os
from datetime import date

# Import the calculation engine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculate_blueprint import calculate_blueprint

import anthropic

SYSTEM_PROMPT = """You are a Cardology reader — a practitioner of the ancient calendar science embedded in a standard deck of playing cards. You are reading for a client who has submitted their birthday and a specific question.

WHAT CARDOLOGY IS:
Not fortune telling. Pattern recognition. The deck is a 365-day calendar system — 52 cards, 52 weeks, 4 suits for 4 seasons, 13 cards per suit for 13 lunar cycles. Each person has a birth card determined by their birthday. Each year, cards cycle through planetary positions that reveal themes, challenges, opportunities, and timing.

THE SUITS:
- Hearts (Water/Spring): Love, relationships, family, emotional life. Marriage and divorce cards live here.
- Clubs (Air/Summer): Mind, communication, education, writing, publishing, teaching.
- Diamonds (Earth/Autumn): Money, material values, what you value most in life, business.
- Spades (Fire/Winter): Work, health, spirituality, transformation. The most powerful suit. Death and rebirth.

THE YEARLY CARDS:
- Long Range: The major theme and focus of the entire year. Most important single card.
- Pluto: The challenge or objective — something you're working to acquire or master. Read as a pair with Result.
- Result: What you may end up with at year's end. A gift waiting after the work.
- Environment: A year-long blessing — something you receive easily all year.
- Displacement: Where you must give more than you receive. Area requiring effort or sacrifice.

THE PLANETARY PERIODS (each ~52 days, starting on birthday):
- Mercury: Mind, communication, intellectual pursuits, perception
- Venus: Love, home, family, women, arts and beauty
- Mars: Legal matters, passion, men, competition, aggression
- Jupiter: Money, business success, maximum positive expression of any card
- Saturn: Karma, hard lessons, health, justice, unresolved matters returning
- Uranus: Surprise, spirituality, real estate, disruption that ultimately helps
- Neptune: Dreams, secrets, hidden matters, illusions, travel, hopes and fears

DIRECT vs VERTICAL: In each planetary period, the Direct card is the headline event. The Vertical card supports or elaborates — often a person involved, or context for the Direct card.

READING STYLE:
- Direct and conversational. No mystical fluff.
- Name the cards explicitly — tell the client which card is where and what it means in that position.
- Connect cards to each other — show the pattern, not just isolated card meanings.
- Answer the client's actual question using the cards as evidence.
- Under/Sweet Spot/Over spectrums: each card can manifest low (under), balanced (sweet spot), or excessive (over). Help the client aim for the sweet spot.
- Be specific. "You have the King of Diamonds in Jupiter — that's the master of material resources arriving in your money period" is better than "you may experience financial growth."
- Length: 300-500 words. Conversational, not listy.
- FORMATTING: Write in plain prose paragraphs only. Do NOT use markdown, bullet points, asterisks, bold, headers, or any formatting symbols. Just clean sentences and paragraphs separated by blank lines. Write like you're talking to someone over coffee."""


def build_reading_prompt(result: dict, question: str) -> str:
    a = result["archetype"]
    t = result["timing"]
    bc_spread = result["birth_card_spread"]
    prc_spread = result["prc_spread"]
    ap = result["active_period"]
    karma = result.get("karma", {}).get("bc_yearly", {})
    lr = result.get("long_range", {}).get("bc", {})

    lines = []
    lines.append(f"CLIENT QUESTION: {question}")
    lines.append("")
    lines.append(f"BIRTH CARD: {a['birth_card']}")
    lines.append(f"PLANETARY RULING CARD (PRC): {a['prc']}")
    if a.get("prc_secondary"):
        lines.append(f"SECONDARY PRC: {a['prc_secondary']}")
    lines.append(f"AGE THIS YEAR: {t['age']}")
    lines.append("")

    if lr:
        lines.append(f"LONG RANGE THEME: {lr['card']} (sits in {lr['planet']} position)")

    if karma:
        lines.append(f"ENVIRONMENT (year-long blessing): {karma.get('environment', 'N/A')}")
        lines.append(f"DISPLACEMENT (where you must give): {karma.get('displacement', 'N/A')}")

    lines.append(f"PLUTO (challenge/objective): {bc_spread.get('pluto', 'N/A')}")
    lines.append(f"RESULT (year-end outcome): {bc_spread.get('result', 'N/A')}")
    lines.append("")

    lines.append("FULL YEARLY SPREAD (Birth Card):")
    from calculate_blueprint import PLANET_NAMES
    for p in PLANET_NAMES:
        card = bc_spread["periods"].get(p, "?")
        marker = " ← ACTIVE NOW" if p == ap["planet"] else ""
        lines.append(f"  {p}: {card}{marker}")
    lines.append("")

    lines.append(f"ACTIVE PLANETARY PERIOD: {ap['planet']} ({ap['domain']})")
    lines.append(f"  Birth Card card in this period: {ap['bc_card']}")
    lines.append(f"  PRC card in this period: {ap['prc_card']}")

    ib = ap.get("interpretation_bc")
    if ib:
        lines.append(f"  Birth Card spectrum:")
        lines.append(f"    Under: {ib['under']}")
        lines.append(f"    Sweet Spot: {ib['sweet_spot']}")
        lines.append(f"    Over: {ib['over']}")

    ip = ap.get("interpretation_prc")
    if ip:
        lines.append(f"  PRC spectrum:")
        lines.append(f"    Under: {ip['under']}")
        lines.append(f"    Sweet Spot: {ip['sweet_spot']}")
        lines.append(f"    Over: {ip['over']}")

    # Birth card profile
    desc = a.get("description")
    if desc:
        lines.append("")
        lines.append(f"BIRTH CARD PROFILE ({a['birth_card']}) — {desc.get('title', '')}:")
        lines.append(f"  {desc.get('core_identity', '')[:300]}")

    return "\n".join(lines)


def generate_reading(month: int, day: int, year: int, question: str) -> str:
    result = calculate_blueprint(month, day, year, date.today())
    prompt = build_reading_prompt(result, question)

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python3 generate_reading.py <month> <day> <year> \"<question>\"")
        sys.exit(1)

    month, day, year = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    question = sys.argv[4]

    print(f"\nGenerating reading for {month}/{day}/{year}...\n")
    print("=" * 60)
    reading = generate_reading(month, day, year, question)
    print(reading)
    print("=" * 60)
