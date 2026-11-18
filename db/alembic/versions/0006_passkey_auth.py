"""Passkey auth cutover (docs/auth-passkey-design.md 4 and 10, contracts/schema.sql).

Drops Supabase-era admission tables and profiles.email, creates the six auth tables and the
backend-role grants and policies. Downgrade is NOT lossless (email and invite data are gone).
"""

import os
from uuid import UUID

from alembic import op

revision = "0006_passkey_auth"
down_revision = "0005_source_url_host"
branch_labels = None
depends_on = None

AUTH_TABLES = [
    "enrollment_tokens",
    "passkey_credentials",
    "auth_sessions",
    "webauthn_ceremonies",
    "auth_rate_limits",
    "auth_events",
]

AUTH_DDL = [
    """create table enrollment_tokens (
  id               uuid primary key default gen_random_uuid(),
  token_hash       bytea not null unique check (octet_length(token_hash) = 32),
  purpose          text not null check (purpose in ('invite', 'recovery', 'add_credential')),
  user_id          uuid references profiles (user_id) on delete cascade,
  options_count    integer not null default 0 check (options_count between 0 and 1000),
  created_by_actor text not null check (created_by_actor in ('cli')),
  created_by       uuid references profiles (user_id) on delete set null,
  note             text check (note is null or char_length(note) <= 200),
  expires_at       timestamptz not null,
  used_at          timestamptz,
  used_by          uuid references profiles (user_id) on delete set null,
  revoked_at       timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  check ((purpose = 'invite') = (user_id is null)),
  check (used_by is null or used_at is not null),
  check (expires_at > created_at)
)""",
    """create index enrollment_tokens_user_open_idx on enrollment_tokens (user_id)
  where used_at is null and revoked_at is null""",
    """create index enrollment_tokens_expires_idx on enrollment_tokens (expires_at)""",
    """create table passkey_credentials (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references profiles (user_id) on delete cascade,
  credential_id   bytea not null unique check (octet_length(credential_id) between 1 and 1023),
  public_key      bytea not null,
  sign_count      bigint not null default 0 check (sign_count between 0 and 4294967295),
  transports      text[] not null default '{}',
  aaguid          uuid,
  backup_eligible boolean not null,
  backup_state    boolean not null,
  label           text not null default '' check (char_length(label) <= 64),
  last_used_at    timestamptz,
  revoked_at      timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (not backup_state or backup_eligible)
)""",
    """create index passkey_credentials_user_active_idx on passkey_credentials (user_id) where revoked_at is null""",
    """create table auth_sessions (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references profiles (user_id) on delete cascade,
  token_hash          bytea not null unique check (octet_length(token_hash) = 32),
  kind                text not null check (kind in ('passkey', 'dev')),
  credential_id       uuid references passkey_credentials (id) on delete cascade,
  reauth_at           timestamptz,
  last_seen_at        timestamptz not null default now(),
  idle_expires_at     timestamptz not null,
  absolute_expires_at timestamptz not null,
  revoked_at          timestamptz,
  revoke_reason       text check (revoke_reason is null or revoke_reason in
                        ('logout', 'user_revoked', 'passkey_revoked', 'recovery', 'disabled', 'admin', 'limit')),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  check ((kind = 'passkey') = (credential_id is not null)),
  check (absolute_expires_at > created_at),
  check (idle_expires_at <= absolute_expires_at),
  check ((revoked_at is null) = (revoke_reason is null))
)""",
    """create index auth_sessions_user_active_idx on auth_sessions (user_id) where revoked_at is null""",
    """create index auth_sessions_credential_idx on auth_sessions (credential_id)""",
    """create index auth_sessions_absolute_idx on auth_sessions (absolute_expires_at)""",
    """create table webauthn_ceremonies (
  id                   uuid primary key default gen_random_uuid(),
  purpose              text not null check (purpose in
                         ('register_invite', 'register_recovery', 'register_add',
                          'add_passkey', 'login', 'reauth')),
  challenge            bytea not null check (octet_length(challenge) = 32),
  browser_binding_hash bytea not null check (octet_length(browser_binding_hash) = 32),
  user_id              uuid references profiles (user_id) on delete cascade,
  pending_user_id      uuid,
  enrollment_token_id  uuid references enrollment_tokens (id) on delete cascade,
  session_id           uuid references auth_sessions (id) on delete cascade,
  display_name         text check (display_name is null or char_length(display_name) between 1 and 80),
  expires_at           timestamptz not null,
  consumed_at          timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  check (expires_at > created_at),
  check (case purpose
    when 'register_invite'   then enrollment_token_id is not null and pending_user_id is not null
                                  and user_id is null and session_id is null
    when 'register_recovery' then enrollment_token_id is not null and user_id is not null
                                  and pending_user_id is null and session_id is null
    when 'register_add'      then enrollment_token_id is not null and user_id is not null
                                  and pending_user_id is null and session_id is null
    when 'add_passkey'       then session_id is not null and user_id is not null
                                  and enrollment_token_id is null and pending_user_id is null
    when 'reauth'            then session_id is not null and user_id is not null
                                  and enrollment_token_id is null and pending_user_id is null
    when 'login'             then user_id is null and session_id is null
                                  and enrollment_token_id is null and pending_user_id is null
  end),
  check (display_name is null or purpose = 'register_invite')
)""",
    """create index webauthn_ceremonies_expires_idx on webauthn_ceremonies (expires_at)""",
    """create index webauthn_ceremonies_token_idx on webauthn_ceremonies (enrollment_token_id)""",
    """create index webauthn_ceremonies_session_idx on webauthn_ceremonies (session_id)""",
    """create table auth_rate_limits (
  bucket       text not null check (char_length(bucket) <= 200),
  window_start timestamptz not null,
  hits         integer not null default 0 check (hits >= 0),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (bucket, window_start)
)""",
    """create index auth_rate_limits_window_idx on auth_rate_limits (window_start)""",
    """create table auth_events (
  id                  uuid primary key default gen_random_uuid(),
  actor               text not null check (actor in ('user', 'cli', 'system')),
  user_id             uuid references profiles (user_id) on delete set null,
  event               text not null check (event in (
                        'registration_succeeded', 'recovery_completed', 'login_succeeded', 'login_failed',
                        'reauth_succeeded', 'passkey_added', 'passkey_revoked', 'session_revoked',
                        'token_issued', 'token_used', 'token_revoked', 'sign_count_regression',
                        'user_disabled', 'user_enabled')),
  session_id          uuid references auth_sessions (id) on delete set null,
  credential_id       uuid references passkey_credentials (id) on delete set null,
  enrollment_token_id uuid references enrollment_tokens (id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
)""",
    """create index auth_events_user_created_idx on auth_events (user_id, created_at)""",
    """create index auth_events_created_idx on auth_events (created_at)""",
]

BUSINESS_TABLES = [
    "profiles",
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
]
OLD_TABLES = BUSINESS_TABLES + ["allowed_emails", "invite_codes", "invite_redemptions"]
RUNTIME_ALL = BUSINESS_TABLES + [
    "passkey_credentials",
    "auth_sessions",
    "webauthn_ceremonies",
    "auth_rate_limits",
    "auth_events",
]
ADMIN_READ = [
    "passkey_credentials",
    "topics",
    "subscriptions",
    "delivery_slots",
    "jobs",
    "reports",
    "report_sections",
    "report_items",
    "item_marks",
    "usage_logs",
]


def _ensure_roles():
    # Kai (Supabase) or the local bootstrap create the LOGIN roles with passwords outside the repo.
    # Creating them NOLOGIN when missing keeps grants valid in disposable databases.
    for role in ("brief_migrator", "brief_runtime", "brief_admin"):
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN NOBYPASSRLS;
            END IF;
        END $$""")


def _precheck():
    """Stop, without changing anything, on legacy admission state (design 10 step 1)."""
    owner = os.environ.get("OWNER_USER_ID", "").strip()
    allowed = None
    if owner:
        allowed = str(UUID(owner))
    rows = (
        op.get_bind()
        .exec_driver_sql("SELECT user_id::text, status FROM profiles ORDER BY created_at, user_id")
        .fetchall()
    )
    offenders = [
        f"{user_id} ({status})"
        for user_id, status in rows
        if status == "pending" or user_id != allowed
    ]
    if offenders:
        raise RuntimeError(
            "Auth migration stopped: pending profiles or profiles other than OWNER_USER_ID exist; "
            "decide what to do with them first (no data was changed): " + ", ".join(offenders)
        )


def upgrade():
    _precheck()
    _ensure_roles()

    for table in ("invite_redemptions", "invite_codes", "allowed_emails"):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE profiles DROP COLUMN email")
    op.execute("ALTER TABLE profiles DROP CONSTRAINT profiles_status_check")
    op.execute("ALTER TABLE profiles ALTER COLUMN status SET DEFAULT 'active'")
    op.execute(
        "ALTER TABLE profiles ADD CONSTRAINT profiles_status_check "
        "CHECK (status IN ('active', 'disabled'))"
    )

    for statement in AUTH_DDL:
        op.execute(statement)
    for table in AUTH_TABLES:
        op.execute(f"""CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()""")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    # Client roles get no policy at all (default deny); the old restrictive deny policy would also
    # block the backend roles, so it goes. alembic_version keeps its deny policy (owner bypasses).
    for table in BUSINESS_TABLES:
        op.execute(f"DROP POLICY deny_direct_access ON {table}")

    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    for role in ("anon", "authenticated"):
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};
            END IF;
        END $$""")
    # TRUNCATE ignores RLS, so future tables of the migration role must not inherit any client grant.
    for role in ("PUBLIC", "anon", "authenticated"):
        guard = (
            "TRUE"
            if role == "PUBLIC"
            else f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}')"
        )
        for scope in ("", " IN SCHEMA public"):
            for kind in ("TABLES", "SEQUENCES"):
                op.execute(f"""DO $$ BEGIN IF {guard} THEN
                    ALTER DEFAULT PRIVILEGES FOR ROLE brief_migrator{scope}
                        REVOKE ALL ON {kind} FROM {role};
                END IF; END $$""")

    op.execute("GRANT USAGE ON SCHEMA public TO brief_runtime, brief_admin")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON " + ", ".join(RUNTIME_ALL) + " TO brief_runtime"
    )
    op.execute("GRANT SELECT, UPDATE, DELETE ON enrollment_tokens TO brief_runtime")
    for table in RUNTIME_ALL:
        op.execute(
            f"CREATE POLICY runtime_all ON {table} FOR ALL TO brief_runtime "
            "USING (true) WITH CHECK (true)"
        )
    op.execute(
        "CREATE POLICY runtime_select ON enrollment_tokens FOR SELECT TO brief_runtime USING (true)"
    )
    op.execute(
        "CREATE POLICY runtime_update ON enrollment_tokens FOR UPDATE TO brief_runtime "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY runtime_cleanup ON enrollment_tokens FOR DELETE TO brief_runtime "
        "USING (coalesce(used_at, revoked_at, expires_at) < now() - interval '90 days')"
    )

    op.execute("GRANT SELECT, INSERT, UPDATE ON enrollment_tokens, profiles TO brief_admin")
    op.execute("GRANT SELECT, INSERT ON agent_settings, auth_events TO brief_admin")
    op.execute("GRANT SELECT, UPDATE ON auth_sessions, webauthn_ceremonies TO brief_admin")
    op.execute("GRANT SELECT ON " + ", ".join(ADMIN_READ) + " TO brief_admin")
    for name, table in (
        ("admin_tokens", "enrollment_tokens"),
        ("admin_profiles", "profiles"),
        ("admin_sessions", "auth_sessions"),
        ("admin_ceremony", "webauthn_ceremonies"),
        ("admin_settings", "agent_settings"),
        ("admin_events", "auth_events"),
    ):
        op.execute(
            f"CREATE POLICY {name} ON {table} FOR ALL TO brief_admin USING (true) WITH CHECK (true)"
        )
    for table in ADMIN_READ:
        op.execute(f"CREATE POLICY admin_read ON {table} FOR SELECT TO brief_admin USING (true)")


def downgrade():
    """Not lossless: emails (placeholders), invite and admission data, and all auth data are gone."""
    for table in RUNTIME_ALL + ["enrollment_tokens"]:
        op.execute(f"DROP POLICY IF EXISTS runtime_all ON {table}")
    for policy in ("runtime_select", "runtime_update", "runtime_cleanup", "admin_tokens"):
        op.execute(f"DROP POLICY {policy} ON enrollment_tokens")
    for name, table in (
        ("admin_profiles", "profiles"),
        ("admin_sessions", "auth_sessions"),
        ("admin_ceremony", "webauthn_ceremonies"),
        ("admin_settings", "agent_settings"),
        ("admin_events", "auth_events"),
    ):
        op.execute(f"DROP POLICY {name} ON {table}")
    for table in ADMIN_READ:
        op.execute(f"DROP POLICY admin_read ON {table}")
    for table in reversed(AUTH_TABLES):
        op.execute(f"DROP TABLE {table}")
    for table in BUSINESS_TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM brief_runtime, brief_admin")
        op.execute(
            f"""CREATE POLICY deny_direct_access ON {table} AS RESTRICTIVE
            FOR ALL TO PUBLIC USING (false) WITH CHECK (false)"""
        )

    op.execute("ALTER TABLE profiles ADD COLUMN email text")
    op.execute("UPDATE profiles SET email = user_id::text || '@invalid.example'")
    op.execute("ALTER TABLE profiles ALTER COLUMN email SET NOT NULL")
    op.execute("ALTER TABLE profiles ADD CHECK (email = lower(email))")
    op.execute("CREATE UNIQUE INDEX profiles_email_lower_uq ON profiles (lower(email))")
    op.execute("ALTER TABLE profiles DROP CONSTRAINT profiles_status_check")
    op.execute("ALTER TABLE profiles ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute(
        "ALTER TABLE profiles ADD CONSTRAINT profiles_status_check "
        "CHECK (status IN ('pending', 'active', 'disabled'))"
    )
    op.execute("""CREATE TABLE allowed_emails (
      email text PRIMARY KEY CHECK (email = lower(email)), note text,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE invite_codes (
      code text PRIMARY KEY, max_uses integer NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
      used_count integer NOT NULL DEFAULT 0 CHECK (used_count >= 0), expires_at timestamptz,
      created_by uuid REFERENCES profiles (user_id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK (used_count <= max_uses))""")
    op.execute("""CREATE TABLE invite_redemptions (
      code text NOT NULL REFERENCES invite_codes (code) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES profiles (user_id) ON DELETE CASCADE,
      redeemed_at timestamptz NOT NULL DEFAULT now(),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (code, user_id))""")
    for table in ("allowed_emails", "invite_codes", "invite_redemptions"):
        op.execute(f"""CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()""")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY deny_direct_access ON {table} AS RESTRICTIVE
            FOR ALL TO PUBLIC USING (false) WITH CHECK (false)"""
        )
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")


# ruff: noqa: E501
