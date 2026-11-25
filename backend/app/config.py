"""Environment validation shared by API startup and database setup."""
import os
from sqlalchemy.engine import make_url
from urllib.parse import urlsplit

_DEMO_PASSWORDS = {"retail", "password", "postgres", "change-me", "change-me-local-only", "replace-with-a-unique-local-password", ""}


def production_errors(env: dict[str, str] | None = None) -> list[str]:
    values = os.environ if env is None else env
    if values.get("APP_ENV", "development").lower() != "production":
        return []
    errors: list[str] = []
    raw_url = values.get("DATABASE_URL", "").strip()
    if not raw_url:
        return ["DATABASE_URL is required in production"]
    try:
        url = make_url(raw_url)
    except Exception:
        return ["DATABASE_URL is invalid"]
    if url.get_backend_name() != "postgresql":
        errors.append("Production requires a PostgreSQL DATABASE_URL")
    if not url.host or not url.database or not url.username or url.password is None:
        errors.append("Production DATABASE_URL must include a host, database, username, and password")
    if (url.password or "").lower() in _DEMO_PASSWORDS:
        errors.append("Production DATABASE_URL must not use a development or demo password")
    if values.get("COOKIE_SECURE", "").lower() != "true":
        errors.append("COOKIE_SECURE=true is required in production")
    origins = [origin.strip() for origin in values.get("CORS_ORIGINS", "").split(",") if origin.strip()]
    if not origins or "*" in origins:
        errors.append("Production CORS_ORIGINS must contain explicit HTTPS origins and no wildcard")
    if any(urlsplit(origin).scheme != "https" or not urlsplit(origin).hostname or urlsplit(origin).username for origin in origins):
        errors.append("Every production CORS origin must use HTTPS")
    sslmode = url.query.get("sslmode", values.get("DB_SSLMODE", ""))
    if sslmode != "verify-full":
        errors.append("Production database TLS must use sslmode=verify-full")
    if values.get("DEBUG", "false").lower() in {"1", "true", "yes"}:
        errors.append("DEBUG must be disabled in production")
    try:
        session_hours=int(values.get("SESSION_HOURS", "8"))
        if not 1<=session_hours<=24:errors.append("SESSION_HOURS must be between 1 and 24 in production")
    except ValueError:
        errors.append("SESSION_HOURS must be a whole number in production")
    return errors


def validate_production_environment() -> None:
    errors = production_errors()
    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
