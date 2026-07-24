"""Alembic operations that create and drop the rewards punch-card tables.

A host app calls these from one of its own revision files, so the revision chain stays
in the host while the schema itself lives here::

    from hour_rewards.migrations import downgrade as rewards_downgrade
    from hour_rewards.migrations import upgrade as rewards_upgrade

    revision = "..."
    down_revision = "..."

    def upgrade() -> None:
        rewards_upgrade()

    def downgrade() -> None:
        rewards_downgrade()

Tables are created parent-first (reward_programs -> punch_cards -> punch_events /
reward_redemption_codes -> reward_redemptions) since each references the previous one.
Requires the ``migrations`` extra: ``pip install hour-rewards-sdk[migrations]``.
"""

import sqlalchemy as sa

from alembic import op

REWARD_PROGRAMS = "reward_programs"
PUNCH_CARDS = "punch_cards"
PUNCH_EVENTS = "punch_events"
REDEMPTION_CODES = "reward_redemption_codes"
REDEMPTIONS = "reward_redemptions"

# Stored as VARCHAR sized to the longest value (native_enum=False), matching the
# `value_enum(...)` columns on the SQLModel side.
PUNCH_EVENT_STATUS = sa.Enum(
    "pending_review",
    "verified",
    "rejected",
    name="puncheventstatus",
    native_enum=False,
)
REDEMPTION_CODE_STATUS = sa.Enum(
    "pending",
    "redeemed",
    "expired",
    "invalidated",
    name="rewardredemptioncodestatus",
    native_enum=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(REWARD_PROGRAMS):
        op.create_table(
            REWARD_PROGRAMS,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("venue_id", sa.UUID(), nullable=False),
            sa.Column(
                "punches_required", sa.Integer(), nullable=False, server_default=sa.text("10")
            ),
            sa.Column("reward_description", sa.String(length=256), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("venue_id", name="uq_reward_programs_venue_id"),
        )

    if not inspector.has_table(PUNCH_CARDS):
        op.create_table(
            PUNCH_CARDS,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("venue_id", sa.UUID(), nullable=False),
            sa.Column("cycle_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("punch_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "venue_id", name="uq_user_venue_punch_card"),
        )
        op.create_index("ix_punch_cards_venue_id", PUNCH_CARDS, ["venue_id"], unique=False)

    if not inspector.has_table(PUNCH_EVENTS):
        op.create_table(
            PUNCH_EVENTS,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("punch_card_id", sa.UUID(), nullable=False),
            sa.Column("venue_id", sa.UUID(), nullable=False),
            sa.Column("cycle_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("receipt_image_id", sa.UUID(), nullable=True),
            sa.Column("receipt_date", sa.TIMESTAMP(), nullable=True),
            sa.Column("receipt_total_amount", sa.Numeric(), nullable=True),
            sa.Column("receipt_identifier", sa.String(length=128), nullable=True),
            sa.Column("dedupe_hash", sa.String(length=128), nullable=False),
            sa.Column(
                "status",
                PUNCH_EVENT_STATUS,
                nullable=False,
                server_default="pending_review",
            ),
            sa.Column("rejection_reason", sa.String(length=256), nullable=True),
            sa.Column("ai_notes", sa.String(), nullable=True),
            sa.Column("ai_confidence_score", sa.Numeric(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
            sa.ForeignKeyConstraint(["punch_card_id"], ["punch_cards.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["receipt_image_id"], ["user_images.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("venue_id", "dedupe_hash", name="uq_venue_receipt_dedupe"),
        )
        op.create_index(
            "ix_punch_events_punch_card_id", PUNCH_EVENTS, ["punch_card_id"], unique=False
        )

    if not inspector.has_table(REDEMPTION_CODES):
        op.create_table(
            REDEMPTION_CODES,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("punch_card_id", sa.UUID(), nullable=False),
            sa.Column("cycle_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                REDEMPTION_CODE_STATUS,
                nullable=False,
                server_default="pending",
            ),
            sa.Column("expires_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("redeemed_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("redeemed_by_owner_id", sa.UUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
            sa.ForeignKeyConstraint(["punch_card_id"], ["punch_cards.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["redeemed_by_owner_id"], ["owners.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token", name="uq_reward_redemption_codes_token"),
        )
        op.create_index(
            "ix_reward_redemption_codes_punch_card_id",
            REDEMPTION_CODES,
            ["punch_card_id"],
            unique=False,
        )

    if not inspector.has_table(REDEMPTIONS):
        op.create_table(
            REDEMPTIONS,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("venue_id", sa.UUID(), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=True),
            sa.Column("punch_card_id", sa.UUID(), nullable=True),
            sa.Column("redemption_code_id", sa.UUID(), nullable=True),
            sa.Column("cycle_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("punches_required", sa.Integer(), nullable=False),
            sa.Column("reward_description", sa.String(length=256), nullable=False),
            sa.Column("redeemed_by_owner_id", sa.UUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
            sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["punch_card_id"], ["punch_cards.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["redemption_code_id"], ["reward_redemption_codes.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["redeemed_by_owner_id"], ["owners.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_reward_redemptions_venue_id", REDEMPTIONS, ["venue_id"], unique=False)
        op.create_index("ix_reward_redemptions_user_id", REDEMPTIONS, ["user_id"], unique=False)
        op.create_index(
            "ix_reward_redemptions_punch_card_id", REDEMPTIONS, ["punch_card_id"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Reverse creation order: children before the tables they reference.
    for table in (REDEMPTIONS, REDEMPTION_CODES, PUNCH_EVENTS, PUNCH_CARDS, REWARD_PROGRAMS):
        if inspector.has_table(table):
            op.drop_table(table)
