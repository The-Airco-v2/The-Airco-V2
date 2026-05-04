from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # TimescaleDB
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "airco"
    postgres_user: str = "airco"
    postgres_password: str = "changeme"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_public_url: str = ""
    minio_public_presign: bool = True
    minio_access_key: str = "airco"
    minio_secret_key: str = "changeme"
    minio_bucket: str = "airco-evidence"
    minio_secure: bool = False

    # Centrifugo
    centrifugo_api_url: str = "http://localhost:8080/api"
    centrifugo_api_key: str = "changeme"
    centrifugo_token_secret: str = "changeme"

    # Triton
    triton_url: str = "localhost:8001"

    # Tenant
    tenant_id: str = "default"
    tenant_name: str = "Default Tenant"

    # Supabase Auth
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # Backend session auth
    session_secret: str = ""
    session_cookie_name: str = "airco_session"
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_secure_cookie: bool = True
    session_same_site: str = "lax"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
