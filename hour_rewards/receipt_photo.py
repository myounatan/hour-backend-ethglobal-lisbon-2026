"""The photo half of a punch: an uploaded image in, a verdict out.

:meth:`hour_rewards.service.RewardService.submit_receipt` starts from text, because judging a
receipt is this package's business and reading pixels is not. What sits above it -- what an
upload has to be to be worth reading, and what happens to the text once it exists -- is this
package's business too, so it lives here rather than in each host's route handler.

The reading itself is the one step handed back: a host calls :func:`configure_receipt_reader`
once at startup with a coroutine that turns bytes into text, exactly as it configures the other
two layers (:func:`hour_rewards.zg.configure_zg`,
:func:`hour_rewards.hedera.configure_hedera`). Hosts tend to already run a document pipeline,
and this package has no business holding a second set of OCR credentials. Until a reader is
installed, every submission raises :class:`ReceiptScanError` -- an outage, not a refusal.

Photos are read and dropped. A punch keeps the verdict, the receipt's dedupe hash and the
attestation for the run that decided, never the image.
"""

from typing import Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from hour_rewards.models.responses import ReceiptSubmissionResponse
from hour_rewards.service import RewardService

# One photo of one receipt from a phone camera. Well above what a JPEG of a bill needs, and
# far below what an OCR service will accept, so the cap only ever catches misuse.
MAX_RECEIPT_IMAGE_BYTES = 6 * 1024 * 1024

# What a receipt can arrive as. Formats every phone camera and OCR service agree on, checked
# by magic bytes rather than by what the upload claims to be.
SUPPORTED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")

#: Turns an image into whatever text is in it. The host's OCR, in one call.
ReceiptReader = Callable[[bytes], Awaitable[str]]


class ReceiptImageError(ValueError):
    """The upload is not a photo worth spending an OCR call on."""


class ReceiptImageTooLargeError(ReceiptImageError):
    """Beyond :data:`MAX_RECEIPT_IMAGE_BYTES` -- no receipt needs this many pixels."""


class UnsupportedReceiptImageError(ReceiptImageError):
    """Not one of :data:`SUPPORTED_IMAGE_TYPES`, by its own leading bytes."""


class ReceiptScanError(RuntimeError):
    """The photo could not be read at all, so the user should be asked for another one.

    Distinct from a refusal on purpose: the receipt was never judged, so nothing about it was
    held against it and retrying the same photo is fair.
    """


def sniff_image_content_type(data: bytes) -> Optional[str]:
    """The image type by its magic bytes, or ``None`` for anything else.

    A client's declared content type is a claim about the file; this is the file itself.
    """
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_receipt_image(data: bytes) -> str:
    """The upload's content type, or a :class:`ReceiptImageError` saying what is wrong with it.

    Two subclasses, so a host can answer "too big" and "not an image" differently (413 and 400
    over HTTP) without inspecting messages.
    """
    if len(data) > MAX_RECEIPT_IMAGE_BYTES:
        limit_mb = MAX_RECEIPT_IMAGE_BYTES // (1024 * 1024)
        raise ReceiptImageTooLargeError(f"Photo exceeds the {limit_mb} MB limit")

    content_type = sniff_image_content_type(data)
    if content_type is None:
        raise UnsupportedReceiptImageError("File is not a supported image (JPEG, PNG, or WebP)")
    return content_type


_reader: Optional[ReceiptReader] = None


def configure_receipt_reader(reader: Optional[ReceiptReader]) -> None:
    """Install (or with ``None``, clear) the OCR every submitted photo is read with."""
    global _reader
    _reader = reader


def get_receipt_reader() -> Optional[ReceiptReader]:
    return _reader


async def submit_receipt_photo(
    session: AsyncSession,
    *,
    user_id: UUID,
    venue_id: UUID,
    image: bytes,
) -> ReceiptSubmissionResponse:
    """Read a photographed receipt and claim a punch with it at ``venue_id``.

    Everything a host's upload endpoint does after authenticating the caller. A verdict either
    way, punch or refusal, comes back as a response; the exceptions are the two cases that are
    not judgements on the receipt -- an upload that was never a photo
    (:class:`ReceiptImageError`) and OCR that could not read one (:class:`ReceiptScanError`).
    """
    validate_receipt_image(image)

    reader = get_receipt_reader()
    if reader is None:
        raise ReceiptScanError("No receipt reader is configured (see configure_receipt_reader)")

    try:
        receipt_text = await reader(image)
    except ReceiptScanError:
        raise
    except Exception as error:
        raise ReceiptScanError(f"Could not read the receipt photo: {error}") from error

    return await RewardService.submit_receipt(session, user_id, venue_id, receipt_text)
