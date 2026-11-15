"""Environment-only configuration. Never log settings or connection strings."""

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

APP_ENVS = ("local", "production")


def _flag(name, default=False):
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


@dataclass(frozen=True, repr=False)
class Settings:
    # APP_ENV has no default (docs/auth-passkey-design.md section 1): local | production.
    app_env: str
    database_url: str = ""
    admin_database_url: str = ""
    internal_service_token: str = ""
    owner_user_id: UUID | None = None
    worker_dispatcher: str = "local"
    github_repository: str = ""
    github_dispatch_token: str = ""
    local_worker_command: str = ""
    agent_runner: str = "stub"
    # Passkeys. The RP ID and origins are never derived from request headers.
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "Daily Brief"
    webauthn_origins: tuple[str, ...] = ("http://localhost:5173",)
    non_owner_admission: bool = False
    rate_limit_key: str = ""
    compose_project_name: str = "local"
    auth_global_circuit_breaker: bool = field(default=False)
    # The admin CLI runs on Kai's machine and needs no runtime secrets in production.
    admin_cli: bool = False
    # Production connections require TLS; only tests against a throwaway container relax it.
    database_sslmode: str = ""

    def __post_init__(self):
        if self.app_env not in APP_ENVS:
            raise ValueError("APP_ENV must be local or production")
        if self.worker_dispatcher not in {"local", "github"}:
            raise ValueError("Unsupported WORKER_DISPATCHER")
        if not self.webauthn_origins or not self.webauthn_rp_id:
            raise ValueError("WEBAUTHN_RP_ID and WEBAUTHN_ORIGINS are required")
        for origin in self.webauthn_origins:
            parts = urlsplit(origin)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.path
                or parts.query
                or parts.fragment
                or parts.username
                or origin.endswith("/")
            ):
                raise ValueError("WEBAUTHN_ORIGINS must be exact origins")
            host = parts.hostname
            if host != self.webauthn_rp_id and not host.endswith("." + self.webauthn_rp_id):
                raise ValueError("WEBAUTHN_ORIGINS must belong to WEBAUTHN_RP_ID")
        if self.app_env != "local" and not self.admin_cli:
            if self.worker_dispatcher == "local":
                raise ValueError("Local dispatch requires APP_ENV=local")
            if not self.database_url or not self.internal_service_token:
                raise ValueError("Production database and internal token are required")
            if len(self.rate_limit_key) < 32:
                raise ValueError("RATE_LIMIT_KEY must be set (at least 32 characters)")
            if self.agent_runner == "stub":
                raise ValueError("Stub results require APP_ENV=local")
        if self.app_env != "local":
            if any(not o.startswith("https://") for o in self.webauthn_origins):
                raise ValueError("Production WEBAUTHN_ORIGINS must use https")
            if self.webauthn_rp_id in {"localhost", ""}:
                raise ValueError("Production WEBAUTHN_RP_ID must be the app host")

    @property
    def local(self) -> bool:
        return self.app_env == "local"

    @property
    def session_cookie(self) -> str:
        return (
            "brief_session_" + self.compose_project_name if self.local else "__Host-brief_session"
        )

    @property
    def binding_cookie(self) -> str:
        return (
            "brief_ceremony_" + self.compose_project_name if self.local else "__Host-brief_ceremony"
        )

    @property
    def app_origin(self) -> str:
        return self.webauthn_origins[0]

    @classmethod
    def from_env(cls, admin_cli=False):
        app_env = os.environ.get("APP_ENV", "")
        if app_env not in APP_ENVS:
            raise ValueError("APP_ENV must be local or production")
        owner = os.environ.get("OWNER_USER_ID", "")
        project = os.environ.get("COMPOSE_PROJECT_NAME", "local")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", project):
            raise ValueError("COMPOSE_PROJECT_NAME has unsupported characters")
        origins = tuple(
            o.strip() for o in os.environ.get("WEBAUTHN_ORIGINS", "").split(",") if o.strip()
        )
        return cls(
            app_env=app_env,
            database_url=os.environ.get("DATABASE_URL", ""),
            admin_database_url=os.environ.get("ADMIN_DATABASE_URL", ""),
            internal_service_token=os.environ.get("INTERNAL_SERVICE_TOKEN", ""),
            owner_user_id=UUID(owner) if owner else None,
            worker_dispatcher=os.environ.get("WORKER_DISPATCHER", "local"),
            github_repository=os.environ.get("GITHUB_REPOSITORY", ""),
            github_dispatch_token=os.environ.get("GITHUB_DISPATCH_TOKEN", ""),
            local_worker_command=os.environ.get("LOCAL_WORKER_COMMAND", ""),
            agent_runner=os.environ.get("AGENT_RUNNER", "stub"),
            webauthn_rp_id=os.environ.get("WEBAUTHN_RP_ID", ""),
            webauthn_rp_name=os.environ.get("WEBAUTHN_RP_NAME", "Daily Brief"),
            webauthn_origins=origins,
            non_owner_admission=_flag("NON_OWNER_ADMISSION"),
            rate_limit_key=os.environ.get("RATE_LIMIT_KEY", ""),
            compose_project_name=project,
            auth_global_circuit_breaker=_flag("AUTH_GLOBAL_CIRCUIT_BREAKER"),
            admin_cli=admin_cli,
        )
