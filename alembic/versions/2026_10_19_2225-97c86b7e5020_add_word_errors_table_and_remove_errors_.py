"""add_word_errors_table_and_remove_errors_count

Revision ID: 97c86b7e5020
Revises: 14d1180c4af5
Create Date: 2026-10-19 22:25:01.703886
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "97c86b7e5020"
down_revision: str | Sequence[str] | None = "14d1180c4af5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create word_errors table
    op.create_table(
        "word_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_word_id", sa.Integer(), nullable=True),
        sa.Column("common_word_id", sa.Integer(), nullable=True),
        sa.Column(
            "error_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["common_word_id"],
            ["words.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_word_id"],
            ["users_words.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes
    op.create_index(
        "idx_word_errors_user_date", "word_errors", ["user_id", "error_date"]
    )

    # 2. Backfill error history from the aggregated users_words.errors_count.
    #    generate_series expands one row per recorded error, so the counters
    #    are preserved exactly instead of collapsing to a single event.
    #    error_date falls back to the word's creation time: the original
    #    per-error timestamps were never stored.
    op.execute(
        """
        INSERT INTO word_errors (user_id, user_word_id, error_date)
        SELECT uw.user_id, uw.id, uw.created_at
        FROM users_words uw
        CROSS JOIN generate_series(1, uw.errors_count) AS n
        WHERE uw.errors_count > 0
        """
    )

    # 3. Drop the errors_count column from users_words
    op.drop_column("users_words", "errors_count")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "users_words",
        sa.Column(
            "errors_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("users_words", "errors_count", server_default=None)

    op.drop_index("idx_word_errors_user_date", table_name="word_errors")
    op.drop_table("word_errors")
