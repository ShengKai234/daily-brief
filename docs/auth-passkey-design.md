# Passkey-only Auth（自建 WebAuthn，邀請制）— BDD 設計草稿

> **狀態：** v0.3，Kai 於 2026-11-15 核准（全部照建議）。審查紀錄：Claude／Codex 兩輪＋兩次確認。DESIGN 3.4 與 8 引用本文件
> **v0.3 修改：** 計數器規則可實作（wrapper 傳 `current_sign_count=0`）；RLS 改為只對後端角色建立 policy；已撤銷 passkey 永久保留；每人 passkey／session／ceremony 上限；補上門檻在 ceremony 中途關閉、回應遺失、同時復原等情境。
> **日期：** 2026-11-15
> **作者：** lead
> **語言：** 說明用繁體中文；Gherkin、識別字、錯誤碼用英文
> **取代：** DESIGN 目前的「Supabase Auth（Google），後端只驗證 JWT」
> **v0.2 主要修改：** `/internal` 不檢查 Origin；ceremony 狀態機與統一的鎖定順序；瀏覽器綁定可重複使用；recovery 取代舊連結並預設全部替換；非 owner 加入在發行與兌換時都檢查；移除會被攻擊者利用的 rate limit；`APP_ENV` 必填、正式環境拒絕 dev session；修正 DDL；新增遷移章節、CSP、權限與測試分層。

## 0. 摘要

| 決定 | 內容 |
|---|---|
| 驗證權威 | 我們自己的 API（FastAPI on Lambda）。不使用 Supabase Auth、Google OAuth。Supabase 只當 Postgres |
| 登入方式 | **只有 passkey（WebAuthn）**。沒有密碼、Email OTP、TOTP、社群登入 |
| 註冊門檻 | **邀請制**：沒有有效的一次性 enrollment token 就不能建立帳號。發 token＝准許加入 |
| 非 owner 門檻 | `AnthropicApiRunner` 完成前，非 owner 的加入與復原一律拒絕（DESIGN 3.1），在發行、options、verify 三處都檢查 |
| Owner 起始 | 本機 CLI（需要資料庫權限）建立 owner profile 並發一次性連結。沒有公開的起始端點 |
| Session | 隨機不透明 token，DB 只存 SHA-256；正式環境 `__Host-` cookie（`Secure`、`HttpOnly`、`SameSite=Strict`）；每個請求查 DB；撤銷立即生效；沒有 refresh token、沒有 JWT |
| CSRF | 必要且完全相符的 `Origin`＋`SameSite=Strict`＋只接受 JSON。不另加 CSRF token（兩位審查者同意） |
| 跨裝置 | 同步的 passkey（iCloud 鑰匙圈、Google 密碼管理工具）＋瀏覽器內建的 FIDO 跨裝置驗證（CDA）。**不自建 QR 授權** |
| 復原 | 至少兩把 passkey 或同步 passkey；全部遺失時由 owner 確認身分後發 1 小時的 recovery 連結，預設撤銷舊 passkey 與所有 session |
| 成本 | 不新增任何付費服務（被攻擊時 Lambda／DB 用量仍會增加） |

### 0.1 非目標

- 自訂 QR 一次性登入／QR 註冊 passkey（consent phishing 會造成永久盜用，RFC 10027 §6.1.1）。
- Email OTP、SES、寄信服務；TOTP；Google／Apple 登入；密碼。
- 原生 iOS／Android app 的登入（Later，見 §11）。
- 自己實作 CTAP、BLE、hybrid transport。
- 帳號合併、身分連結。
- POC 不做：重新命名 passkey、「登出其他所有裝置」按鈕、裝置名稱解析、只有一把未同步 passkey 的提醒（M3 再評估）。

### 0.2 保留自 Kai spec 的原則

credential 與 session 分開；一個帳號可有多把 passkey；一次性 token／challenge 只能成功一次；不存生物特徵或私鑰；`credential_id` 不是裝置識別碼；登入失敗不洩漏帳號狀態。

## 1. 架構與信任邊界

```
Browser (https://<APP_HOST>)                       Lambda: FastAPI                  Postgres (Supabase)
 ├─ navigator.credentials.create/get  ──► /api/v1/auth/*  (public; Origin, rate limit) ──► webauthn_ceremonies
 │                                     ──► /api/v1/...    (session cookie; Origin)     ──► auth_sessions, profiles
 └─ cookies: __Host-brief_session, __Host-brief_ceremony                              passkey_credentials, ...
Kai's machine: `uv run python -m app.auth_admin ...` (admin DB role, TLS) ──► enrollment_tokens, profiles
Worker (GitHub Actions) ──► /api/internal/* (X-Internal-Token only; no Origin check; unchanged)
EventBridge ──► scheduler handler (direct invoke; unchanged)
```

- 前端與 API 同一個 origin（DESIGN 4 不變），沒有跨站 cookie、沒有 CORS。
- **RP ID = app 的確切主機名稱**（例如 `brief.<domain>`），設定 `WEBAUTHN_RP_ID`；`WEBAUTHN_ORIGINS` 為完全相符的 origin 列表（正式環境只有 `https://brief.<domain>`）。**絕不從 `Host`、`X-Forwarded-*` 推導。** 改 RP ID 會讓所有 passkey 失效，需要另外規劃重新註冊。
- 本機：RP ID `localhost`，origin `http://localhost:<WEB_PORT>`（每個 worktree 的 `.env`）。本機 passkey 與正式環境互不相通。
- **`APP_ENV` 必填**，只接受 `local`、`production`；缺少或其他值時 API 與 CLI 拒絕啟動。只有 `local` 允許非 `Secure` cookie、dev session 與 dev CLI 指令。（目前 `api/app/config.py` 預設為 `local`，切換時必須改。）
- Supabase：由 Kai 關閉 Data API；SQL 權限另外收緊（§4.4）。RLS default deny 是第二道防線。
- 邊緣（CloudFront）：`/api/*` 關閉快取、轉送 `Cookie`、`Origin` 與 `CloudFront-Viewer-Address`、多個 `Set-Cookie` 完整傳回；SPA 的 403/404 → `index.html` 規則不套用到 `/api/*`；所有 `/api` 回應 `Cache-Control: no-store`；PWA service worker 不快取 `/api/*`。
- 靜態頁面回應標頭：`Content-Security-Policy`（`default-src 'self'`、無 inline script、`frame-ancestors 'none'`、`base-uri 'none'`、`object-src 'none'`）、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。`/enroll` 頁面不載入任何第三方 script。

## 2. 名詞

| 名詞 | 意義 |
|---|---|
| Profile | 應用程式使用者，主鍵 `profiles.user_id`（UUID；也當 WebAuthn user handle，以 16 bytes 編碼，不含個資） |
| Owner | `profiles.user_id = OWNER_USER_ID` 的使用者 |
| Passkey / credential | 一把 WebAuthn 公鑰憑證，存在 `passkey_credentials` |
| Enrollment token | 一次性、有期限的註冊授權。`invite`＝建立新 profile；`recovery`＝替換既有使用者的所有 passkey；`add_credential`＝替既有使用者新增一把（保留舊的） |
| Ceremony | 一次 WebAuthn 註冊或驗證流程，存在 `webauthn_ceremonies`，5 分鐘有效，只能成功一次 |
| Browser binding | 一個瀏覽器的隨機值（ceremony cookie），同一瀏覽器的多個 ceremony 共用，用來確認 verify 來自發起 options 的瀏覽器 |
| Session | 登入狀態，存在 `auth_sessions`；瀏覽器只持有隨機 token |
| Reauth | 已登入狀態下再用自己的 passkey 驗證一次，5 分鐘內可執行敏感操作 |

## 3. 安全不變量（MUST）

- **AUTH-1 加入門檻** 沒有有效 `invite` token 就不能建立 profile。登入成功永遠不會建立 profile 或改變 `status`。
- **AUTH-2 停用** `disabled` 的 profile 不能登入、註冊、復原或使用任何 `/v1` 路由。停用時撤銷其所有 session 與未使用的 token；重新啟用不會恢復舊 session 或舊 token。
- **AUTH-3 WebAuthn 檢查** 由我們的 wrapper 明確要求，不依賴函式庫預設值：`type`、challenge、origin 完全相符、RP ID hash、UP 與 UV（`require_user_verification=True`）、拒絕 `crossOrigin=true` 或有 `topOrigin`；登入時 `userHandle` 必須等於 credential 的 `user_id`（`UUID.bytes`）；reauth 與新增 passkey 時，credential 必須屬於該 session 的使用者；`allowCredentials` 非空時由伺服器再檢查一次；BE（backup eligible）旗標不可變，且 BS 為真時 BE 必須為真。
- **AUTH-4 ceremony 狀態機與鎖定**（詳見 §5.6）
  1. 先檢查 browser binding、purpose 與 endpoint 相符；不符時回 `ceremony_invalid`，**不消耗**該 ceremony。
  2. 密碼學驗證在鎖定之外進行（用未鎖定讀取的資料）。
  3. 開啟交易，依統一順序鎖定：**profile → enrollment token → sessions → credentials → ceremony**（invite 沒有 profile，從 token 開始）。鎖定後重新檢查所有狀態（期限、撤銷、status、credential 公鑰與計數、加入門檻、reauth 是否仍有效）。
  4. 成功：所有寫入（profile、credential、token、session、ceremony 消耗）在同一交易提交。
  5. 失敗（綁定正確但 assertion 無效或狀態不符）：只把 ceremony 標記為已消耗、寫入失敗事件，提交後再回錯誤。token 不會因失敗而被使用。
  CLI 的停用、發行 recovery 使用同一個順序。
- **AUTH-5 綁定** 每個 ceremony 綁定 purpose、browser binding，以及（如適用）使用者、session、enrollment token。每個端點只接受自己 purpose 的 ceremony。
- **AUTH-6 新身分材料** `credential_id` 在整個資料庫唯一；不能重新註冊已存在（包含已撤銷）的 credential，因此已撤銷的 credential 永久保留。`excludeCredentials` 只是使用者體驗，不是唯一性保證。
- **AUTH-7 祕密** session token、enrollment token、browser binding、challenge 都用 CSPRNG 產生至少 32 bytes；DB 只存 token 與 binding 的 SHA-256（challenge 5 分鐘內存原始值）。不寫進 log、URL query、錯誤、audit。**不記錄 request body、`Cookie`、`Set-Cookie`**（含 debug 與 Lambda adapter 的請求 log）。enrollment token 只出現在 URL fragment，頁面在載入任何其他 script 前用 `history.replaceState` 移除，只保存在記憶體。CLI 印出的連結是刻意的祕密傳遞管道，不得被 CI 或 log 擷取。
- **AUTH-8 session 產生** 每次登入、註冊、復原都產生新的 session token；伺服器從不接受客戶端指定的 session。新 session 的 `reauth_at` 為 null。
- **AUTH-9 請求邊界**（只適用於瀏覽器的 `/api/v1`）
  - 所有 unsafe method（POST、PUT、PATCH、DELETE），包含公開 auth 端點與登出，要求恰好一個、完全等於 `WEBAUTHN_ORIGINS` 之一的 `Origin`；缺少、`null`、多個或不相符都回 403 `origin_mismatch`。
  - 有 body 的操作要求 `Content-Type: application/json`，否則 415 `unsupported_media_type`；沒有 body 的操作（例如登出、`POST /v1/jobs`）允許空 body。
  - GET 不執行業務或安全管理動作；只允許有上限的 session 活動紀錄（§4.3）。
  - `/v1` 拒絕 `X-Internal-Token` 與 `Authorization: Bearer`（401）；`/api/internal` 只接受 service token，帶 session cookie 則 401，**不檢查 Origin**。
  - 檢查順序：路由家族 → body 大小（413）→ Origin（403）→ media type（415）→ rate limit（429）→ 身分驗證（401）→ reauth（403）→ 驗證（400/422）→ 狀態衝突（409）。
- **AUTH-10 session 驗證** 每個請求：依 cookie 的 SHA-256 查詢，條件為未撤銷、未超過 idle 與 absolute 期限、`profiles.status='active'`；`kind='passkey'` 時對應的 credential 必須屬於同一使用者且未撤銷；`APP_ENV≠local` 時拒絕 `kind='dev'`。任何一項不符回 401 `unauthorized`。重複的 session cookie 也回 401。
- **AUTH-11 最後一把 passkey** 刪除 passkey 前鎖定 profile，排除目標後若沒有其他有效 passkey 回 409 `last_passkey`。刪除需要 5 分鐘內的 reauth，並撤銷由它建立的 session（包含目前 session）。
- **AUTH-12 錯誤** RFC 9457 problem+json、小寫 `code`。登入失敗不區分「credential 不存在」「簽章錯誤」「已撤銷」；只有在證明有效之後，才可回 403 `account_disabled`。
- **AUTH-13 資料表** 新資料表一律 `created_at`／`updated_at`、RLS enabled；client 角色沒有任何 policy，只有兩個後端角色有 policy（§4.4）；不存原始 IP、user agent、token 或 binding 原始值。
- **AUTH-14 非 owner 門檻（DESIGN 3.1）** `NON_OWNER_ADMISSION` 預設 `false`，同時存在於 API 與 CLI 設定。為 false 時：CLI 拒絕發 `invite` 與非 owner 的 token；`registration/options` 與 `verify` 拒絕 `invite` 及目標為非 owner 的 token（403 `admission_disabled`），包含已經發出的 token。只有 `AnthropicApiRunner` 完成且 Kai 核准後才能改為 true。新的非 owner profile 的 `agent_settings.runner` 明確設為 `anthropic_api`。scheduler、claim、result 的 owner-only 檢查維持獨立。
- **AUTH-15 復原** 只在 owner 透過另一個已知管道確認對方身分後發行。發新的 recovery／add_credential token 時，撤銷該使用者其他未使用的同類 token 及其 ceremony。`recovery` 成功時在同一交易：撤銷該使用者所有舊 passkey 與 session、撤銷其他未使用 token、新增 passkey、建立新 session。復原不會啟用 disabled 帳號。
- **AUTH-16 排程資格** scheduler 只為「active 且至少有一把有效 passkey」的 profile 建立工作；`APP_ENV=local` 例外（本機 seed 的 owner 沒有 passkey 也能 demo）。

## 4. 資料模型（schema.sql 草稿變更）

### 4.1 修改與移除

```sql
-- profiles
--   user_id：保留現有值（包含 OWNER_USER_ID）；新 profile 由 API 產生 UUID
--   移除 email 欄位、profiles_email_lower_uq、email = lower(email) 檢查（K3）
--   status：('active', 'disabled')，預設 'active'；移除 'pending'（遷移規則見 §10）
-- 移除：invite_redemptions、invite_codes、allowed_emails（由 enrollment_tokens 取代）
```

### 4.2 新增

```sql
create table enrollment_tokens (
  id               uuid primary key default gen_random_uuid(),
  token_hash       bytea not null unique check (octet_length(token_hash) = 32),
  purpose          text not null check (purpose in ('invite', 'recovery', 'add_credential')),
  user_id          uuid references profiles (user_id) on delete cascade,     -- recovery/add_credential 的目標
  options_count    integer not null default 0 check (options_count between 0 and 1000),  -- 終身嘗試次數
  created_by_actor text not null check (created_by_actor in ('cli')),        -- POC 只有 CLI 能發行
  created_by       uuid references profiles (user_id) on delete set null,
  note             text check (note is null or char_length(note) <= 200),    -- 不放個資
  expires_at       timestamptz not null,
  used_at          timestamptz,
  used_by          uuid references profiles (user_id) on delete set null,
  revoked_at       timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  check ((purpose = 'invite') = (user_id is null)),
  check (used_by is null or used_at is not null),      -- 保留使用紀錄，即使使用者被刪除
  check (expires_at > created_at)
);
create index enrollment_tokens_user_open_idx on enrollment_tokens (user_id)
  where used_at is null and revoked_at is null;
create index enrollment_tokens_expires_idx on enrollment_tokens (expires_at);

create table passkey_credentials (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references profiles (user_id) on delete cascade,
  credential_id   bytea not null unique check (octet_length(credential_id) between 1 and 1023),
  public_key      bytea not null,                                         -- COSE key
  sign_count      bigint not null default 0 check (sign_count between 0 and 4294967295),
  transports      text[] not null default '{}',
  aaguid          uuid,                                                   -- 未驗證的中繼資料，僅供顯示
  backup_eligible boolean not null,
  backup_state    boolean not null,
  label           text not null default '' check (char_length(label) <= 64),
  last_used_at    timestamptz,
  revoked_at      timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (not backup_state or backup_eligible)
);
create index passkey_credentials_user_active_idx on passkey_credentials (user_id) where revoked_at is null;

create table auth_sessions (
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
);
create index auth_sessions_user_active_idx on auth_sessions (user_id) where revoked_at is null;
create index auth_sessions_credential_idx on auth_sessions (credential_id);
create index auth_sessions_absolute_idx on auth_sessions (absolute_expires_at);

create table webauthn_ceremonies (
  id                   uuid primary key default gen_random_uuid(),      -- ceremony_id
  purpose              text not null check (purpose in
                         ('register_invite', 'register_recovery', 'register_add',
                          'add_passkey', 'login', 'reauth')),
  challenge            bytea not null check (octet_length(challenge) = 32),
  browser_binding_hash bytea not null check (octet_length(browser_binding_hash) = 32),
  user_id              uuid references profiles (user_id) on delete cascade,
  pending_user_id      uuid,                                           -- register_invite：成功後成為 profiles.user_id
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
);
create index webauthn_ceremonies_expires_idx on webauthn_ceremonies (expires_at);
create index webauthn_ceremonies_token_idx on webauthn_ceremonies (enrollment_token_id);
create index webauthn_ceremonies_session_idx on webauthn_ceremonies (session_id);

create table auth_rate_limits (
  bucket       text not null check (char_length(bucket) <= 200),  -- 例："ip:<hmac>:auth"
  window_start timestamptz not null,
  hits         integer not null default 0 check (hits >= 0),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (bucket, window_start)
);
create index auth_rate_limits_window_idx on auth_rate_limits (window_start);

create table auth_events (
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
);
create index auth_events_user_created_idx on auth_events (user_id, created_at);
create index auth_events_created_idx on auth_events (created_at);
-- 全部新表：set_updated_at trigger；enable row level security（client 角色無 policy，後端角色 policy 見 §4.4）
```

### 4.3 預設值與清理

| 項目 | 預設 | 備註 |
|---|---|---|
| ceremony 有效期 | 5 分鐘 | WebAuthn timeout 300 s |
| browser binding cookie | 10 分鐘，每次 options 時延長 | 同一瀏覽器的多個 ceremony 共用 |
| invite token | 7 天，`options_count` 上限 10 | 單次成功使用 |
| recovery／add_credential token | 1 小時（CLI 可用 `--hours` 延長，上限 24） | 可接管既有帳號，期限從短 |
| owner bootstrap token | 1 小時 | |
| session idle | 30 天 | `last_seen_at` 超過 1 小時才更新，同時設 `idle_expires_at = least(now() + 30 days, absolute_expires_at)`；已過期的 session 不會被延長 |
| session absolute | 90 天 | |
| reauth 有效 | 5 分鐘 | 新增 passkey 時 options 與 verify 兩次都檢查 |
| 清理（現有 scheduler tick，每批 1000 筆，有 statement timeout） | ceremony：過期 1 小時後；session：撤銷或過期 30 天後；token：使用、撤銷或過期 90 天後；rate-limit window：1 天後；auth_events：90 天後 | 清理不決定期限，期限一律在查詢時判斷；不刪除仍有效的 token |
| 已撤銷的 passkey | **永久保留**（不刪除） | 保證 AUTH-6：舊 credential id 永遠不能重新註冊。每人最多 10 把有效 passkey，資料量很小 |
| 每人上限 | 有效 passkey 10 把（超過 409 `passkey_limit`）；有效 session 20 個（超過時撤銷最舊的，`revoke_reason='limit'`）；每個 session 未消耗的 ceremony 5 個 | 讓未過期的狀態也有上限；公開 ceremony 由 per-IP 限制與 5 分鐘期限限制 |

### 4.4 權限與 RLS

RLS 開啟後，沒有 policy 的一般角色連自己的資料都讀不到；table grant 不會繞過 RLS。因此 DESIGN 原本的「RLS enabled、no policies」改為：**對 client 角色（`PUBLIC`、`anon`、`authenticated`）沒有任何 policy（default deny）；只對兩個後端角色建立 policy**。後端角色不是資料表 owner，也沒有 `BYPASSRLS`，所以 policy 與 grant 就是它們的全部權限。

| 角色 | 用途 | auth 資料表 | 既有業務資料表 |
|---|---|---|---|
| `brief_migrator` | 執行 migration（資料表 owner） | owner | owner |
| `brief_runtime` | Lambda 與 scheduler | session、ceremony、credential、rate limit、event：讀寫；`enrollment_tokens`：`select`、`update`（使用、計數），以及只刪除「使用、撤銷或過期 90 天以上」列的 `delete` policy；**不能 `insert`** | 讀寫（與現在相同） |
| `brief_admin` | CLI | `enrollment_tokens` 讀寫；`webauthn_ceremonies`：`update`（設定 `consumed_at`，讓舊連結的 ceremony 失效）；`profiles` 建立與更新 status；session 撤銷；event 新增 | `agent_settings`：`insert`（bootstrap）；其他只讀 |

- 每張表 `enable row level security`；依上表建立 `create policy ... to brief_runtime`／`to brief_admin`；client 角色沒有 policy。`brief_runtime` 在讀寫的表上用 `for all` policy（`SELECT ... FOR UPDATE` 也需要 update 權限，否則 scheduler 與登入的鎖定會失敗）。
- 所有表與 sequence：`revoke all ... from public, anon, authenticated`（含 `TRUNCATE`），並在 `brief_migrator` 上設定 default privileges。
- 測試（F10）：client 角色全部被拒；`brief_runtime` 能完成登入、session 驗證、業務 API，但不能新增 token；`brief_admin` 能完成 bootstrap 與 recovery 發行。
- 在 Supabase 上建立這些角色與 policy 的方式為 unverified（§12 第 5 項）。
- Lambda 連線經 transaction pooler；CLI 可用 direct 或 session pooler（IPv4 環境），必須驗證 TLS 身分，連線字串不放在 shell 參數或歷史紀錄。

## 5. API 合約草稿（`/api/v1`）

所有回應 `Cache-Control: no-store`。錯誤為現有 `Problem`。credential JSON 採 WebAuthn Level 3 JSON 序列化（base64url）。公開 auth 端點的 request body 上限 64 KB（超過 413 `payload_too_large`），在解析與密碼學運算之前檢查。

### 5.1 公開（`security: []`）

| 操作 | 輸入 | 成功 | 錯誤 |
|---|---|---|---|
| `POST /v1/auth/registration/options` | `{enrollment_token, display_name?}`（`display_name` 1–80 字、不含控制字元，只用於 invite） | 200 `{ceremony_id, public_key}`（CreationOptions）＋設定或延長 binding cookie | 400 `enrollment_token_invalid`；409 `enrollment_token_used`、`enrollment_token_expired`；403 `account_disabled`、`admission_disabled`；422 `validation_error`；429 `rate_limited` |
| `POST /v1/auth/registration/verify` | `{ceremony_id, credential, label?}` | 200 `Profile`＋session cookie | 400 `ceremony_invalid`、`registration_failed`；409 `credential_exists`、`enrollment_token_used`；403 `account_disabled`、`admission_disabled` |
| `POST /v1/auth/login/options` | `{}` | 200 `{ceremony_id, public_key}`（RequestOptions，`allowCredentials` 空）＋binding cookie | 429 `rate_limited` |
| `POST /v1/auth/login/verify` | `{ceremony_id, credential}` | 200 `Profile`＋session cookie | 400 `ceremony_invalid`；401 `authentication_failed`；403 `account_disabled`（證明有效後） |

CreationOptions：`rp {id, name}`；`user {id: user_id 16 bytes, name: "<display_name 或 'Daily Brief'> · <APP_HOST>", displayName}`；`pubKeyCredParams` 必要 ES256(-7)、RS256(-257)，EdDSA(-8) 只有在固定版本的函式庫與瀏覽器測試通過時才加入；`authenticatorSelection {residentKey: required, userVerification: required}`；`attestation: none`；`timeout: 300000`；`excludeCredentials`：`register_add` 與 `add_passkey` 列出該使用者的有效 credential，`register_recovery` 不列（舊的會被全部撤銷）。

### 5.2 需要 session（`sessionCookie`）

| 操作 | 成功 | 需要 reauth | 錯誤 |
|---|---|---|---|
| `POST /v1/auth/logout`（無 body） | 204，撤銷目前 session，清除 cookie | 否 | 401 |
| `POST /v1/auth/reauth/options` | 200 `{ceremony_id, public_key}`（`allowCredentials`＝該使用者有效 credential） | — | 401 |
| `POST /v1/auth/reauth/verify` | 204，設定目前 session 的 `reauth_at` | — | 400 `ceremony_invalid`；401 `authentication_failed` |
| `GET /v1/me/passkeys` | 200 `{passkeys: [Passkey]}` | 否 | 401 |
| `POST /v1/me/passkeys/options` | 200 `{ceremony_id, public_key}` | 是 | 403 `reauth_required` |
| `POST /v1/me/passkeys/verify` | 201 `Passkey` | 是（verify 時再檢查） | 400 `ceremony_invalid`、`registration_failed`；403 `reauth_required`；409 `credential_exists`、`passkey_limit` |
| `DELETE /v1/me/passkeys/{id}` | 204；若是目前 session 使用的 passkey，回應同時清除 cookie | 是 | 403 `reauth_required`；404 `not_found`（不存在或不是自己的）；409 `last_passkey` |
| `GET /v1/me/sessions` | 200 `{sessions: [Session]}` | 否 | 401 |
| `DELETE /v1/me/sessions/{id}` | 204；刪除目前 session 等同登出 | 否 | 404 `not_found` |

- `Passkey`：`id, label, created_at, last_used_at, backup_eligible, backup_state, is_current`（目前 session 是否由它建立）。
- `Session`：`id, created_at, last_seen_at, is_current`。不回傳 token、hash、IP、user agent。
- 現有 `/v1` 業務路由不變，只是驗證方式從 Bearer JWT 改為 session cookie。

### 5.3 移除與調整

- 移除 `POST /v1/auth/redeem-invite`、`ForbiddenAccount` 的 `account_pending`；`Profile` 移除 `email`（**對前端產生的型別是 breaking change**），`status` 只剩 `active`、`disabled`。
- `securitySchemes`：移除 `userJwt`，新增 `sessionCookie`（`apiKey`、`in: cookie`、`name: __Host-brief_session`）；全域預設 `sessionCookie`；§5.1 四個操作與既有的 `/health` 是 `security: []`；`/internal` 維持 `internalToken`。
- 回應遺失：若 verify 已提交但回應沒有送達瀏覽器（例如斷線），token 已被使用、ceremony 已消耗，重送會得到 `ceremony_invalid`。使用者若沒有拿到 session，需要重新登入；若是註冊流程，passkey 已存在，可以直接登入。
- 新增錯誤碼到 OpenAPI components：`origin_mismatch`、`unsupported_media_type`、`payload_too_large`、`reauth_required`、`last_passkey`、`passkey_limit`、`ceremony_invalid`、`registration_failed`、`authentication_failed`、`credential_exists`、`enrollment_token_invalid`、`enrollment_token_used`、`enrollment_token_expired`、`admission_disabled`。
- 不提供 `dev-login` 路由。本機用 CLI `dev-session`（§6）或在 localhost 用真的 passkey。前端不再有 `VITE_DEV_JWT_SECRET` 或任何簽章祕密。

### 5.4 Cookie

| Cookie | 正式環境 | 本機（`APP_ENV=local`） |
|---|---|---|
| session | `__Host-brief_session`；`Secure; HttpOnly; SameSite=Strict; Path=/`；`Max-Age`＝absolute 剩餘秒數 | `brief_session_<COMPOSE_PROJECT_NAME>`；不加 `Secure` |
| browser binding | `__Host-brief_ceremony`；同上屬性；`Max-Age=600` | `brief_ceremony_<COMPOSE_PROJECT_NAME>` |

- 本機名稱加上 compose project，避免同一個 `localhost` 上六個 worktree 互相覆蓋（cookie 不分 port）。
- 清除 cookie 時使用相同名稱與屬性並設 `Max-Age=0`。
- binding cookie：請求帶有格式正確的值就沿用，否則產生新值；兩個分頁可同時進行各自的 ceremony。

### 5.5 Rate limit

| 控制 | 規則 |
|---|---|
| 每個用戶端 IP，公開 auth 端點 | 20 次／10 分鐘（固定視窗，允許邊界時兩倍突發）。IP 從 `CloudFront-Viewer-Address` 解析，**去掉 port**；IPv6 以 /64 計；以 `RATE_LIMIT_KEY` 做 HMAC 後存放 |
| 每個 enrollment token | `options_count` 終身上限 10，原子遞增，不受清理影響 |
| 不採用 | 全域登入上限、每把 credential 失敗次數上限（攻擊者可故意觸發，把 owner 鎖在外面） |
| 選配 | `AUTH_GLOBAL_CIRCUIT_BREAKER`：預設關閉；開啟代表 Kai 接受「用可用性換成本」 |

- 正式環境只信任 CloudFront 產生的標頭，而且前提是 Function URL 已鎖定只接受 CloudFront（DESIGN 11 第 1 項）；標頭缺少或格式錯誤時拒絕請求，絕不退回使用 `X-Forwarded-For`。本機使用連線位址。
- 失敗計數、rate-limit 命中與 token 的 `options_count` 遞增都在業務交易之外提交，請求失敗也不會被回滾。
- 正式環境在 Function URL 鎖定經過驗證之前不對外開放（M2 部署驗收）；per-IP 限制不是加入門檻的依據，加入只取決於 256-bit token。
- 洪水攻擊時，`login_failed` 事件每個 IP 視窗最多記錄一次。

### 5.6 Ceremony 狀態機

```
options ──► created (challenge, binding, purpose, bindings) ──5 min──► expired
   verify: binding/purpose 不符 ─────────────► 400 ceremony_invalid（不消耗）
   verify: 已消耗或過期 ──────────────────────► 400 ceremony_invalid
   verify: 驗證失敗（綁定正確）───────────────► consumed + 失敗事件，提交後回 401/400
   verify: 成功（鎖定後重新檢查通過）─────────► consumed + 所有寫入，同一交易提交
```

各流程的鎖定與檢查（皆遵守 AUTH-4 順序）：

| 流程 | 鎖定 | 鎖定後重新檢查 | 成功時寫入 |
|---|---|---|---|
| invite 註冊 | token → ceremony | token 未使用、未撤銷、未過期；`NON_OWNER_ADMISSION` | profile（`pending_user_id`）、`agent_settings`（`anthropic_api`）、credential、token used、session、ceremony |
| recovery | profile → token → sessions → credentials → ceremony | profile active；token 有效；非 owner 時檢查門檻 | 撤銷舊 credential 與 session、撤銷其他未使用 token、新 credential、token used、新 session、ceremony |
| add_credential | profile → token → credentials → ceremony | 同上 | 新 credential、token used、撤銷其他未使用的 add_credential token、新 session、ceremony |
| 登入 | profile → credential → ceremony | profile active；credential 未撤銷且公鑰未變；計數規則（§5.7） | credential 計數與 `last_used_at`、session、ceremony |
| reauth | profile → session → credential → ceremony | session 有效且為同一使用者；credential 屬於該使用者 | `reauth_at`、ceremony |
| 新增 passkey | profile → session → credentials → ceremony | reauth 仍在 5 分鐘內 | credential、ceremony |
| 刪除 passkey | profile → sessions → credentials | 排除目標後仍有有效 passkey | credential revoked、相關 session revoked |
| 停用（CLI） | profile → tokens → sessions | — | status、撤銷 session 與未使用 token |

「立即撤銷」的意思：撤銷提交之後開始的授權檢查都會失敗；已經通過檢查、正在執行中的請求會完成。

### 5.7 計數器規則

- wrapper 呼叫函式庫時**一律傳入 `current_sign_count=0`**，讓函式庫不對計數做判斷（函式庫可能在簽章驗證之前就因計數拒絕）；簽章驗證成功後，wrapper 才套用下列規則。
- 已存與新值都是 0：通過（同步 passkey 通常永遠是 0），不寫入計數。
- 任一非 0 且新值 ≤ 已存值：`backup_eligible=false` → 401 `authentication_failed`＋`sign_count_regression` 事件；`backup_eligible=true` → 放行，只記錄事件。不自動撤銷。
- 其他情況通過。寫入 `sign_count = greatest(sign_count, $new)`（登入時 profile 已鎖定，不會並行覆蓋）；更新 0 筆不是錯誤。
- wrapper 絕不略過簽章驗證；針對固定版本的函式庫測試這些規則。

## 6. 管理 CLI（`api/app/auth_admin.py`，在 Kai 的電腦上執行）

| 指令 | 作用 |
|---|---|
| `bootstrap-owner` | 若沒有 `OWNER_USER_ID` 的 profile 就建立（active，`agent_settings` 用 `claude_subscription`）；發 1 小時 `recovery` token（撤銷舊的）；印出 `https://<APP_HOST>/enroll#t=<token>` |
| `issue-invite [--days 7]` | `NON_OWNER_ADMISSION` 不是 true 就拒絕 |
| `issue-recovery --user <uuid> [--hours 1]` | 預設全部替換；非 owner 受門檻限制 |
| `issue-add-credential --user <uuid> [--hours 1]` | 保留舊 passkey，新增一把 |
| `list-tokens`、`revoke-token <id>` | 不印出 token 本身 |
| `disable-user <uuid>`、`enable-user <uuid>` | 停用時撤銷 session 與未使用 token；啟用不恢復任何東西 |
| `revoke-sessions --user <uuid>` | 撤銷該使用者所有 session |
| `dev-session`（僅 `APP_ENV=local` 且連到本機 DB） | 建立 `kind='dev'` session，印出 cookie 值供 curl 與 demo 腳本使用 |

- 每個會修改資料的指令先印出目標環境與資料庫主機（不含密碼），要求確認。
- 所有動作寫入 `auth_events`（`actor='cli'`），不含 token。
- 發 recovery 前，Kai 必須透過另一個已知管道確認對方身分（例如當面、既有的聊天帳號）。

## 7. BDD 規格

標籤：`@unit`（偽造 assertion、純邏輯）、`@integration`（真的本機 Postgres，含並行）、`@e2e`（Playwright Chromium 虛擬 authenticator）、`@manual`（Touch ID／Face ID、Safari、CDA 實機）。

```gherkin
Feature: F01 Owner bootstrap

  Background:
    Given WEBAUTHN_RP_ID is "brief.example.com"
    And WEBAUTHN_ORIGINS is exactly "https://brief.example.com"
    And OWNER_USER_ID is "00000000-0000-4000-8000-000000000001"

  @integration
  Scenario: First bootstrap creates the owner and a one-hour link
    Given no profile exists for OWNER_USER_ID
    When Kai runs "auth_admin bootstrap-owner" and confirms the target
    Then a profile with user_id OWNER_USER_ID and status "active" exists
    And its agent_settings runner is "claude_subscription"
    And one unused "recovery" token for the owner expires in 1 hour
    And the database stores only the SHA-256 of the token
    And an auth event with actor "cli" and event "token_issued" exists

  @integration
  Scenario: Bootstrapping again supersedes the previous link
    Given the owner exists and an unused bootstrap link L1
    When Kai runs "auth_admin bootstrap-owner" again
    Then L1 is revoked
    And registration/options with L1 returns 400 "enrollment_token_invalid"

  @manual
  Scenario: Owner registers the first passkey
    Given Kai opens the enrollment URL in Safari on macOS
    Then the token is removed from the address bar before any other script runs
    When Kai completes Touch ID
    Then GET /v1/me returns the owner profile
    And the session cookie is "__Host-brief_session" with Secure, HttpOnly and SameSite=Strict

Feature: F02 Invite-only registration and the non-owner gate

  @unit
  Scenario: CLI refuses invites while non-owner admission is off
    Given NON_OWNER_ADMISSION is "false"
    When Kai runs "auth_admin issue-invite"
    Then the CLI exits with an error that mentions DESIGN 3.1
    And no token is created

  @integration
  Scenario: An already-issued invite stops working when the gate closes
    Given NON_OWNER_ADMISSION was "true" when invite token T was issued
    And NON_OWNER_ADMISSION is now "false"
    When registration/options is called with T
    Then the response is 403 "admission_disabled"
    And no ceremony is created

  @e2e
  Scenario: Invited user registers
    Given NON_OWNER_ADMISSION is "true"
    And an unused invite token
    When the invitee enters display name "Mei" and completes the ceremony
    Then a new profile with a new user_id and status "active" exists
    And its agent_settings runner is "anthropic_api"
    And the token is used by that profile
    And the invitee is signed in with reauth_at null

  @integration
  Scenario: An invited user never gets a subscription job
    Given an active invited profile with an active passkey and an enabled slot
    When the scheduler tick runs
    Then no job with runner "claude_subscription" exists for that profile

  @unit
  Scenario Outline: An unusable token never creates anything
    Given an invite token that is <state>
    When registration/options is called with it
    Then the response is <status> "<code>"
    And no ceremony and no profile exist

    Examples:
      | state   | status | code                     |
      | unknown | 400    | enrollment_token_invalid |
      | revoked | 400    | enrollment_token_invalid |
      | used    | 409    | enrollment_token_used    |
      | expired | 409    | enrollment_token_expired |

  @integration
  Scenario: Token revoked between options and verify
    Given a registration ceremony was created with token T
    When T is revoked and registration/verify is called with a valid credential
    Then the response is 400 "enrollment_token_invalid"
    And no profile or credential exists
    And the ceremony is consumed

  @integration
  Scenario: The gate closes between options and verify
    Given NON_OWNER_ADMISSION is "true" and a registration ceremony was created with invite token T
    When NON_OWNER_ADMISSION becomes "false" and registration/verify is called with a valid credential
    Then the response is 403 "admission_disabled"
    And no profile exists and T remains unused

  @integration
  Scenario: The token expires between options and verify
    Given a registration ceremony created with invite token T that expires in 2 minutes
    When the clock moves 3 minutes forward and registration/verify is called with a valid credential
    Then the response is 409 "enrollment_token_expired"
    And no profile, credential or session exists and T remains unused

  @integration
  Scenario: A crash before commit leaves nothing behind
    Given registration/verify with token T fails after the profile insert but before commit
    Then no profile, credential or session exists and T remains unused
    And the ceremony is not consumed
    When the same browser calls registration/verify again while T and the ceremony are valid
    Then it succeeds exactly once

  @integration
  Scenario: A lost verify response cannot be replayed
    Given registration/verify with token T committed but the response never reached the browser
    When the browser retries registration/verify with the same ceremony
    Then the response is 400 "ceremony_invalid"
    And exactly one profile and one credential exist

  @integration
  Scenario: Two browsers race with the same invite
    Given browsers A and B each created a registration ceremony with token T
    When both call registration/verify at the same time
    Then exactly one succeeds and exactly one profile and one credential exist
    And the other gets 409 "enrollment_token_used"

  @integration
  Scenario: The token budget survives cleanup
    Given token T has options_count 10 and is still valid
    When the cleanup tick runs
    And registration/options is called with T
    Then the response is 429 "rate_limited"

  @unit
  Scenario Outline: Display name is validated
    When registration/options is called with a valid invite and display_name <value>
    Then the response is 422 "validation_error"

    Examples:
      | value                 |
      | ""                    |
      | 81 characters         |
      | a control character   |

  @integration
  Scenario: A duplicate credential id is rejected without using the token
    Given credential id C is already stored (active or revoked)
    When registration/verify presents a new attestation with credential id C
    Then the response is 409 "credential_exists"
    And the token remains unused
    And the ceremony is consumed

  @unit
  Scenario: Signing in never creates a profile
    Given an assertion whose credential and user handle match no stored credential
    When login/verify is called
    Then the response is 401 "authentication_failed"
    And no profile exists

Feature: F03 Passkey sign-in

  Background:
    Given the owner has an active passkey

  @e2e
  Scenario: Discoverable sign-in on the same device
    When the sign-in page calls login/options
    Then the options have an empty allowCredentials and userVerification "required"
    When the user verifies with the authenticator and the page calls login/verify
    Then a new session with a new random token exists
    And the credential's last_used_at is updated
    And an auth event "login_succeeded" exists

  @manual
  Scenario: Sign-in on another computer with a phone (native cross-device)
    Given Kai is on a computer without the passkey
    When the browser offers a phone, Kai scans the browser's QR code and approves with Face ID
    Then login/verify succeeds exactly as on the same device
    And the API has no QR endpoint

  @unit
  Scenario Outline: Assertion checks fail closed
    Given a login ceremony bound to browser A
    When A calls login/verify with an assertion where <defect>
    Then the response is 401 "authentication_failed" with the same body for every row
    And no session exists
    And the ceremony is consumed

    Examples:
      | defect                                                  |
      | the origin is "https://evil.example"                    |
      | clientDataJSON.crossOrigin is true                      |
      | the RP ID hash is for another domain                    |
      | the challenge does not match the ceremony               |
      | the user presence flag is not set                       |
      | the user verification flag is not set                   |
      | the credential is revoked                               |
      | the credential id is unknown                            |
      | the userHandle differs from the credential's user_id    |
      | the userHandle is missing                               |
      | the backup eligible flag differs from the stored value  |
      | the signature is invalid                                |

  @integration
  Scenario: A wrong browser cannot consume someone else's ceremony
    Given browser A created a login ceremony
    When browser B calls login/verify with A's ceremony_id
    Then the response is 400 "ceremony_invalid"
    And A's ceremony is still unconsumed

  @integration
  Scenario: Two tabs in one browser
    Given tab 1 and tab 2 in one browser each called login/options
    When tab 1 completes login/verify
    Then it succeeds
    And tab 2's ceremony is still usable until it expires

  @unit
  Scenario: Replay and expiry
    Given a login ceremony that already produced a session
    When login/verify is called again with it
    Then the response is 400 "ceremony_invalid"
    Given another login ceremony created 6 minutes ago
    When login/verify is called with it
    Then the response is 400 "ceremony_invalid"

  @unit
  Scenario Outline: A ceremony only works at its own endpoint
    Given a ceremony created for purpose "<created>"
    When "<endpoint>" is called with an otherwise valid response
    Then the response is 400 "ceremony_invalid"
    And no session or credential is created

    Examples:
      | created     | endpoint            |
      | login       | registration/verify |
      | reauth      | login/verify        |
      | login       | reauth/verify       |
      | add_passkey | login/verify        |

  @unit
  Scenario Outline: Signature counter rules
    Given a credential with stored sign_count <stored> and backup_eligible <be>
    When a valid assertion arrives with sign_count <new>
    Then the result is <result>

    Examples:
      | stored | be    | new | result                                           |
      | 0      | true  | 0   | success                                          |
      | 41     | false | 42  | success                                          |
      | 41     | false | 12  | 401 "authentication_failed" and a regression event |
      | 41     | false | 41  | 401 "authentication_failed" and a regression event |
      | 41     | true  | 12  | success and a regression event                   |

  @integration
  Scenario: Sign-in racing with credential revocation
    Given a login assertion for passkey P passed signature verification
    When P is revoked before the session is written
    Then the response is 401 "authentication_failed"
    And no session exists

  @unit
  Scenario: Disabled account with a valid passkey
    Given the profile is disabled
    When login/verify is called with a valid assertion
    Then the response is 403 "account_disabled"
    And no session exists

Feature: F04 Session lifecycle

  @integration
  Scenario: Every request checks session, credential and profile in the database
    Given Kai has a valid passkey session
    When the profile status is set to "disabled" directly in the database
    And Kai calls GET /v1/reports
    Then the response is 401 "unauthorized"

  @integration
  Scenario: Logout takes effect immediately
    When Kai calls POST /v1/auth/logout with the exact Origin and no body
    Then the response is 204
    And the cookie is cleared with the same name and attributes and Max-Age=0
    And the old cookie value gets 401 "unauthorized"

  @integration
  Scenario: Idle renewal and expiry with a controlled clock
    Given a session with last_seen_at 2 hours ago and idle_expires_at in 5 days
    When it is used
    Then last_seen_at is now and idle_expires_at is 30 days from now, capped by absolute_expires_at
    Given a session whose idle_expires_at has passed
    When it is used
    Then the response is 401 "unauthorized"
    And its idle_expires_at is not extended

  @integration
  Scenario: Absolute expiry even when active
    Given a session whose absolute_expires_at has passed
    When it is used
    Then the response is 401 "unauthorized"

  @integration
  Scenario: Revoking one of my sessions
    Given Kai has sessions on a Mac and an iPhone
    When Kai calls DELETE /v1/me/sessions/{iPhone session id} from the Mac
    Then the iPhone session is revoked and the Mac session stays valid

  @integration
  Scenario: Another user's ids are not found
    Given users U1 and U2 each have a session and a passkey
    When U1 deletes U2's session id or U2's passkey id
    Then the response is 404 "not_found"
    And nothing changes

  @integration
  Scenario: Re-enabling does not revive anything
    Given user U had sessions and an unused recovery token
    When Kai disables U and then enables U
    Then the old sessions and the old token stay revoked

  @unit
  Scenario: Session list does not expose secrets
    When Kai calls GET /v1/me/sessions
    Then each entry has only id, created_at, last_seen_at and is_current

Feature: F05 Reauthentication

  @integration
  Scenario: Deleting a passkey needs a recent reauth
    Given Kai signed in 2 hours ago and has not reauthenticated
    When Kai calls DELETE /v1/me/passkeys/{id}
    Then the response is 403 "reauth_required"
    When Kai completes reauth/options and reauth/verify
    And calls DELETE /v1/me/passkeys/{id} within 5 minutes
    Then the passkey is revoked

  @unit
  Scenario: Reauth with another user's credential fails
    Given users U1 and U2 each have a passkey and U1 has session S1
    When S1 completes reauth/verify with an assertion from U2's credential
    Then the response is 401 "authentication_failed"
    And S1's reauth_at is unchanged

  @unit
  Scenario: Reauth evidence is bound to its session
    Given a reauth ceremony created for session S1
    When session S2 of the same user calls reauth/verify with it
    Then the response is 400 "ceremony_invalid"

  @integration
  Scenario: Reauth expires between add options and add verify
    Given Kai reauthenticated 4 minutes ago and called passkeys/options
    When Kai calls passkeys/verify 2 minutes later
    Then the response is 403 "reauth_required"

Feature: F06 Passkey management

  @e2e
  Scenario: Add a passkey on a new device after cross-device sign-in
    Given Kai signed in on a PC using the phone and reauthenticated
    When Kai adds a passkey from the PC
    Then excludeCredentials lists Kai's active credentials
    And the new passkey belongs to the same user_id

  @integration
  Scenario: A revoked credential id can never be registered again
    Given credential C was revoked 200 days ago and cleanup has run many times
    When an authorized registration presents credential id C
    Then the response is 409 "credential_exists"

  @integration
  Scenario: Per-user limits
    Given Kai has 10 active passkeys and a recent reauth
    When Kai calls passkeys/verify with a new credential
    Then the response is 409 "passkey_limit"
    Given Kai has 20 active sessions
    When Kai signs in again
    Then the oldest session is revoked with reason "limit"

  @integration
  Scenario: The last passkey cannot be deleted
    Given Kai has exactly one active passkey and a recent reauth
    When Kai deletes it
    Then the response is 409 "last_passkey"

  @integration
  Scenario: Concurrent deletions cannot remove every passkey
    Given Kai has exactly two active passkeys P and Q and recent reauth on two sessions
    When the two sessions delete P and Q at the same time
    Then one deletion succeeds and the other returns 409 "last_passkey"
    And exactly one active passkey remains

  @integration
  Scenario: Deleting the passkey behind the current session
    Given session S1 was created with passkey P and Kai also has passkey Q
    When S1 deletes P with a recent reauth
    Then the response is 204 and clears the session cookie
    And S1 and every other session created with P are revoked with reason "passkey_revoked"
    And the next request with S1's cookie gets 401

Feature: F07 Recovery

  @integration
  Scenario: Recovery replaces every old credential and session by default
    Given user U has passkeys P1 and P2 and two sessions
    When Kai issues a recovery link for U and U registers a new passkey with it
    Then P1 and P2 are revoked
    And both old sessions are revoked with reason "recovery"
    And U has exactly one active passkey and one new session

  @integration
  Scenario: A newer recovery link supersedes an older one
    Given Kai issued recovery link R1 for U and then recovery link R2 for U
    When the holder of R1 calls registration/options
    Then the response is 400 "enrollment_token_invalid"

  @integration
  Scenario: Completed recovery invalidates other grants and ceremonies
    Given an add_credential token A and an open ceremony for U
    When U completes recovery with token R
    Then A and the open ceremony can no longer add a credential or create a session

  @integration
  Scenario: Two recoveries for the same user at the same time
    Given recovery token R for user U and two browsers with ceremonies for R
    When both call registration/verify at the same time
    Then exactly one succeeds
    And U has exactly one active passkey and one active session

  @integration
  Scenario: Add-credential keeps existing passkeys
    Given user U has passkey P1
    When U registers with an add_credential token
    Then P1 stays active and U has two active passkeys

  @integration
  Scenario: Recovery never creates a profile or enables an account
    Given user U is disabled and has a recovery token issued before the disable
    When registration/options is called with that token
    Then the response is 400 "enrollment_token_invalid"
    And U stays disabled

  @integration
  Scenario: Non-owner recovery follows the gate
    Given NON_OWNER_ADMISSION is "false" and user U is not the owner
    When Kai runs "auth_admin issue-recovery --user U"
    Then the CLI refuses
    And a recovery token for U issued earlier gets 403 "admission_disabled" at registration/options

  @manual
  Scenario: Owner break-glass
    Given Kai lost every passkey
    When Kai runs "auth_admin bootstrap-owner" against production and confirms the target
    Then the old owner passkeys and sessions are revoked when the new passkey is registered

Feature: F08 Request protection

  @integration
  Scenario Outline: Unsafe browser requests need exactly one trusted Origin
    When a <method> request to <path> has Origin <origin>
    Then the response is 403 "origin_mismatch"
    And no state changes

    Examples:
      | method | path                  | origin                                       |
      | POST   | /v1/auth/login/verify | "https://evil.example"                       |
      | POST   | /v1/auth/logout       | (missing)                                    |
      | PATCH  | /v1/me                | "http://brief.example.com"                   |
      | DELETE | /v1/me/sessions/{id}  | "null"                                       |
      | POST   | /v1/jobs              | two Origin headers                           |

  @integration
  Scenario: The worker is independent of browser Origin
    When a request with a valid service token and no Origin calls POST /internal/jobs/claim
    Then the existing internal contract applies
    When the same request also carries a session cookie
    Then the response is 401 "unauthorized"

  @integration
  Scenario: Credential families stay separate
    When a /v1 request carries X-Internal-Token or "Authorization: Bearer x"
    Then the response is 401 "unauthorized"

  @unit
  Scenario: Media types and sizes
    When POST /v1/auth/login/options is sent as application/x-www-form-urlencoded
    Then the response is 415 "unsupported_media_type" and no ceremony exists
    When POST /v1/auth/registration/verify has a 65 KB body
    Then the response is 413 "payload_too_large" before any decoding
    When POST /v1/jobs has an empty body and the exact Origin
    Then the request is accepted by the media-type check

  @integration
  Scenario: Login CSRF is blocked
    Given an attacker page on another site
    When it submits a login/verify request with the attacker's own assertion
    Then the response is 403 "origin_mismatch"
    And the victim's browser receives no session cookie

  @integration
  Scenario: Per-IP limit on public auth endpoints
    Given one client IP sent 20 requests to public auth endpoints in 10 minutes
    When it sends another, even from a different source port
    Then the response is 429 "rate_limited" with Retry-After

  @integration
  Scenario: Untrusted ingress metadata is refused in production
    Given APP_ENV is "production"
    When a public auth request has no CloudFront-Viewer-Address or a malformed one
    Then the request is rejected
    And X-Forwarded-For is never used

  @integration
  Scenario: A known credential id cannot be used to lock the owner out
    Given an attacker sends 100 invalid assertions naming the owner's credential id from many IPs
    When the owner signs in with a valid assertion
    Then the owner signs in successfully

  @integration
  Scenario: API responses are never cached
    When any /api response is returned, including errors
    Then it has "Cache-Control: no-store" and a problem+json body for errors

Feature: F09 Environment, local development and tests

  @unit
  Scenario: APP_ENV is required
    Given APP_ENV is unset or "prod"
    When the API or the CLI starts
    Then startup fails with a configuration error

  @integration
  Scenario: Dev sessions are rejected outside local
    Given a session row with kind "dev" exists in the database
    And APP_ENV is "production"
    When a request uses its cookie
    Then the response is 401 "unauthorized"

  @unit
  Scenario: dev-session only runs locally
    Given APP_ENV is "production"
    When Kai runs "auth_admin dev-session"
    Then the CLI refuses

  @integration
  Scenario: Worktrees do not share local cookies
    Given the stacks "brief-main" and "brief-api" both run on localhost
    When Kai signs in on each
    Then the cookies are "brief_session_brief-main" and "brief_session_brief-api"
    And signing in on one does not sign out the other

  @manual
  Scenario: Real passkeys on localhost
    Given the web app runs at http://localhost:5173 with RP ID "localhost"
    When Kai registers and signs in with Touch ID in Chrome and in Safari
    Then both work without Secure cookies

  @e2e
  Scenario: Automated ceremonies
    Given a Playwright Chromium virtual authenticator with user verification
    When the suite registers with an invite, signs in, reauthenticates, adds and deletes a passkey
    Then every step passes without a real device

  @unit
  Scenario: The browser bundle holds no signing secret
    When the production web build is inspected
    Then it contains no session, JWT or dev signing secret

Feature: F10 Data protection and migration

  @integration
  Scenario: New auth tables are default deny
    Given the migrations are applied
    Then every table in section 4.2 has row level security enabled
    And no policy applies to PUBLIC, anon or authenticated
    And those roles cannot select, insert, update, delete or truncate any table

  @integration
  Scenario: Backend roles work under RLS, client roles do not
    Given the roles brief_runtime and brief_admin exist without BYPASSRLS and without table ownership
    Then brief_runtime can sign in, validate a session and read reports
    And brief_runtime cannot insert into enrollment_tokens
    And brief_admin can run bootstrap-owner and issue-recovery
    And PUBLIC, anon and authenticated can do none of these

  @integration
  Scenario: Secrets never reach logs or audit
    When the e2e suite runs with log capture
    Then no captured log contains a token, challenge, binding value, cookie value or request body

  @integration
  Scenario: Cleanup is bounded and never ends live grants
    When the scheduler tick runs
    Then at most 1000 rows per table are deleted per tick per section 4.3
    And no unused, unexpired token is deleted

  @integration
  Scenario: Upgrading the seeded local database preserves data
    Given a local database with the seeded owner, six subscriptions, slots, reports and marks
    When the auth migration runs
    Then the owner's user_id, reports, marks, settings, subscriptions and slots are unchanged
    And profiles has no email column

  @integration
  Scenario: Migration stops on unexpected legacy admission state
    Given a profile with status "pending" exists
    When the auth migration runs
    Then it stops with an error listing the pending profiles
    And no profile is promoted or deleted

  @integration
  Scenario: Scheduler eligibility in production
    Given APP_ENV is "production" and the owner has no active passkey
    When the scheduler tick runs
    Then no job is created for the owner
```

## 8. DESIGN 修改清單（核准後由 lead 寫入）

| DESIGN 段落 | 修改 |
|---|---|
| 1 目標 | 「Google sign-in with restricted registration」→「passkey sign-in, invite-only registration」 |
| 2.1 帳號 | 第 4 列 Supabase：只需資料庫，關閉 Data API，不需 service role key／public key；建立 runtime 與 admin 兩個 DB 角色。刪除第 5 列 Google OAuth。第 8 列 owner：改為 `OWNER_USER_ID` 與 bootstrap CLI，不需 email |
| 2.3 secret | 移除 Supabase service role key、public key、owner email；新增 `RATE_LIMIT_KEY`（AWS）、admin DB 連線字串（只在 Kai 的密碼管理器）；runtime `DATABASE_URL` 不變 |
| 3 決定 | Sign-in 列改為「自建 WebAuthn passkey（py_webauthn），session cookie，邀請制」；新增 3.4「Auth 原則」摘要 §0 與 §3 |
| 4.1／4.4 | 架構圖加 auth 路由與 CLI；`AuthVerifier` 改為 `SessionAuthenticator`；4.4 加 RP ID 段落 |
| 4.5 本機 | `APP_ENV` 必填；每個 worktree 的 `WEBAUTHN_ORIGINS` 與 cookie 名稱；dev 用 CLI `dev-session` |
| 5 資料表 | 依 §4 |
| 6 API | 依 §5；「Registration gate」段落改為邀請制與 AUTH-14 |
| 8 安全 | AUTH-1～16；CSP 與回應標頭；CloudFront cookie／no-store／SPA fallback；PWA 不快取 `/api`；RLS 規則由「no policies」改為「client 角色沒有 policy，只有後端角色有 policy」（§4.4），也套用到既有資料表 |
| 11 待確認 | 見 §12 |
| 12 里程碑 | M1：本機 passkey 註冊與登入可用（虛擬 authenticator E2E＋手動 Touch ID）。M2：正式 RP ID、owner bootstrap、§12 的部署驗收。Later：iOS app（Associated Domains、另立 session 傳遞合約） |

## 9. 對現有工作的影響

- **db：** 依 §10 遷移；新增六張表與權限；seed 保留 owner、六個預設主題與時段，移除 email。
- **api：** `config.py` 的 `APP_ENV` 改為必填；`auth.py` 改為 session 驗證；移除 `profiles.py` 在 GET 時自動建立 profile 的邏輯（改為只查詢）；新增 auth 路由、WebAuthn wrapper、`auth_admin` CLI、rate limit、清理；scheduler 加上 AUTH-16；移除 `dev_token`。
- **web：** 移除 `VITE_DEV_JWT_SECRET` 與前端簽 JWT；新增 `/enroll`、登入、安全性頁面；`fetch` 用 `credentials: "same-origin"`；`GET /v1/me` 回 401 時顯示登入頁；處理 `reauth_required`。
- **worker：** 無影響。
- **infra（M2）：** CloudFront 行為、回應標頭、Function URL 鎖定方式、Supabase Data API 關閉與 DB 角色。
- **本機 demo 腳本：** `demo-run.sh` 改用 `auth_admin dev-session`。

## 10. 遷移與切換

1. 遷移前檢查：若有 `status='pending'` 的 profile 或 `OWNER_USER_ID` 以外的 profile，**停止並列出**，等 Kai 決定，不自動升級或刪除。
2. 依序移除：`invite_redemptions` → `invite_codes` → `allowed_emails`；`alter table profiles drop column email`（索引與檢查一併移除）；替換 `status` 檢查。
3. 建立 §4.2 的資料表、trigger、RLS、權限；建立 `brief_migrator`、`brief_runtime`、`brief_admin` 角色，並為**既有的業務資料表**也加上 §4.4 的後端角色 policy（否則 runtime 角色讀不到現有資料）。
4. 保留所有 `profiles.user_id`、`OWNER_USER_ID` 與業務資料。
5. api、web、seed、設定、測試 fixture 一起切換；舊的 JWT 與邀請碼刻意失效。
6. 降級不是無損的（email 與舊邀請資料會被刪除）；正式環境目前沒有資料，影響只在本機。

## 11. Later：原生 app

iOS app 用同一個 RP ID（`apple-app-site-association` 的 `webcredentials`，經同一個 CloudFront 提供）。原生 app 沒有 cookie 與 Origin，session 傳遞方式需要另立合約與威脅模型，不沿用本文的 cookie 規則。

## 12. 待確認（unverified，記錄到 VERIFIED.md）

1. `py_webauthn` 固定版本：options／verify API、UV 參數、計數器在簽章前拒絕的行為、EdDSA、`crossOrigin` 檢查；Lambda 套件大小與冷啟動（WebAuthn 模組延遲載入）。
2. 瀏覽器：L3 JSON 方法（`parseCreationOptionsFromJSON` 等）在 Safari／Chrome／Firefox 的支援；不足時用固定版本的轉換工具。
3. Safari 在 `http://localhost` 的 cookie 與 WebAuthn 行為（本草稿本機不用 `Secure`）。
4. CloudFront：Function URL 鎖定方式（OAC 需要 POST／PUT 的 body SHA-256 標頭，或自訂祕密標頭）、`CloudFront-Viewer-Address` 轉送與清除、Lambda adapter 傳回多個 `Set-Cookie`、SPA fallback 不影響 `/api/*`。
5. Supabase：關閉 Data API 後 migration 與 runtime 連線正常；`anon`／`authenticated` 的預設權限；CLI 在 IPv4 網路使用 session pooler。
6. CDA 實機矩陣：macOS Safari／Chrome＋iPhone，Windows Chrome＋iPhone。

## 13. 需要 Kai 決定

| # | 問題 | 建議 |
|---|---|---|
| K1 | session：idle 30 天、absolute 90 天、reauth 5 分鐘。接受「cookie 被偷時，到撤銷或過期前都能被重放」 | 照建議 |
| K2 | 非 owner 加入與復原在 `AnthropicApiRunner` 完成前鎖住（AUTH-14） | 照建議（DESIGN 3.1） |
| K3 | 移除 `profiles.email` | 移除 |
| K4 | 正式 RP ID 的主機名稱（上線後不能改） | `brief.<你的網域>` |
| K5 | invite 7 天；recovery／add_credential **1 小時**（v0.1 是 24 小時） | 照建議 |
| K6 | 管理操作只用 CLI（POC 不做管理頁面） | 照建議 |
| K7 | 全域 circuit breaker 預設關閉 | 關閉 |
| K8 | §0.1 延後的功能（重新命名 passkey、登出其他所有裝置等）放到 M3 再評估 | 照建議 |
