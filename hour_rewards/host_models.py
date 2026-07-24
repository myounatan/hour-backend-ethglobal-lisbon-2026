"""Type-only stand-ins for the models the host application owns.

This package never imports the host app at runtime. SQLAlchemy resolves relationship
targets by class *name* when mappers are configured, so the host's real ``User``,
``Venue``, ``Owner`` and ``UserImage`` classes are what the rewards tables link to once
both sets of models are registered with the same SQLModel metadata. The declarations
below exist only so type checkers can resolve those names inside this package.

See "Host contract" in the README for the tables and back-references a host must provide.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:

    class User:
        """Host's user table (``users``), back-populating ``punch_cards``."""

    class Venue:
        """Host's venue table (``venues``), back-populating ``reward_program``/``punch_cards``."""

    class Owner:
        """Host's venue-owner table (``owners``), the staff side of a redemption."""

    class UserImage:
        """Host's uploaded-image table (``user_images``), where receipt photos live."""
