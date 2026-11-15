"""Environment-only configuration. Never log settings or connection strings."""

import os
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, repr=False)
class Settings:
    app_env: str = "local"
    auth_verifier: str = "dev"
    database_url: str = ""
    dev_jwt_secret: str = ""
    internal_service_token: str = ""
    owner_user_id: UUID | None = None
    supabase_url: str = ""
    supabase_jwks_url: str = ""
    worker_dispatcher: str = "local"
    github_repository: str = ""
    github_dispatch_token: str = ""
    local_worker_command: str = ""
    agent_runner: str = "stub"

    def __post_init__(self):
        if self.auth_verifier == "dev" and self.app_env != "local":
            raise ValueError("Dev authentication requires APP_ENV=local")
        if self.auth_verifier not in {"dev", "supabase"}:
            raise ValueError("Unsupported AUTH_VERIFIER")
        if self.worker_dispatcher not in {"local", "github"}:
            raise ValueError("Unsupported WORKER_DISPATCHER")
        if self.app_env != "local":
            if self.worker_dispatcher == "local":
                raise ValueError("Local dispatch requires APP_ENV=local")
            if not self.database_url or not self.internal_service_token or not self.supabase_url:
                raise ValueError(
                    "Production database, internal token and Supabase URL are required"
                )
            if not self.supabase_url.startswith("https://"):
                raise ValueError("Supabase requires HTTPS")
            if self.agent_runner == "stub":
                raise ValueError("Stub results require APP_ENV=local")

    @classmethod
    def from_env(cls):
        owner = os.environ.get("OWNER_USER_ID", "")
        return cls(
            app_env=os.environ.get("APP_ENV", "local"),
            auth_verifier=os.environ.get("AUTH_VERIFIER", "dev"),
            database_url=os.environ.get("DATABASE_URL", ""),
            dev_jwt_secret=os.environ.get("DEV_JWT_SECRET", ""),
            internal_service_token=os.environ.get("INTERNAL_SERVICE_TOKEN", ""),
            owner_user_id=UUID(owner) if owner else None,
            supabase_url=os.environ.get("SUPABASE_URL", "").rstrip("/"),
            supabase_jwks_url=os.environ.get("SUPABASE_JWKS_URL", ""),
            worker_dispatcher=os.environ.get("WORKER_DISPATCHER", "local"),
            github_repository=os.environ.get("GITHUB_REPOSITORY", ""),
            github_dispatch_token=os.environ.get("GITHUB_DISPATCH_TOKEN", ""),
            local_worker_command=os.environ.get("LOCAL_WORKER_COMMAND", ""),
            agent_runner=os.environ.get("AGENT_RUNNER", "stub"),
        )
