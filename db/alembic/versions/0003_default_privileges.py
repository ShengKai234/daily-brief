"""Prevent future tables from inheriting direct-client privileges."""

from alembic import op

revision = "0003_default_privileges"
down_revision = "0002_security_slots"
branch_labels = None
depends_on = None


def upgrade():
    # Defaults belong to the creating role. Without FOR ROLE, this changes the
    # migration role's defaults, which future migrations must also use.
    # Schema defaults add to global defaults: revoke both to cover either grant.
    for scope in ("", " IN SCHEMA public"):
        op.execute(f"ALTER DEFAULT PRIVILEGES{scope} REVOKE ALL ON TABLES FROM PUBLIC")
        for role in ("anon", "authenticated"):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    ALTER DEFAULT PRIVILEGES{scope} REVOKE ALL ON TABLES FROM {role};
                END IF;
            END $$""")


def downgrade():
    # Never recreate unknown pre-migration grants or reopen client access.
    pass
