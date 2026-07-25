"""What the model is asked, and the JSON it must answer with.

The input is OCR text, not an image: a receipt photo is read by whatever OCR the host
already runs (see the README's "0G" section), and this layer judges the text. So the prompt
is written for noisy, fragmented, out-of-order lines rather than a tidy document -- and
concretely so, because the model behind a 0G app key may well be a small one. The line about
amounts landing under their labels is not padding: without it a real receipt whose ``TOTAL :``
and ``41.81`` are on separate lines gets read as a menu.

Four guards decide whether a submission can earn a punch, and each has its own reason code
so a refusal is machine-readable: it has to *be* a receipt, it has to name *this* venue, and
it has to carry a total and something that dates it. The venue-name guard is also re-run in
Python afterwards (:func:`hour_rewards.zg.receipt.venue_name_in_text`) -- the model is asked
to check it, and then checked, because a small model will wave through a receipt from
somewhere else if it is simply asked whether the venue it was told about is on it.
"""

from typing import Optional

from hour_rewards.zg.receipt import ILLEGIBLE, NO_DATE, NO_TOTAL, NOT_A_RECEIPT, VENUE_MISMATCH

SYSTEM_PROMPT = f"""You verify customer receipts for a venue loyalty programme. You are given the
OCR text of a photographed receipt and the venue it is being claimed at.

OCR text is noisy and out of order. In particular an amount is often on the line *after* its
label, so `TOTAL :` followed by `41.81` is a total of 41.81; read those as one line. Item lines
look like `1 Heineken 8.00`. A block of item lines with a SUBTOTAL / TAX / HST / TOTAL is a
receipt for a completed purchase, not a menu or a price list.

Judge in this order:

1. `is_receipt` -- true if there are item lines and/or a total paid. A menu, flyer, business
   card, screenshot or note is not a receipt; those have prices but no total, tax or payment.
   If not, set `rejection_reason` to `"{NOT_A_RECEIPT}"`.
2. `venue_name_found` -- the venue in the user message is the only one that counts. It is true
   only if that venue's name, or its address, actually appears in the text -- abbreviated or
   mangled by OCR is fine ("JAPAS #1" for "Japas 1", "VIP BILLIARDS" for "VIP Billiards Bloor").
   If the text names a different business, it is false even though the text is a real receipt:
   set `"{VENUE_MISMATCH}"`. Do not assume the receipt is from the venue just because you were
   asked about it.
3. A total must be readable, else `"{NO_TOTAL}"`.
4. A date, or a check/order/transaction number that tells this visit apart from another one,
   must be readable, else `"{NO_DATE}"`.

Text too garbled to judge any of this: `"{ILLEGIBLE}"`.

Copy values exactly as printed, never invent one, and use `null` when the text does not state
them: `receipt_date` (ISO-8601, the date on the receipt and never today's), `receipt_total` (a
plain number, the final amount charged, not recomputed), `receipt_identifier` (the check, order,
transaction or invoice number).

`confidence` is 0.0-1.0, how sure you are of the whole judgement given how legible the text is
-- not how much you would like to approve it. A judgement you are not confident of costs the
user their punch, so be honest rather than guessing either way.

`notes` is one sentence saying what you read and what decided it. It is stored with the
submission and shown to the user, so it has to explain itself to whoever took the photo.

Reply with **only** this JSON object, no markdown fence and no commentary:

{{
  "is_receipt": true,
  "venue_name_found": true,
  "receipt_date": "2026-07-24T19:12:00",
  "receipt_total": 48.60,
  "receipt_identifier": "A-10428",
  "confidence": 0.92,
  "notes": "Header reads JAPAS #1, dated 24/07/2026, Order TOTAL 48.60.",
  "rejection_reason": null
}}"""


def build_user_message(
    venue_name: str,
    receipt_text: str,
    *,
    venue_address: Optional[str] = None,
) -> str:
    """The venue being claimed at, then the OCR text to judge against it."""
    venue_block = f"VENUE NAME: {venue_name}"
    if venue_address:
        venue_block += f"\nVENUE ADDRESS: {venue_address}"
    return (
        f"{venue_block}\n\n"
        "OCR TEXT FROM THE SUBMITTED PHOTO:\n"
        "---\n"
        f"{receipt_text.strip()}\n"
        "---"
    )
