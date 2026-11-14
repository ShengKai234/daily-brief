"""Deny direct client access and allow atomic slot replacement."""

from alembic import op

revision = "0002_security_slots"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TABLES = (
    "profiles",
    "allowed_emails",
    "invite_codes",
    "invite_redemptions",
    "agent_settings",
    "topics",
    "subscriptions",
    "delivery_slots",
    "jobs",
    "reports",
    "report_sections",
    "report_items",
    "item_marks",
    "usage_logs",
    "alembic_version",
)


def upgrade():
    for column in ("position", "local_time"):
        name = f"delivery_slots_user_id_{column}_key"
        op.execute(f"ALTER TABLE delivery_slots DROP CONSTRAINT {name}")
        op.execute(f"""ALTER TABLE delivery_slots ADD CONSTRAINT {name}
            UNIQUE (user_id, {column}) DEFERRABLE INITIALLY DEFERRED""")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY deny_direct_access ON {table} AS RESTRICTIVE
            FOR ALL TO PUBLIC USING (false) WITH CHECK (false)""")
        # RLS does not cover TRUNCATE/REFERENCES; remove table privileges as well.
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        # Supabase may grant these roles access through default privileges.
        # No Supabase schemas/functions or runtime dependency on these role names.
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON TABLE {table} FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON TABLE {table} FROM authenticated;
            END IF;
        END $$""")


def downgrade():
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY deny_direct_access ON {table}")
    op.execute("ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY")
    for column in ("position", "local_time"):
        name = f"delivery_slots_user_id_{column}_key"
        op.execute(f"ALTER TABLE delivery_slots DROP CONSTRAINT {name}")
        op.execute(f"""ALTER TABLE delivery_slots ADD CONSTRAINT {name}
            UNIQUE (user_id, {column})""")
    # Revoked client grants are intentionally not restored by a downgrade.
