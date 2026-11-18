> **Superseded (2026-11-15).** Kept as reference only. The adopted design is [`docs/auth-passkey-design.md`](../auth-passkey-design.md) and DESIGN section 3.4. Not an implementation plan; do not hand section 14 to an agent.

# WebAuthn / Passkey + Cross-device QR Authentication — BDD Specification

> **Document type:** Product / Security / API / Database / BDD / Local Demo Specification  
> **Status:** Proposed, implementation-ready specification (not a completed implementation)  
> **Version:** 1.0  
> **Updated:** 2026-11-14  
> **Language:** 繁體中文（Gherkin 使用英文，方便測試工具與 Coding Agent 使用）  
> **Target stack:** Browser WebAuthn API + Python 3.11+ Backend（Local FastAPI / Production AWS Lambda）+ PostgreSQL（Local Docker / Production Supabase hosted PostgreSQL）

## 0. Executive decision / TL;DR

### 0.1 核心目標

打造**不需密碼**、可以在 Web / iOS / Android 使用、允許多把 Passkey 對應同一個 Application User 的 Auth 系統，並支援 QR Code 進行兩種可明確區分的授權：

1. **QR One-time Login：** 裝置 B 顯示 QR，已登入的裝置 A 掃描並使用 Passkey 重新驗證，明確授權 B 建立一次性、短效、無 Refresh Token 的 Session；**不替 B 建立 Passkey**。
2. **QR Passkey Enrollment：** B 顯示 QR，A 掃描並重新驗證後，B 獲得**只准註冊新 Passkey**的短效授權；B 必須在本機完成 WebAuthn Registration 才能正式擁有自己的 Credential。

**另外有第三種不同機制：FIDO Native Cross-device Authentication (CDA)**，瀏覽器／作業系統產生 QR，使用手機上的既有 Passkey 完成 B 的登入；**不是**自行設計的 QR 授權 API。建議先支援標準 WebAuthn，讓支援 CDA 的瀏覽器自動提供這個 UX，不自行實作 CTAP / BLE / FIDO Hybrid Transport。

### 0.2 首次註冊（Bootstrap）推薦

- **MVP Default：Email OTP 驗證信箱 → `pending_passkey` User → 註冊第一把 Passkey → 啟用 User。**
- **Optional：Google OIDC → Backend 驗證 ID Token／OIDC State+Nonce／Issuer／Audience／Expiry → 建立或安全地連結 Identity → 註冊第一把 Passkey。**
- **Google Authenticator (TOTP) 不是 Google 登入，也無法單獨證明 Email 所有權。** TOTP 可在已建立帳號後作為額外驗證方式。
- **Local Email Provider：Mailpit（免費、信件不會寄往真實網際網路）。** 正式環境可換 Amazon SES。
- 首次註冊的 OTP 僅獲得 `registration` 權限，不直接開放 Business API。
- **不得**用相同 Email 自動合併已存在且不同 Auth Identity 的使用者；需要登入既有帳號後執行明確的 Account Linking。

### 0.3 刻意不做（MVP Non-goals）

- 不實作 Google/Apple 密碼管理器之間的 Passkey 同步；由使用者的 Passkey Provider 管理。
- 不實作 CTAP、BLE、FIDO Hybrid QR 協定。
- 不儲存指紋／臉部影像、Private Key。
- MVP 不提供 Password Login；可後續擴充 Social Login 或 Apple OIDC。
- 不以 Email、Phone、Device ID 作為 Application 的 Primary Key。
- 不承諾自訂 QR 的安全性等同原生 FIDO CDA：QR + 人工批准屬可被釣魚的跨裝置授權模式。

---

## 1. Context / Architecture

```mermaid
flowchart TD
  A[Browser / iOS WebView? / Android Browser] --> B[Same-origin Web Frontend]
  B --> C[Auth API: FastAPI local / Lambda production]
  C --> D[(PostgreSQL)]
  C --> E[EmailSender Interface]
  E --> F[Mailpit local]
  E --> G[Amazon SES production]
  B --> H[Browser WebAuthn API]
  H --> I[Passkey Provider: Apple / Google / Others]
  C -. optional OIDC validation .-> J[Google Identity]
```

Production：`https://auth.example.com` 可同時提供 Frontend 與 `/api`；RP ID 建議固定為 `example.com` 或 `auth.example.com`（須在正式環境確認），WebAuthn Expected Origin 明列允許的 HTTPS Origin。**選定 RP ID 後不可隨意更改**，否則既有 Passkey 通常不能沿用。

Local：`http://localhost:3000` 為 Frontend，反向 Proxy `/api` 到 `http://localhost:8000`，RP ID=`localhost`，Expected Origin=`http://localhost:3000`。Passkey API 在 `localhost` 被視為安全上下文；**真實手機直接連筆電 LAN IP 的 `http://192.168.x.x` 通常不可用於 WebAuthn**，應用可信任 HTTPS 域名及正確 RP ID 測試。

**Boundary**：`domain/users` 不依賴 WebAuthn、Google、Mailpit、SES；`infrastructure/auth` 與 `infrastructure/email` 提供 Adapter；`sessions` / `passkey_credentials` 與 `device_pairings` 都是 Authentication Context 的實作細節。

### 1.1 Identity mapping

```text
users.id = Internal UUID （唯一業務識別）
users.webauthn_user_handle = stable random bytes （WebAuthn user.id）
passkey_credentials.credential_id = 單把 Passkey 的 opaque ID
user_identities.(provider, provider_subject) = google/apple/email 的身份對應
sessions.id = 各裝置自己的 Session（不等於 Passkey）
```

同一個人能有 N 個 Credentials 與 N 個 Sessions：不同平台可使用不同 Private Key，最後都映射至相同的 `users.id`。同一把同步型 Passkey 也可能被不同裝置共用，不可用 `credential_id` 當作精確 Device Identifier。

---

## 2. Domain glossary / Ubiquitous language

| 名稱 | 定義 |
|---|---|
| User | Business Domain 的內部使用者、擁有不可變 UUID |
| Auth Identity | Email / Google / Apple 等登入識別，對應至 User |
| Passkey Credential | 由 WebAuthn 建立，Backend 保存 Credential ID + Public Key |
| WebAuthn User Handle | 隨機、固定、不得含個資，對應 Backend User |
| Challenge | CSPRNG 生成、綁定 Ceremony、短效、一次性，防止重放 |
| Session | 登入後允許操作 API 的伺服器端授權狀態 |
| Pairing | 裝置 B 發起、由 A 批准的跨裝置 QR 授權請求 |
| Consumer Device | 請求登入／註冊新 Passkey 的裝置 B |
| Approver Device | 已持有可信 Session + Passkey 的裝置 A |
| Short-lived Grant | 只允許指定操作、一次性的授權票據；**不是**永久登入憑證 |
| One-time Login | 一次授權建立短效 Session，不新建 Passkey，也不發 Refresh Token |
| Step-up | 敏感操作前要求近期 WebAuthn 重新驗證 |
| Local Demo | 使用 Mailpit、Local PostgreSQL、兩個 Browser Profile 模擬兩台裝置 |

---

## 3. Product rules / Security invariants (MUST)

**AUTH-01** `users.id` 永遠是 Application Data 的 User Key。禁止以 Email 或 Google `sub` 當作業務主鍵。  
**AUTH-02** 登入成功必須查詢 Credential ID 對應的 Public Key，驗證 Challenge、Signature、Origin、RP ID Hash、User Verification、Credential 狀態。  
**AUTH-03** WebAuthn Challenge 使用強隨機數（建議至少 32 bytes），5 分鐘內有效，交易綁定與原子一次性消耗。  
**AUTH-04** Passkey 註冊必須持有有效 `registration` / `enroll` Grant 或已登入且通過 Step-up 的 Session；不能僅依靠猜測 User ID。  
**AUTH-05** Email OTP 建議 CSPRNG 產生 6–8 位數，5 分鐘有效；資料庫僅存具 server-side pepper/HMAC 的摘要（或其他抗離線猜測機制），限制嘗試次數和重寄速率；不得紀錄原始 OTP。  
**AUTH-06** 註冊未完成時 User 狀態為 `pending_passkey`，不能使用 Business API；首次 Passkey Verification 成功才轉 `active`。  
**AUTH-07** QR Payload 不得包含 Access Token、Refresh Token、Session Cookie、Passkey Private Key。  
**AUTH-08** QR 請求分為 `LOGIN_ONCE` 與 `ENROLL_PASSKEY`，Scope 不可中途變更。  
**AUTH-09** QR 預設 120 秒有效；只能由一個 Approver Claim，且只能批准和兌換一次；過期、拒絕、取消、失敗後不可重用。  
**AUTH-10** 掃碼時**僅開啟待確認畫面**；Approve 前必須顯示動作、裝置資訊、時間、兩端一致的比較碼，且由 A 使用 Passkey Step-up。比較碼是輔助防誤授權，不能單獨防止釣魚。  
**AUTH-11** B 必須持有其建立 Pairing 時產生的隨機瀏覽器綁定資料及臨時 Public Key 的 Private Key；兌換 Grant 時要求 Proof of Possession。**只拿 QR 截圖不得取得 Session**。  
**AUTH-12** `LOGIN_ONCE` 預設發 15 分鐘 Short Session、不可 Refresh、不可新增 Passkey、不可修改敏感帳號資訊；必要時可設更短時效。  
**AUTH-13** `ENROLL_PASSKEY` 只授權 B 執行一次 Passkey Registration，不能直接發普通 Session；註冊驗證成功後才能建立 B 的正常 Session。  
**AUTH-14** 必須有失敗重試、Rate Limit、Audit Log 與 Session/Passkey 撤銷功能。  
**AUTH-15** Account Linking 必須要求既有帳號完成可信登入 + Step-up，不得只因 Email 相同而自動合併。  
**AUTH-16** Local Fake OIDC/OTP Test Provider 只在測試環境開啟；Production 啟動時若 `AUTH_FAKE_PROVIDER=true` 應拒絕啟動。  
**AUTH-17** 所有修改 Pairing、Challenge、OTP 使用 PostgreSQL Transaction 和條件式更新，必須在多個 Lambda Concurrent Invocations 下正確。  
**AUTH-18** 登出只撤銷指定 Session；登出所有裝置要撤銷 User 的所有 Session／Refresh Token。  
**AUTH-19** 新增／移除 Passkey、改 Email、帳號復原均有獨立 Risk Policy；僅持有低保證 OTP Session 不得直接刪除全部舊 Passkeys。  
**AUTH-20** Production 使用 HTTPS、CSRF 防護（Cookie Session）、HttpOnly/Secure/SameSite Cookie、嚴格 CORS、避免 Token/PII 記錄。

> QR 安全限制：自訂 QR 無法自動證明掃碼的兩台裝置在物理上接近；即使有 TTL、比較碼、Proof of Possession，仍可能遭受跨裝置同意釣魚或即時中繼。對高風險功能應優先使用原生 FIDO CDA 或完整 Passkey 重新驗證，並作風險評估（RFC 10027）。

---

## 4. First registration decisions

### 4.1 Email OTP（MVP 預設）

```mermaid
sequenceDiagram
  participant U as User / Browser
  participant A as Auth API
  participant E as EmailSender (Mailpit / SES)
  participant D as PostgreSQL
  participant P as Passkey Provider
  U->>A: POST /auth/registrations/email/start
  A->>D: Create OTP challenge (HMAC, TTL, attempt limit)
  A->>E: Send OTP
  U->>A: POST /auth/registrations/email/verify
  A->>D: Consume OTP; create pending User & email Identity
  A-->>U: registration_grant (HttpOnly cookie)
  U->>A: POST /auth/passkeys/registration/options
  A->>D: Save WebAuthn challenge
  A-->>U: PublicKeyCredentialCreationOptions
  U->>P: navigator.credentials.create(options)
  P-->>U: attestation response
  U->>A: POST /auth/passkeys/registration/verify
  A->>D: Verify / Store credential / active User / Create Session
  A-->>U: Session Cookie
```

條件：Email 重寄需冷卻（範例 60s）、每個 Email / IP 每小時限流、通用回應避免枚舉帳號。首次 OTP 驗證通過後，僅給予 10 分鐘的 Registration Grant。若使用者關閉頁面或 WebAuthn 取消，保留有限時間的 `pending_passkey` 註冊紀錄以便安全續註冊，過期後清理。

### 4.2 Google OAuth / OpenID Connect（可選）

- 使用 Google Identity Services / OIDC；Backend 必須驗證 Token 簽章、`iss`、`aud`、`exp`，並處理相應 CSRF、State、Nonce 規則。
- Scope 至少 `openid email`，若需要名字才加 `profile`。
- 將 `provider=google`、`provider_subject=ID Token sub` 存至 `user_identities`。
- `email`、`email_verified` 是屬性，不是穩定 Identity Key；Google 對非 Gmail / 非 Workspace 自有郵箱可能不再具備當前控制權的權威性，適當時要求獨立 Email OTP。
- 若 Google `sub` 對應既有 Identity，登入該 User；若無，建立 `pending_passkey` User / 授予 Registration Grant。
- 若 Email 已被另一 User 使用，**不自動接管或合併**；要求以既有登入方式進入帳戶設定後進行明確連結。
- 登入方式是否保留 Google SSO 是獨立 Product 決策：可以僅作初次註冊，或當作備援登入；後者會持續形成 Google IdP 依賴。

### 4.3 為何不推薦初次只用 Google Authenticator

Google Authenticator 是 TOTP 驗證器。TOTP Seed 必須先安全綁定某個已確認的 User，不能憑 TOTP 證明某個 Email 歸屬；TOTP 可以作為既有帳戶的額外驗證或備援，而非首次註冊信箱驗證的替代。

### 4.4 Email Sender environment strategy

| 環境 | Provider | 成本 / 行為 |
|---|---|---|
| Local | Mailpit SMTP (`localhost:1025`) | 免費；不對外寄信 |
| CI | In-memory / Fake EmailSender | 免費；直接捕捉測試內容 |
| Staging | SES sandbox 或專用測試 Email | Sandbox 只可寄到驗證地址／網域 |
| Production | SES 或可靠 Transactional Email Provider | 按使用量付費；需 Domain 驗證、SPF/DKIM、送達監控 |

**AWS SES 定價（確認日：2026-11-14）**：2026-07-21 後新 SES 客戶不再有舊式 3,000 封/月專屬免費額度；已符合舊制資格的用戶可能保留到期限。當前 Essentials 公開費率約 US$0.16/1,000 emails，à-la-carte 基本 outbound 約 US$0.10/1,000（另有附加收費／稅費；以 AWS 帳戶及 Region 實際帳單為準）；新 SES 帳戶的 Sandbox 只能寄往已驗證的地址或網域，限制每 24 小時 200 封、每秒 1 封。

---

## 5. QR flow contracts

### 5.1 Native FIDO CDA — preferred where supported

```text
B calls navigator.credentials.get({ publicKey: ... })
  → browser / OS shows its own QR for "use a passkey from another device"
  → A scans, confirms using its existing passkey / device unlock
  → browser transports signed assertion through FIDO hybrid protocol
  → B posts assertion to our WebAuthn Login Verify endpoint
  → Backend verifies normally and creates B Session
```

- No custom `/pairings` API needed; no new credential registered.
- Native QR payload, BLE proximity, platform UX **are not our implementation**; browsers / OS own them.
- Exact availability depends on browser / platform / Bluetooth configuration.

### 5.2 Custom QR — state machine

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> CLAIMED: A scan + authenticated claim
  CLAIMED --> APPROVED: A step-up + explicit approve
  PENDING --> EXPIRED: ttl
  CLAIMED --> EXPIRED: ttl
  PENDING --> CANCELED: B cancels
  CLAIMED --> REJECTED: A rejects
  APPROVED --> REDEEMED: B bound proof + one-time exchange
  APPROVED --> EXPIRED: grant ttl
  REDEEMED --> [*]
  EXPIRED --> [*]
  REJECTED --> [*]
  CANCELED --> [*]
```

**Initiation on B**

1. B generates non-exportable ephemeral signing key pair using WebCrypto; B keeps Private Key in the original browser context. Store ephemeral public key with new Pairing. For browser reload handling, use secure same-origin Session Binding (and invalidate on loss), not a bearer QR token.
2. B calls `POST /auth/pairings` with `purpose=LOGIN_ONCE` or `ENROLL_PASSKEY` and ephemeral public key.
3. API returns `pairing_id`, `qr_url`, `comparison_code`, `expires_at`; B only displays these data. QR URL includes opaque claim nonce but no Session Token.
4. B polls `GET /auth/pairings/{id}/status` with its requester-bound authentication, or uses authenticated SSE / WebSocket. Anonymous unrelated clients cannot enumerate or see pairing states.

**Claim/Approval on A**

5. A scans QR and opens verified official domain. If A not logged in, it must authenticate normally using WebAuthn; scanning alone cannot authorize.
6. A calls `/claim` using claim nonce; server binds exactly one approver User. QR cannot claim a User identity by itself.
7. A sees `LOGIN_ONCE` vs `ENROLL_PASSKEY`, B platform/browser summary (untrusted hint), expiry, matching comparison code and warning. A confirms that the same code appears on B's screen. **Do not present comparison-code matching as protection against all live relay phishing.**
8. A performs fresh Passkey Step-up and explicitly Approves; write audit event and state transition atomically.

**Redemption on B**

9. B signs server-issued redemption Challenge using the ephemeral Private Key; backend verifies against stored ephemeral Public Key and against the original requester binding.
10. Backend consumes approved Grant atomically. If `LOGIN_ONCE`: create B short Session (15 min, no refresh, limited permissions). If `ENROLL_PASSKEY`: issue B scoped registration Grant (5 min, one ceremony); B must call WebAuthn registration APIs and pass validation to create new Credential + normal Session.
11. A's original Session remains active. B must be able to revoke its own Session; A must be able to revoke the new Session or Credential from account settings.

**Cross-device caution:** The above is a **bespoke cross-device consent flow**, distinct from WebAuthn's phishing-resistant login ceremony. For sensitive applications, require additional contextual assurance; policy should be reviewed against RFC 10027 and a threat model.

---

## 6. API contract (v1)

Base path: `/api/v1`. Content-Type JSON. For browser, use **same-origin** reverse proxy + HttpOnly Secure SameSite cookies (Secure enabled in HTTPS environments), CSRF mitigation for state-changing cookie-auth endpoints, and strict Origin validation. All API errors must use stable error codes; no sensitive values in logs.

| Method | Path | Auth | Intent |
|---|---|---|---|
| POST | `/auth/registrations/email/start` | public + rate limit | send OTP |
| POST | `/auth/registrations/email/verify` | OTP transaction | consume OTP, issue limited registration grant |
| POST | `/auth/oidc/google/start` | public | OIDC state/nonce init (optional) |
| GET | `/auth/oidc/google/callback` | OIDC callback | verify identity / register or login (optional) |
| POST | `/auth/passkeys/registration/options` | registration grant or verified step-up | WebAuthn create options |
| POST | `/auth/passkeys/registration/verify` | ceremony-bound | verify & store public credential |
| POST | `/auth/passkeys/authentication/options` | public, rate limit | WebAuthn get options (discoverable) |
| POST | `/auth/passkeys/authentication/verify` | ceremony-bound | verify and create session |
| POST | `/auth/pairings` | B requester-binding | generate QR session |
| GET | `/auth/pairings/{id}/status` | B requester-binding | status polling |
| POST | `/auth/pairings/{id}/claim` | A authenticated | claim QR as approver |
| POST | `/auth/pairings/{id}/approve` | A + recent Passkey step-up | approval |
| POST | `/auth/pairings/{id}/reject` | A authenticated | rejection |
| POST | `/auth/pairings/{id}/redeem/challenge` | B requester-binding | issue PoP challenge |
| POST | `/auth/pairings/{id}/redeem` | B + ephemeral signature | one-use redemption |
| POST | `/auth/sessions/refresh` | refresh cookie | rotate refresh token (not QR one-time session) |
| POST | `/auth/sessions/logout` | session | revoke current session |
| GET | `/me/passkeys` | session | list owned credentials |
| DELETE | `/me/passkeys/{credential_id}` | recent Passkey step-up | revoke credential |
| GET | `/me/sessions` | session | list sessions |
| DELETE | `/me/sessions/{id}` | session + reauth as policy | revoke session |
| POST | `/auth/recovery/start` | public + strong rate limit | begin managed recovery (future / restricted) |

### 6.1 Example: create Pairing

**Request**

```json
{
  "purpose": "ENROLL_PASSKEY",
  "ephemeral_public_key_jwk": {"kty":"EC", "crv":"P-256", "x":"<base64url>", "y":"<base64url>"}
}
```

**Response (201)**

```json
{
  "pairing_id": "<uuid>",
  "qr_url": "https://auth.example.com/connect?pairing=<uuid>&claim=<opaque_nonce>",
  "comparison_code": "482731",
  "expires_at": "2026-11-14T14:36:00Z",
  "status": "PENDING"
}
```

`claim` is **not** a Login Token. The comparison code is not a secret capable of authorizing login. The sample QR URL is illustrative; actual origin must be configured per environment, not trusted from user input.

### 6.2 Example: WebAuthn registration options

```json
{
  "challenge_id": "<uuid>",
  "publicKey": {
    "rp": {"name": "My Side Project", "id": "localhost"},
    "user": {
      "id": "<base64url-user-handle>",
      "name": "user@example.com",
      "displayName": "user@example.com"
    },
    "challenge": "<base64url-challenge>",
    "pubKeyCredParams": [{"type":"public-key", "alg":-7}, {"type":"public-key", "alg":-257}],
    "authenticatorSelection": {"residentKey":"required", "userVerification":"required"},
    "timeout": 300000,
    "attestation": "none"
  }
}
```

For production, use a vetted WebAuthn library to create and verify options/response. `challenge_id` is a backend correlation ID, not a replacement for checking browser-reported challenge. Base64url conversion between JSON and ArrayBuffer must be implemented correctly. RP ID and Origin expected values are server-configured.

### 6.3 Standard error codes

`OTP_INVALID`, `OTP_EXPIRED`, `OTP_ATTEMPTS_EXCEEDED`, `REGISTRATION_GRANT_REQUIRED`, `WEBAUTHN_CHALLENGE_INVALID`, `WEBAUTHN_ORIGIN_MISMATCH`, `WEBAUTHN_SIGNATURE_INVALID`, `PASSKEY_REVOKED`, `PAIRING_EXPIRED`, `PAIRING_ALREADY_CLAIMED`, `PAIRING_NOT_APPROVED`, `PAIRING_PROOF_INVALID`, `PAIRING_ALREADY_REDEEMED`, `STEP_UP_REQUIRED`, `AUTH_REQUIRED`, `RATE_LIMITED`.

Do not return granular user-existence information from public endpoints; some internal codes should be replaced by a generic client-facing message if they reveal account state.

---

## 7. PostgreSQL model / DDL (foundation)

> Use PostgreSQL 15+ locally; Supabase PostgreSQL in production. UUID values generated with `gen_random_uuid()`. `users` is an Application table, **not** Supabase `auth.users`.

```mermaid
erDiagram
  users ||--o{ user_identities : has
  users ||--o{ passkey_credentials : owns
  users ||--o{ auth_sessions : opens
  users |o--o{ auth_challenges : initiates
  users |o--o{ device_pairings : approves
  users ||--o{ auth_audit_events : relates
  users { uuid id PK bytea webauthn_user_handle UK text status }
  user_identities { uuid id PK uuid user_id FK text provider text provider_subject }
  passkey_credentials { bytea credential_id PK uuid user_id FK bytea public_key bigint sign_count }
  auth_sessions { uuid id PK uuid user_id FK timestamptz expires_at timestamptz revoked_at }
  auth_challenges { uuid id PK uuid user_id FK text purpose timestamptz expires_at }
  device_pairings { uuid id PK text purpose text status uuid approved_by_user_id FK }
  auth_audit_events { bigint id PK uuid user_id FK text action }
```

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  webauthn_user_handle BYTEA NOT NULL UNIQUE,
  primary_email TEXT,
  email_verified_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'pending_passkey'
    CHECK (status IN ('pending_passkey','active','suspended','deleted')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Application layer owns email canonicalization/uniqueness policy;
-- do NOT assume raw TEXT equality normalizes all email rules.
CREATE UNIQUE INDEX users_primary_email_lower_uniq
  ON users (lower(primary_email)) WHERE primary_email IS NOT NULL;

CREATE TABLE user_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL CHECK (provider IN ('email','google','apple')),
  provider_subject TEXT NOT NULL,
  verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (provider, provider_subject)
);

CREATE TABLE passkey_credentials (
  credential_id BYTEA PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  public_key BYTEA NOT NULL,
  sign_count BIGINT NOT NULL DEFAULT 0,
  transports TEXT[] NOT NULL DEFAULT '{}',
  backup_eligible BOOLEAN,
  backed_up BOOLEAN,
  label TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);
CREATE INDEX passkey_credentials_user_idx ON passkey_credentials(user_id);

CREATE TABLE auth_challenges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  purpose TEXT NOT NULL,                 -- registration / authentication / step_up / redeem
  challenge_hash BYTEA NOT NULL,
  context_hash BYTEA NOT NULL,           -- bound ceremony/requester context
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE email_otp_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email_normalized TEXT NOT NULL,
  otp_digest BYTEA NOT NULL,
  attempts SMALLINT NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE auth_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  session_kind TEXT NOT NULL CHECK (session_kind IN ('normal','qr_once')),
  auth_method TEXT NOT NULL,
  access_expires_at TIMESTAMPTZ NOT NULL,
  refresh_token_hash BYTEA,              -- NULL for qr_once
  refresh_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at TIMESTAMPTZ
);

CREATE TABLE device_pairings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  purpose TEXT NOT NULL CHECK (purpose IN ('LOGIN_ONCE','ENROLL_PASSKEY')),
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','CLAIMED','APPROVED','REDEEMED','REJECTED','CANCELED','EXPIRED')),
  claim_nonce_hash BYTEA NOT NULL,
  requester_binding_hash BYTEA NOT NULL,
  ephemeral_public_key_jwk JSONB NOT NULL,
  comparison_code_hash BYTEA NOT NULL,
  approved_by_user_id UUID REFERENCES users(id),
  approver_session_id UUID REFERENCES auth_sessions(id),
  expires_at TIMESTAMPTZ NOT NULL,
  approved_at TIMESTAMPTZ,
  redeemed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX device_pairings_expires_idx ON device_pairings(expires_at);

CREATE TABLE auth_audit_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  action TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**DDL TODO（Production before release）：**

- `email_otp_attempts` 再加入 resend / IP / device-keyed rate-limit records，避免依賴 `attempts` 單一維度。
- `auth_challenges` 的 `challenge_hash` 與 `context_hash` 設計需配合驗證函式；驗證時要能安全還原／比對原 Challenge。標準做法可保存加密後原值或原 Challenge 的 hash 並在回傳時以相同 bytes 驗證，不能只拿 hash 直接餵給期待原始 bytes 的 WebAuthn Library。
- `device_pairings` 的 claim、approval、redeem 實務上建議補獨立 grant table 或 grant hash / TTL / redeemed flag、step-up Auth Evidence、衝突更新版本。
- `auth_sessions` 可採 opaque session token 的 hash lookup（較易撤銷），或 JWT + refresh rotation + deny/revocation model。正式 Production 必須明確定案其儲存、輪替及 Cookie 防護。
- `user_identities` 的 Google `provider_subject=sub`，不能使用 Email 代替；Email OTP 使用 normalized email subject 是產品政策而非 Google OIDC subject。
- 不應給 Client Supabase `anon` / publishable API 存取這些 Auth Tables 的權限；由受信任 Backend 以最小權限 DB Credential 操作。若使用 Supabase Data API，務必明確設計 RLS / Schema exposure。

---

## 8. BDD: features and scenarios

> Gherkin 以下可複製為 `.feature` 檔；MVP 可用 pytest-bdd / Behave 搭配 Playwright，Infrastructure 部分用 Test Double。標籤：`@unit`, `@integration`, `@browser`, `@manual`, `@security`, `@phase2`。BDD Scenario 用語即為驗收語言。

### Feature 01 — Email bootstrap registration

```gherkin
@registration @integration
Feature: Register an account using Email OTP and bootstrap a passkey
  Background:
    Given the local EmailSender uses Mailpit
    And the user email "new@example.test" is not registered

  Scenario: Verified email can register the first passkey
    When I request a registration OTP for "new@example.test"
    Then a registration OTP email is delivered to Mailpit
    And the API response does not disclose whether an account exists
    When I submit the correct unexpired OTP
    Then a user with status "pending_passkey" is created
    And a scoped registration grant is issued
    And the grant cannot call business APIs
    When I start a WebAuthn registration ceremony
    And I create a discoverable credential with user verification
    And I submit the valid attestation
    Then exactly one passkey credential is stored for the user
    And the user status becomes "active"
    And an authenticated session is issued

  Scenario: Invalid OTP is rejected
    When I request a registration OTP for "new@example.test"
    And I submit an invalid OTP
    Then the API rejects the OTP
    And no business session is created

  Scenario: Expired OTP is rejected
    Given a registration OTP has expired
    When I submit that OTP
    Then the API returns "OTP_EXPIRED"
    And no registration grant is issued

  Scenario: Reusing a consumed OTP is rejected
    Given an OTP has been successfully consumed
    When I submit the same OTP again
    Then the API rejects the replay

  Scenario: Repeated invalid OTP attempts are limited
    When I fail OTP verification beyond the configured threshold
    Then the OTP transaction is locked or invalidated
    And the next attempt is rate limited

  Scenario: User cancels passkey enrollment
    Given the user has a scoped registration grant
    When the WebAuthn UI is canceled
    Then no credential is stored
    And the user cannot access business APIs
```

### Feature 02 — Google OIDC as optional first registration

```gherkin
@registration @oidc @integration
Feature: Bootstrap a passkey with verified Google OIDC identity
  Scenario: Google account is validated and linked to a new user
    Given Google OIDC returns a correctly signed ID token with valid issuer audience expiry and nonce
    And the stable subject is "google-sub-A"
    And the email policy considers the identity sufficiently verified
    When the user signs up through Google OIDC
    Then an internal user is created or safely recovered
    And a user identity with provider "google" and subject "google-sub-A" is stored
    And a scoped registration grant allows first passkey enrollment

  Scenario: Google token has wrong audience
    Given a Google ID token has an invalid audience
    When I submit it to the callback
    Then login is rejected without creating a session

  Scenario: Existing email is not sufficient to merge identities
    Given a local user already owns "member@example.test"
    And an unlinked Google identity reports "member@example.test"
    When the Google identity signs in
    Then the system does not silently merge or take over the existing user
    And explicit account linking is required

  Scenario: Third-party email ownership may need independent confirmation
    Given Google provides an external non-Gmail non-Workspace email
    When the account-email trust policy requires current email control
    Then additional Email OTP verification is required before activation
```

### Feature 03 — Passkey registration & login

```gherkin
@webauthn @browser
Feature: Register and authenticate using WebAuthn passkeys
  Scenario: Discoverable passkey login without typing email
    Given an active user owns a valid discoverable credential
    When I start passkey authentication without username
    And I approve the browser's passkey challenge
    Then the server identifies the user by credential ID and validated signature
    And a normal session is issued for that user

  Scenario: Multiple credentials can belong to the same user
    Given an active user has credential A
    And the same user securely registers credential B
    When the user logs in with credential B
    Then the resolved internal user ID equals the user ID for credential A

  Scenario: Forged WebAuthn signature is rejected
    Given an issued authentication challenge
    When an attacker submits an assertion with an invalid signature
    Then login is rejected
    And no session is issued

  Scenario: Wrong origin or RP ID is rejected
    Given an issued authentication challenge
    When a response comes from an untrusted origin or RP ID
    Then verification is rejected

  Scenario: Used challenge cannot be replayed
    Given a WebAuthn challenge has already been consumed
    When the same response is submitted again
    Then the API rejects the replay

  Scenario: Revoked credential cannot log in
    Given a passkey credential is revoked
    When the credential attempts to authenticate
    Then login is rejected
```

### Feature 04 — Native FIDO cross-device authentication

```gherkin
@webauthn @cross_device @manual
Feature: Native FIDO hybrid authentication using another device's passkey
  Scenario: Use phone passkey to log into a desktop browser
    Given a compatible desktop browser supports cross-device passkeys
    And the user owns a valid passkey on a nearby phone
    When the user chooses to authenticate using another device
    And the browser displays its native FIDO QR code
    And the user scans it on the phone and approves the passkey request
    Then the desktop browser receives a valid WebAuthn assertion
    And our backend validates it and opens a desktop session
    And our backend does not create a new passkey credential
```

### Feature 05 — QR one-time login (custom)

```gherkin
@pairing @integration
Feature: Approve a short one-time login using QR code
  Background:
    Given device A is logged in as user A and can perform WebAuthn step-up
    And device B has no authenticated session

  Scenario: Approved QR creates exactly one short session
    When B initiates a "LOGIN_ONCE" pairing with an ephemeral public key
    Then B displays a QR and comparison code
    When A scans the QR and claims the pairing
    And A confirms the same comparison code is shown on B
    And A completes fresh passkey verification
    And A explicitly approves "LOGIN_ONCE"
    And B proves possession of its ephemeral private key
    Then B can redeem the pairing exactly once
    And B receives a session with maximum lifetime of 15 minutes
    And B receives no refresh token
    And no passkey is added to user A

  Scenario: Scanning QR without approval cannot log in
    When B starts a "LOGIN_ONCE" pairing
    And A scans the QR but does not approve
    Then B cannot redeem a session

  Scenario: QR expiration blocks approval and redemption
    Given a pairing is older than 120 seconds
    When A tries to approve it
    Then the API returns "PAIRING_EXPIRED"
    And B cannot redeem it

  Scenario: Replayed QR pairing is rejected
    Given B has already redeemed an approved pairing
    When a second client attempts to redeem the same pairing
    Then the API returns "PAIRING_ALREADY_REDEEMED"

  Scenario: Screenshot of QR does not transfer B session
    Given an attacker has a screenshot of B's QR code
    And the attacker does not possess B's ephemeral private key and requester binding
    When the attacker tries to redeem the pairing
    Then the API returns "PAIRING_PROOF_INVALID"

  Scenario: Pairing is rejected by approver
    Given A has claimed a pending pairing
    When A rejects the request
    Then B sees a rejected state
    And no session is issued

  Scenario: One-time login cannot manage credentials
    Given B has a valid "qr_once" session
    When B calls the register or delete passkey API
    Then the API returns "STEP_UP_REQUIRED" or "AUTH_REQUIRED"

  Scenario: Only one redemption succeeds under concurrent requests
    Given A approved a pairing
    When two clients try to redeem it concurrently
    Then exactly one redemption succeeds
    And exactly one B session is issued
```

### Feature 06 — QR passkey enrollment (custom)

```gherkin
@pairing @registration @integration
Feature: Approve registering a new passkey on another platform
  Background:
    Given device A is an authenticated user A with an existing passkey
    And device B has no credential for user A

  Scenario: New device receives only registration rights
    When B starts an "ENROLL_PASSKEY" pairing
    And A scans the pairing QR
    And A verifies the comparison code and completes passkey step-up
    And A explicitly approves "ENROLL_PASSKEY"
    And B proves possession of its ephemeral private key
    Then B receives a single-use scoped enrollment grant
    And B cannot use the grant to call business APIs
    When B generates a new local passkey and submits a valid WebAuthn registration
    Then credential B is saved for the same internal user A
    And a normal session can be created for B

  Scenario: Enrollment approval does not automatically log in B
    Given A approved "ENROLL_PASSKEY" for B
    When B has not completed WebAuthn registration verification
    Then B does not have a normal business session

  Scenario: Enrollment grant cannot be used twice
    Given B has consumed an enrollment grant
    When B attempts to register another credential with the same grant
    Then registration is denied

  Scenario: QR login grant cannot become enrollment grant
    Given a pairing purpose is "LOGIN_ONCE"
    When B attempts to register a passkey using that pairing
    Then the action is rejected

  Scenario: Revoking one of multiple credentials keeps user identity
    Given user A owns credentials A and B
    When user A step-up verifies and revokes credential B
    Then credential A remains valid
    And the internal user ID is unchanged
```

### Feature 07 — Recovery, sessions and negative security tests

```gherkin
@security @integration
Feature: Preserve account security during device changes and recovery
  Scenario: Account remains the same across platforms
    Given user A owns an iOS passkey and an Android passkey
    When the user authenticates from either platform
    Then both sessions resolve to the same internal user ID

  Scenario: Existing email alone cannot authorize a new credential
    Given user A exists with a verified email
    When a different device submits only that email to enroll a passkey
    Then no enrollment grant is issued

  Scenario: High-risk operation needs recent strong authentication
    Given the current session was established using Email OTP only
    When the user tries to revoke all passkeys
    Then a strong recovery or step-up process is required

  Scenario: Revoking a session does not revoke all passkeys
    Given user A has two sessions and two passkeys
    When session B is revoked
    Then only session B is invalid
    And passkey credentials remain intact

  Scenario: All passkeys lost is not recoverable by a single unchecked email alone
    Given user A lost all passkeys
    When an actor requests recovery using only an email address
    Then the system does not immediately remove existing passkeys
    And a separately governed recovery workflow is required

  Scenario: Production cannot enable test identity provider
    Given the environment is production
    When AUTH_FAKE_PROVIDER is set to true
    Then the service refuses startup
```

---

## 9. Local demo / how to validate

### 9.1 Required tools

- Docker / Docker Compose, Python 3.11+, Node.js LTS / npm, latest Chrome/Safari/Edge, real Passkey-capable device if testing native cross-device QR.
- Suggested Python libraries: `fastapi`, `uvicorn`, `webauthn` (PyPI package for `py_webauthn`), `psycopg` or `asyncpg`, `pytest`, `pytest-bdd` or `behave`.
- Frontend: `vite` / React (or existing project), WebAuthn browser API and a base64url JSON conversion helper.
- E2E: Playwright; **real OS Passkey dialogs may require manual tests or browser virtual authenticator support**.

### 9.2 Suggested repository layout

```text
side-project/
├─ docs/
│  └─ webauthn_cross_device_bdd_spec.md
├─ backend/
│  ├─ app/
│  │  ├─ domain/users/
│  │  ├─ application/auth/
│  │  ├─ infrastructure/postgres/
│  │  ├─ infrastructure/webauthn/
│  │  ├─ infrastructure/oidc/
│  │  ├─ infrastructure/email/      # Mailpit / SES adapters
│  │  ├─ infrastructure/sessions/
│  │  └─ api/v1/
│  ├─ migrations/
│  ├─ tests/features/              # Gherkin .feature files
│  ├─ tests/steps/                 # pytest-bdd step definitions
│  └─ pyproject.toml
├─ frontend/
│  ├─ src/pages/register/
│  ├─ src/pages/login/
│  ├─ src/pages/device-connect/
│  └─ src/lib/webauthn/
├─ e2e/
│  └─ playwright/
├─ compose.yaml
└─ .env.example
```

### 9.3 Start local PostgreSQL + Mailpit

Minimal `compose.yaml` (run only against a local machine; use strong non-demo credentials in shared environments):

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: authdemo
      POSTGRES_PASSWORD: authdemo_local_only
      POSTGRES_DB: authdemo
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U authdemo -d authdemo"]
      interval: 3s
      retries: 10
  mailpit:
    image: axllent/mailpit:latest
    ports:
      - "127.0.0.1:8025:8025" # mailbox UI
      - "127.0.0.1:1025:1025" # SMTP (host backend)
```

```bash
# from repository root after creating compose.yaml
docker compose up -d
# PostgreSQL: localhost:5432
# Mailpit UI: http://localhost:8025
# Mailpit SMTP: localhost:1025
```

### 9.4 Environment variables

```dotenv
APP_ENV=local
DATABASE_URL=postgresql://authdemo:authdemo_local_only@localhost:5432/authdemo
WEB_ORIGIN=http://localhost:3000
WEBAUTHN_RP_ID=localhost
WEBAUTHN_EXPECTED_ORIGIN=http://localhost:3000
EMAIL_PROVIDER=smtp
SMTP_HOST=localhost
SMTP_PORT=1025
EMAIL_FROM=auth@demo.local
OTP_TTL_SECONDS=300
REGISTRATION_GRANT_TTL_SECONDS=600
WEBAUTHN_CHALLENGE_TTL_SECONDS=300
PAIRING_TTL_SECONDS=120
QR_ONCE_SESSION_TTL_SECONDS=900
AUTH_FAKE_PROVIDER=false
# Generate strong random pepper/secret only for local, never commit to Git:
OTP_HMAC_PEPPER=<replace-with-random-secret>
SESSION_SECRET=<replace-with-random-secret-if-used>
```

**Local URL constraint:** `http://localhost:3000` is a secure context for WebAuthn. Browser and Backend should be served through the same hostname/scheme in the browser (`/api` reverse-proxied to `localhost:8000`) to avoid origin/Cookie problems. On a real mobile device, `localhost` refers to the phone, not your laptop.

### 9.5 Required developer entry points (implementation acceptance contract)

These commands are the **target scripts the project implementation MUST provide**; this specification alone does **not** create runnable backend/frontend code:

```bash
# after implementing API + UI + migrations
make dev-up                  # compose + migrations + backend + frontend
make test-unit               # tests of Challenge / signature verification wrapper / state machines
make test-bdd                # pytest-bdd / Behave, uses local PostgreSQL + fake authenticator
make test-e2e                # Playwright browser scenarios
make demo-seed               # demo accounts without production credentials
make dev-down                # stop local containers
```

The implementation must expose `/health` and a DEV-ONLY reset endpoint or command to clear seed data; production must not expose reset/fake authentication controls.

### 9.6 Manual local acceptance walkthrough

**Demo A — Email OTP + first Passkey:**

1. Open `http://localhost:3000/register` in Browser Profile A.
2. Enter `demo@example.test`; submit.
3. Open `http://localhost:8025`, read latest OTP.
4. Enter OTP; expected: pending registration, business API denies access.
5. Create Passkey via browser prompt (platform Authenticator or supported virtual authenticator).
6. Confirm `users.status=active`, a new `passkey_credentials` row, and a Session.
7. Log out; log back in using Passkey without typing Email (discoverable credential where supported).

**Demo B — Local QR One-time Login (two Browser Profiles on same machine):**

1. A is logged in with Passkey in Chrome Profile A.
2. B uses Chrome Incognito or Profile B and clicks **QR One-time Login**.
3. B shows QR and 6-digit comparison code. For localhost demo, display a clickable `qr_url` beside QR and paste it into Profile A to simulate scanning.
4. A claims, compares code, completes a fresh Passkey step-up and approves.
5. B polls until approved, proves ephemeral-key possession, redeems and obtains 15-minute restricted Session.
6. Verify B cannot register Passkey or refresh its Session; repeat redeem fails.

**Demo C — Local QR Add Passkey:**

1. B is unauthenticated and starts **Add Passkey via QR**.
2. A opens scanned link, matches comparison code, performs Passkey step-up, approves.
3. B redeems **enrollment-only grant**, performs its own `navigator.credentials.create()`.
4. DB has two distinct credential IDs referencing the same `users.id`.
5. B logs out and logs back in using its own Passkey, without A.

**Demo D — Native cross-platform CDA with real phone (manual integration gate):**

1. Use an HTTPS **stable custom test domain** with a valid certificate, fixed RP ID and reachable Backend; do **not** use raw LAN HTTP IP. Test can use a trusted HTTPS tunnel + your own fixed subdomain.
2. Register a Passkey on iPhone or Android.
3. On a compatible desktop browser with Bluetooth enabled, open the same website and choose **Use passkey from another device**.
4. Scan the browser's native FIDO QR on the phone; finish Face ID / fingerprint / PIN.
5. Verify desktop receives a Session and DB credential count **does not increase**.
6. Repeat using a different OS/browser combination where supported. Native CDA may not be automatable in CI.

### 9.7 Demo minimum acceptance checklist

- [ ] `new@example.test` can register using OTP seen in Mailpit.
- [ ] Business APIs reject `pending_passkey` user.
- [ ] Browser Passkey Registration and Authentication succeed.
- [ ] Same User has >1 Credential, and both map to one Internal User UUID.
- [ ] QR login / QR enrollment have separate scopes and UI labels.
- [ ] QR login issues no Refresh Token; enrollment grant cannot call business APIs.
- [ ] Challenge replay / invalid signature / wrong Origin / expired QR / double redemption are rejected.
- [ ] QR screenshot cannot redeem without requester key+binding.
- [ ] Google OIDC optional tests reject invalid token and prevent unsafe Email merge.
- [ ] Pairing transitions remain race-safe under concurrent redemption.
- [ ] At least one native CDA manual test on HTTPS + real devices.

---

## 10. Threat model / prioritized risks

| Risk | Severity | MVP mitigation | Test |
|---|---|---|---|
| QR screenshot shared to attacker | High | Pairing bound to B ephemeral key + requester context; not a bearer token | Feature 05 screenshot |
| QR approval phishing / relay | High | Clear transaction intent, comparison code, Step-up, short TTL, audit; recognize residual social-engineering risk | Feature 05 + manual abuse review |
| OTP brute-force / spam | High | TTL, per transaction/IP/email rate limit, retry cap, hashed/HMAC OTP | Feature 01 invalid/retry |
| A victim Email compromised | High | OTP can bootstrap new User, but does not automatically replace existing Passkeys; strict recovery | Feature 07 |
| WebAuthn replay / fake credential | Critical | Vetted WebAuthn verifier, strict Challenge + Origin + RP ID + UV | Feature 03 |
| Unsafe social identity linking | Critical | `provider,sub` unique, no email-only merge | Feature 02 |
| Pairing redeemed simultaneously | High | Row lock / atomic compare-and-swap + uniqueness for one grant/session | Feature 05 concurrency |
| Supabase database credentials leaked to Frontend | Critical | Backend-only DB access, secrets management, schema privileges | Deployment tests |
| Browser session stolen | High | HttpOnly Secure Cookie, CSRF, short session, revocation, refresh rotation if enabled | Feature 07 |
| All passkeys lost | High | Backup credential, verified recovery with policy and delays, alerting | Feature 07 |

**Design gate:** For financial, payment or other high-impact operations, do not enable custom QR session transfer without a security review of real-time phishing, device trust and transaction binding. Native CDA offers stronger phishing resistance properties and should be the default when feasible.

---

## 11. Development backlog / Definition of Done

| Priority | Story | Done when |
|---|---|---|
| P0 | Schema + migrations + domain entities | PostgreSQL test migration succeeds; constraints tested |
| P0 | EmailSender + Mailpit | OTP email appears in local UI |
| P0 | Email OTP Bootstrap | Feature 01 passes |
| P0 | WebAuthn Ceremony | Feature 03 positive + replay tests pass |
| P0 | SessionService + Cookie/Auth Guard | unauthorized Business APIs rejected |
| P1 | Credential Management + Step-up | registered credentials can be listed/revoked securely |
| P1 | QR One-time Login | Feature 05 incl. concurrency passes |
| P1 | QR Enroll Passkey | Feature 06 incl. scope isolation passes |
| P1 | Multi-profile Local E2E | Demo A/B/C documented + reproducible |
| P1 | Native CDA validation | Demo D verified manually on real devices |
| P2 | Google OIDC bootstrap | Feature 02 passes, safe linking enforced |
| P2 | Apple OIDC / Account Linking | provider sub mapping + explicit linking tested |
| P2 | Recovery / abuse controls | Threat model / security review completed |
| P2 | Production Lambda + Supabase rollout | IAM, secrets, pooling, rate limits, observability deployed |

**DoD:** All required `@unit` and `@integration` tests green; security-negative scenarios pass; no sensitive tokens/OTP logged; reviewer validates QR state transitions and database concurrency constraints; documentation updated with actual commands and real-mobile HTTPS test observations.

---

## 12. Configuration / implementation notes

1. **WebAuthn implementation:** Python `py_webauthn` for `generate_registration_options`, `verify_registration_response`, `generate_authentication_options`, `verify_authentication_response`. Verify expected Challenge, Origin, RP ID, and require User Verification. Keep verification rules server-side, not trusting client claims.
2. **Challenge verification:** Each ceremony is bound to a separate session/transaction; challenge must be consumed once with DB atomic operation. Do not allow challenge from one User or ceremony to be reused by another.
3. **Sessions:** For small side project, opaque server-side session IDs stored as hashes in PostgreSQL can simplify revocation. Use Cookie `HttpOnly`, `Secure` (HTTPS), `SameSite=Lax` or stricter according to UX, CSRF tokens and request-origin checks. Never include session secrets inside QR.
4. **Google OIDC:** Validate public signing keys and complete OIDC requirements, apply issuer/audience/nonce/state protection as appropriate. Store `sub` as Provider Subject; treat Email as attribute; never trust `email_verified` without verifying the token.
5. **PostgreSQL in Lambda:** Use Supabase transaction pooler where appropriate; account for serverless connection churn and transaction pooling constraints. Do not send credentials or privileged DB keys to Frontend.
6. **Local test:** Browser virtual authenticator can test assertion verification; real iOS/Android/CDA UX requires manual testing on actual hardware and trusted HTTPS.
7. **Multi-device:** Do not conflate synced Passkey with one-device-one-Credential, or Credential with Session; implement distinct Session records for revocation.
8. **Account recovery:** Remains a separately reviewed feature, not a side effect of the OTP or QR registration API.

### 12.1 Open decisions (explicit TODOs)

- [ ] Product Domain & final RP ID (`example.com` vs `auth.example.com`); ensure stable before real registrations.
- [ ] Session model: opaque DB Session vs short JWT + refresh rotation; define CSRF and device-session policy.
- [ ] Whether Email OTP alone is permitted for existing-user recovery (default **NO** for sensitive changes).
- [ ] QR short session allowed scopes / TTL for the actual Side Project domain.
- [ ] UI QR polling vs SSE and how to handle browser reload while preserving ephemeral requester key.
- [ ] Production SES vs other provider; domain verification, delivery rate, SPF/DKIM.
- [ ] Google as bootstrap-only vs persistent alternative sign-in provider.
- [ ] Whether native iOS/Android Apps are in scope, and their Associated Domains / Digital Asset Links.
- [ ] Manual security review for phishing, session binding and recovery before production.

---

## 13. References (official / primary resources)

- W3C Web Authentication: https://www.w3.org/TR/webauthn-3/
- MDN Web Authentication API: https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API
- MDN Secure Contexts (`localhost` exception): https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts
- FIDO Alliance Passkeys / Cross-device Authentication: https://fidoalliance.org/passkeys-2/
- Google Passkeys use cases (cross-device): https://developers.google.com/identity/passkeys/use-cases
- IETF RFC 10027 (Cross-device security, August 2026): https://www.rfc-editor.org/rfc/rfc10027.html
- py_webauthn Python library: https://github.com/duo-labs/py_webauthn
- Google Verify ID Token: https://developers.google.com/identity/gsi/web/guides/verify-google-id-token
- Mailpit Docker: https://mailpit.axllent.org/docs/install/docker/
- AWS SES Pricing: https://aws.amazon.com/ses/pricing/
- AWS SES Sandbox: https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html
- SES July 2026 change: https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-ses-pricing-plans/
- Apple Associated Domains: https://developer.apple.com/documentation/xcode/supporting-associated-domains
- Android Digital Asset Links: https://developer.android.com/identity/credential-manager/prerequisites

---

## 14. Prompt for Coding Agent / implementation handoff

Copy into your Coding Agent along with this file:

> Read `docs/webauthn_cross_device_bdd_spec.md` as the source of truth. Implement Phase P0 first using Python FastAPI for local and an ASGI-to-Lambda adapter or Lambda-compatible handlers for deployment, PostgreSQL, `py_webauthn`, and Mailpit. Preserve all AUTH-01..AUTH-20 invariants. Create working `compose.yaml`, migrations, `.env.example`, UI, pytest-bdd feature files/steps, and Playwright E2E. Implement `make dev-up`, `make test-bdd`, and `make demo-seed`. Provide automated negative tests for Challenge replay and OTP abuse. Do not implement custom cryptography to replace WebAuthn verification, and do not consider QR scan alone as authorization. After P0 tests pass, implement custom QR LOGIN_ONCE and ENROLL_PASSKEY as separate scoped flows with atomic one-time redemption and proof-of-possession; separately verify native FIDO CDA on real hardware. Mark missing security details as TODO and do not silently weaken the acceptance criteria.

