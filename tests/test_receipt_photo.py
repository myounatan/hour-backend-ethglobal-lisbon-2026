"""Unit tests for the photo pipeline's pure parts: what an upload must be, and who reads it.

Nothing here touches a network or a database. Every case stops before
:meth:`RewardService.submit_receipt` is reached, since a photo that got as far as being read is
the host app's ``test_reward_receipts.py`` to prove -- same split as ``test_zg.py``.
"""

from uuid import uuid4

import pytest

from hour_rewards.receipt_photo import (
    MAX_RECEIPT_IMAGE_BYTES,
    ReceiptImageTooLargeError,
    ReceiptScanError,
    UnsupportedReceiptImageError,
    configure_receipt_reader,
    get_receipt_reader,
    sniff_image_content_type,
    submit_receipt_photo,
    validate_receipt_image,
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00\x00\x10\x00" + b"WEBP" + b"\x00" * 16


@pytest.fixture(autouse=True)
def no_reader():
    """Leave the seam empty unless a test fills it, and never leak a reader."""
    configure_receipt_reader(None)
    yield
    configure_receipt_reader(None)


async def submit(image: bytes):
    return await submit_receipt_photo(
        None,  # type: ignore[arg-type] -- unreachable in these cases
        user_id=uuid4(),
        venue_id=uuid4(),
        image=image,
    )


def test_image_types_are_taken_from_the_bytes_themselves():
    assert sniff_image_content_type(JPEG) == "image/jpeg"
    assert sniff_image_content_type(PNG) == "image/png"
    assert sniff_image_content_type(WEBP) == "image/webp"


def test_anything_else_is_not_an_image():
    assert sniff_image_content_type(b"") is None
    assert sniff_image_content_type(b"%PDF-1.7 not a photo") is None
    # A truncated header is unreadable even when it started out right.
    assert sniff_image_content_type(b"\xff\xd8\xff") is None


def test_a_supported_photo_validates_to_its_type():
    assert validate_receipt_image(JPEG) == "image/jpeg"


def test_an_oversized_photo_is_refused_before_it_is_read():
    with pytest.raises(ReceiptImageTooLargeError):
        validate_receipt_image(JPEG + b"\x00" * MAX_RECEIPT_IMAGE_BYTES)


def test_a_file_that_is_not_a_photo_is_refused_before_it_is_read():
    with pytest.raises(UnsupportedReceiptImageError):
        validate_receipt_image(b"receipt total 48.60")


def test_the_reader_is_whatever_the_host_installed():
    async def reader(_: bytes) -> str:
        return "TOTAL 48.60"

    configure_receipt_reader(reader)
    assert get_receipt_reader() is reader


@pytest.mark.asyncio
async def test_a_photo_cannot_be_submitted_while_no_reader_is_configured():
    with pytest.raises(ReceiptScanError):
        await submit(JPEG)


@pytest.mark.asyncio
async def test_ocr_failing_is_an_outage_and_not_a_verdict():
    async def broken(_: bytes) -> str:
        raise RuntimeError("Azure said no")

    configure_receipt_reader(broken)
    with pytest.raises(ReceiptScanError, match="Azure said no"):
        await submit(JPEG)


@pytest.mark.asyncio
async def test_a_reader_may_raise_its_own_scan_error_untouched():
    async def unconfigured(_: bytes) -> str:
        raise ReceiptScanError("Receipt OCR is not configured")

    configure_receipt_reader(unconfigured)
    with pytest.raises(ReceiptScanError, match="not configured"):
        await submit(JPEG)


@pytest.mark.asyncio
async def test_an_unreadable_upload_never_reaches_the_reader():
    async def explode(_: bytes) -> str:
        raise AssertionError("should not be asked to read a non-photo")

    configure_receipt_reader(explode)
    with pytest.raises(UnsupportedReceiptImageError):
        await submit(b"not a photo at all")
