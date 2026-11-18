"""Require a non-empty host in report source URLs (contract f6ed465)."""

from alembic import op

revision = "0005_source_url_host"
down_revision = "0004_profile_locale"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("report_items_source_url_check", "report_items", type_="check")
    op.create_check_constraint(
        "report_items_source_url_check",
        "report_items",
        r"source_url ~ '^https?://[^\s/?#@]+([/?#][^\s]*)?$' "
        "AND char_length(source_url) <= 2048",
    )


def downgrade():
    op.drop_constraint("report_items_source_url_check", "report_items", type_="check")
    op.create_check_constraint(
        "report_items_source_url_check",
        "report_items",
        "source_url ~ '^https?://' AND char_length(source_url) <= 2048",
    )
