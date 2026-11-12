"""Initial schema copied from Phase 0 contracts/schema.sql (6ada9a6).

Self-contained historical snapshot; never reads a mutable contract at runtime.
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""create extension if not exists pgcrypto""")
    op.execute("""create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$""")
    op.execute("""create table profiles (
  user_id      uuid primary key,
  email        text not null,
  display_name text not null default '' check (char_length(display_name) <= 80),
  avatar_url   text check (avatar_url is null or char_length(avatar_url) <= 2048),
  bio          text not null default '' check (char_length(bio) <= 500),
  timezone     text not null default 'Asia/Taipei',
  locale       text not null default 'zh-TW',
  status       text not null default 'pending' check (status in ('pending', 'active', 'disabled')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  check (email = lower(email))
)""")
    op.execute("""create unique index profiles_email_lower_uq on profiles (lower(email))""")
    op.execute("""create table allowed_emails (
  email      text primary key check (email = lower(email)),
  note       text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
)""")
    op.execute("""create table invite_codes (
  code        text primary key,
  max_uses    integer not null default 1 check (max_uses >= 1),
  used_count  integer not null default 0 check (used_count >= 0),
  expires_at  timestamptz,
  created_by  uuid references profiles (user_id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check (used_count <= max_uses)
)""")
    op.execute("""create table invite_redemptions (
  code        text not null references invite_codes (code) on delete cascade,
  user_id     uuid not null references profiles (user_id) on delete cascade,
  redeemed_at timestamptz not null default now(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (code, user_id)
)""")
    op.execute("""create table agent_settings (
  user_id         uuid primary key references profiles (user_id) on delete cascade,
  runner          text not null default 'claude_subscription'
                    check (runner in ('claude_subscription', 'anthropic_api')),
  model           text not null default 'sonnet',
  daily_run_limit integer not null default 3 check (daily_run_limit >= 0),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
)""")
    op.execute("""create table topics (
  id             uuid primary key default gen_random_uuid(),
  owner_id       uuid not null references profiles (user_id) on delete cascade,
  title          text not null check (char_length(title) between 1 and 200),
  normalized_key text not null,
  visibility     text not null default 'private' check (visibility in ('private', 'public')),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (owner_id, normalized_key)
)""")
    op.execute("""create table subscriptions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references profiles (user_id) on delete cascade,
  topic_id   uuid not null references topics (id) on delete cascade,
  item_count smallint not null default 3 check (item_count between 1 and 10),
  position   integer not null default 0 check (position >= 0),
  enabled    boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, topic_id)
)""")
    op.execute("""create table delivery_slots (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references profiles (user_id) on delete cascade,
  local_time time not null,
  enabled    boolean not null default true,
  position   smallint not null check (position between 1 and 3),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, position),
  unique (user_id, local_time),
  check (extract(minute from local_time) in (0, 30) and extract(second from local_time) = 0)
)""")
    op.execute("""create table jobs (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references profiles (user_id) on delete cascade,
  slot_id       uuid references delivery_slots (id) on delete set null,
  scheduled_for timestamptz not null,
  status        text not null default 'queued'
                  check (status in ('queued', 'running', 'succeeded', 'failed')),
  attempts      integer not null default 0 check (attempts >= 0),
  runner        text not null check (runner in ('claude_subscription', 'anthropic_api')),
  locked_at     timestamptz,
  locked_by     text,
  error         text check (error is null or char_length(error) <= 2000),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (user_id, slot_id, scheduled_for)
)""")
    op.execute("""create index jobs_status_scheduled_idx on jobs (status, scheduled_for)""")
    op.execute("""create table reports (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles (user_id) on delete cascade,
  job_id       uuid unique references jobs (id) on delete set null,
  report_date  date not null,
  slot_id      uuid references delivery_slots (id) on delete set null,
  generated_at timestamptz not null default now(),
  visibility   text not null default 'private' check (visibility in ('private', 'public')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
)""")
    op.execute("""create index reports_user_date_idx on reports (user_id, report_date)""")
    op.execute("""create table report_sections (
  id         uuid primary key default gen_random_uuid(),
  report_id  uuid not null references reports (id) on delete cascade,
  topic_id   uuid references topics (id) on delete set null,
  title      text not null check (char_length(title) between 1 and 200),
  position   integer not null check (position >= 0),
  status     text not null default 'ok' check (status in ('ok', 'insufficient_news')),
  note       text check (note is null or char_length(note) <= 500),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (report_id, position)
)""")
    op.execute("""create table report_items (
  id           uuid primary key default gen_random_uuid(),
  section_id   uuid not null references report_sections (id) on delete cascade,
  rank         integer not null check (rank >= 1),
  title        text not null check (char_length(title) between 1 and 200),
  summary      text not null check (char_length(summary) between 1 and 600),
  source_name  text not null check (char_length(source_name) between 1 and 100),
  source_url   text not null check (source_url ~ '^https?://' and char_length(source_url) <= 2048),
  published_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (section_id, rank)
)""")
    op.execute("""create table item_marks (
  user_id    uuid not null references profiles (user_id) on delete cascade,
  item_id    uuid not null references report_items (id) on delete cascade,
  read_at    timestamptz,
  note       text check (note is null or char_length(note) <= 2000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, item_id)
)""")
    op.execute("""create table usage_logs (
  id            uuid primary key default gen_random_uuid(),
  job_id        uuid references jobs (id) on delete set null,
  user_id       uuid references profiles (user_id) on delete cascade,
  runner        text not null check (runner in ('claude_subscription', 'anthropic_api', 'stub')),
  model         text not null,
  duration_ms   integer not null check (duration_ms >= 0),
  search_count  integer not null check (search_count >= 0),
  input_tokens  integer check (input_tokens is null or input_tokens >= 0),
  output_tokens integer check (output_tokens is null or output_tokens >= 0),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
)""")
    op.execute("""create index usage_logs_job_idx on usage_logs (job_id)""")
    op.execute("""create trigger profiles_set_updated_at 
        before update on profiles
        for each row execute function set_updated_at()""")
    op.execute("""create trigger allowed_emails_set_updated_at 
        before update on allowed_emails
        for each row execute function set_updated_at()""")
    op.execute("""create trigger invite_codes_set_updated_at 
        before update on invite_codes
        for each row execute function set_updated_at()""")
    op.execute("""create trigger invite_redemptions_set_updated_at 
        before update on invite_redemptions
        for each row execute function set_updated_at()""")
    op.execute("""create trigger agent_settings_set_updated_at 
        before update on agent_settings
        for each row execute function set_updated_at()""")
    op.execute("""create trigger topics_set_updated_at 
        before update on topics
        for each row execute function set_updated_at()""")
    op.execute("""create trigger subscriptions_set_updated_at 
        before update on subscriptions
        for each row execute function set_updated_at()""")
    op.execute("""create trigger delivery_slots_set_updated_at 
        before update on delivery_slots
        for each row execute function set_updated_at()""")
    op.execute("""create trigger jobs_set_updated_at 
        before update on jobs
        for each row execute function set_updated_at()""")
    op.execute("""create trigger reports_set_updated_at 
        before update on reports
        for each row execute function set_updated_at()""")
    op.execute("""create trigger report_sections_set_updated_at 
        before update on report_sections
        for each row execute function set_updated_at()""")
    op.execute("""create trigger report_items_set_updated_at 
        before update on report_items
        for each row execute function set_updated_at()""")
    op.execute("""create trigger item_marks_set_updated_at 
        before update on item_marks
        for each row execute function set_updated_at()""")
    op.execute("""create trigger usage_logs_set_updated_at 
        before update on usage_logs
        for each row execute function set_updated_at()""")
    op.execute("""alter table profiles           enable row level security""")
    op.execute("""alter table allowed_emails     enable row level security""")
    op.execute("""alter table invite_codes       enable row level security""")
    op.execute("""alter table invite_redemptions enable row level security""")
    op.execute("""alter table agent_settings     enable row level security""")
    op.execute("""alter table topics             enable row level security""")
    op.execute("""alter table subscriptions      enable row level security""")
    op.execute("""alter table delivery_slots     enable row level security""")
    op.execute("""alter table jobs               enable row level security""")
    op.execute("""alter table reports            enable row level security""")
    op.execute("""alter table report_sections    enable row level security""")
    op.execute("""alter table report_items       enable row level security""")
    op.execute("""alter table item_marks         enable row level security""")
    op.execute("""alter table usage_logs         enable row level security""")


def downgrade():
    op.drop_table("usage_logs")
    op.drop_table("item_marks")
    op.drop_table("report_items")
    op.drop_table("report_sections")
    op.drop_table("reports")
    op.drop_table("jobs")
    op.drop_table("delivery_slots")
    op.drop_table("subscriptions")
    op.drop_table("topics")
    op.drop_table("agent_settings")
    op.drop_table("invite_redemptions")
    op.drop_table("invite_codes")
    op.drop_table("allowed_emails")
    op.drop_table("profiles")
    op.execute("DROP FUNCTION set_updated_at()")
    # pgcrypto may predate this migration and is intentionally retained.
