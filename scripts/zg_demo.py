"""Judge one receipt on 0G Compute, with no database, and show what came back.

Runs exactly what a punch runs (:func:`hour_rewards.zg.verify_receipt`), so this is the
fastest way to see the guards fire and to iterate on the prompt: feed it a real receipt's OCR
text and it prints the verdict, the extracted fields, and 0G's attestation of the run --
including the public URL where that inference's signature can be checked.

Usage::

    pip install -e "vendor/hour-rewards-sdk[zg]"
    export ZG_ROUTER_API_KEY=app-sk-...        # pc.0g.ai -> Dashboard -> Apps
    export ZG_ROUTER_BASE_URL=https://...      # the gateway that key was issued for
    python scripts/zg_demo.py --venue "Japas 1" --text-file receipt.txt
    ... | python scripts/zg_demo.py --venue "Japas 1"       # OCR text on stdin

Two things worth trying: the same receipt against a venue it isn't from (rejected,
`venue_mismatch`), and a menu instead of a receipt (rejected, `not_a_receipt`).
"""

import argparse
import asyncio
import os
import sys
from typing import Optional
from uuid import uuid4

from hour_rewards.zg import (
    ReceiptVerdict,
    ZGConfig,
    configure_zg,
    get_zg_config,
    quote_url,
    signature_url,
    venue_name_in_text,
    verify_receipt,
)

# Any receipt-shaped text works for a smoke test; a real photo's OCR is the point of --text-file.
SAMPLE_RECEIPT = """JAPAS #1
123 Dundas St W, Toronto
07/24/2026 19:12
Sapporo Draft        8.50
Chicken Karaage     12.00
Gyoza                9.50
Subtotal            30.00
Tax                  3.90
TOTAL               48.60
VISA ****4242
Check A-10428
"""
SAMPLE_VENUE = "Japas 1"


def _config(base_url: Optional[str]) -> ZGConfig:
    config = ZGConfig.build(
        api_key=os.environ.get("ZG_ROUTER_API_KEY"),
        base_url=base_url or os.environ.get("ZG_ROUTER_BASE_URL"),
        model=os.environ.get("ZG_ROUTER_MODEL"),
    )
    if config is None:
        sys.exit("Set ZG_ROUTER_API_KEY (create one at https://pc.0g.ai).")
    return config


def _receipt_text(text_file: Optional[str]) -> str:
    if text_file:
        with open(text_file, encoding="utf-8") as handle:
            return handle.read()
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    print("No --text-file and nothing piped in: using the built-in sample receipt.\n")
    return SAMPLE_RECEIPT


def _report(verdict: ReceiptVerdict, venue: str, receipt_text: str) -> None:
    print(f"\nStatus     {verdict.status.value}" + ("  (punch earned)" if verdict.approved else ""))
    if verdict.rejection_reason:
        print(f"Reason     {verdict.rejection_reason}")
    print(f"Confidence {verdict.confidence:.2f}")

    print("\nRead off the receipt:")
    print(f"   receipt?   {verdict.is_receipt}")
    print(f"   venue?     {verdict.venue_name_found}", end="")
    print(f"   (local guard: {venue_name_in_text(venue, receipt_text)})")
    print(f"   date       {verdict.receipt_date}")
    print(f"   total      {verdict.receipt_total}")
    print(f"   number     {verdict.receipt_identifier}")
    print(f"   dedupe     {verdict.dedupe_hash[:16]}...  (unique per venue)")
    if verdict.notes:
        print(f"\nModel's reasoning:\n   {verdict.notes}")

    print("\n0G trace (what makes the punch attributable):")
    print(f"   request    {verdict.zg_request_id or 'n/a'}")
    print(f"   provider   {verdict.zg_provider_address or 'n/a'}")
    print(f"   TEE        {verdict.zg_tee_verified}")
    if verdict.zg_request_id:
        # Both public, no API key: this is what a punch on Hedera lets anyone check.
        config = get_zg_config()
        if config is not None:
            print(f"   signature  {signature_url(config, verdict.zg_request_id)}")
            print(f"   quote      {quote_url(config)}")


async def demo(config: ZGConfig, venue: str, receipt_text: str) -> None:
    print(f"0G:    {config.base_url}\nModel:  {config.model}  (verify_tee={config.verify_tee})")
    print(f"Venue:  {venue}\nOCR:    {len(receipt_text)} chars")
    _report(await verify_receipt(uuid4(), venue, receipt_text), venue, receipt_text)


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", default=SAMPLE_VENUE, help="venue the receipt is claimed at")
    parser.add_argument("--text-file", help="file holding the receipt's OCR text")
    parser.add_argument(
        "--base-url",
        help="0G gateway to call, overriding $ZG_ROUTER_BASE_URL and the SDK's default",
    )
    args = parser.parse_args(argv)

    config = _config(args.base_url)
    configure_zg(config)
    asyncio.run(demo(config, args.venue, _receipt_text(args.text_file)))


if __name__ == "__main__":
    main()
