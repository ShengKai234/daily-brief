"""Align profile locale length with contract c35e8de."""

from alembic import op

revision = "0004_profile_locale"
down_revision = "0003_default_privileges"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "profiles_locale_check", "profiles", "char_length(locale) BETWEEN 2 AND 35"
    )


def downgrade():
    op.drop_constraint("profiles_locale_check", "profiles", type_="check")
