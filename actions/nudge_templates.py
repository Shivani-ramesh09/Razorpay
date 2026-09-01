"""
actions/nudge_templates.py
────────────────────────────────────────────────────────────────────────────────
Hinglish message templates for promise_to_pay_nudge actions.

Provides channel-specific templates (WhatsApp and SMS) parameterized by:
    - customer_name
    - merchant_name
    - amount (formatted in ₹)
    - short_url

Tone Guidelines:
    - Respectful, helpful, conversational Hinglish.
    - Not aggressive or coercive collections language.
    - Clear 1-click call-to-action link.
"""

from __future__ import annotations

import re
from typing import Dict

# ── Templates ─────────────────────────────────────────────────────────────────

TEMPLATES: Dict[str, str] = {
    # WhatsApp: Friendlier, emoji-supported, slightly longer context
    "whatsapp": (
        "Namaste {customer_name} ji 🙏\n\n"
        "Aapka {merchant_name} subscription (₹{amount}) ka auto-pay debit kisi kaaran se complete nahi ho paya.\n\n"
        "Services uninterrupted rakhne ke liye, kripya niche diye link par click karke payment complete karein ya apni preferred date choose karein:\n"
        "👉 {short_url}\n\n"
        "Kisi bhi query ke liye aap yahan reply kar sakte hain. Dhanyawad!"
    ),
    # SMS: Concise, fits within single/standard SMS segment, direct CTA
    "sms": (
        "Namaste {customer_name}, aapka {merchant_name} subscription (₹{amount}) debit nahi ho paya. "
        "Ek click mein pay karein ya date choose karein: {short_url} - Razorpay"
    ),
    # Fallback for email or other channels
    "default": (
        "Namaste {customer_name}, your {merchant_name} subscription payment of ₹{amount} could not be processed. "
        "Please complete payment or select a retry date using this secure link: {short_url}"
    ),
}


def render_nudge_message(
    channel: str,
    customer_name: str = "Customer",
    merchant_name: str = "Razorpay Merchant",
    amount_in_rupees: float = 0.0,
    short_url: str = "https://rzp.io/i/recovery",
) -> str:
    """
    Render a personalized Hinglish nudge message for the given channel.

    Parameters
    ----------
    channel : str
        Target delivery channel ('whatsapp', 'sms', etc.)
    customer_name : str
        Customer display name
    merchant_name : str
        Merchant or subscription brand name
    amount_in_rupees : float
        Amount in Rupees (e.g. 999.00)
    short_url : str
        Direct 1-click payment / promise-to-pay URL
    """
    ch_key = channel.lower().strip() if channel else "default"
    template = TEMPLATES.get(ch_key, TEMPLATES["default"])

    formatted_amount = f"{amount_in_rupees:,.2f}".rstrip("0").rstrip(".") if amount_in_rupees % 1 == 0 else f"{amount_in_rupees:,.2f}"

    return template.format(
        customer_name=customer_name.strip() or "Customer",
        merchant_name=merchant_name.strip() or "Merchant",
        amount=formatted_amount,
        short_url=short_url.strip(),
    )
