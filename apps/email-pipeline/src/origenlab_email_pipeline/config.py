"""Load settings from environment. No repo-specific or private paths in code."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_root() -> Path:
    return Path.home() / "data" / "origenlab-email"

def _repo_root() -> Path:
    # src/origenlab_email_pipeline/config.py -> repo root
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORIGENLAB_",
        # Repo-local .env (apps/email-pipeline/.env), not cwd — works from monorepo root too.
        env_file=str(_repo_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_root: Path = Field(default_factory=_default_data_root)

    raw_pst_dir: Path | None = Field(default=None, description="Directory containing .pst files")
    mbox_dir: Path | None = Field(default=None, description="Directory with mbox trees from readpst")
    sqlite_path: Path | None = Field(default=None, description="SQLite DB path")
    jsonl_path: Path | None = Field(default=None, description="JSONL output path")
    reports_dir: Path | None = Field(
        default=None,
        description="Client reports root (timestamped runs under here)",
    )

    gmail_oauth_client_json: str | None = Field(
        default=None,
        description="Path to Google Cloud Desktop OAuth client JSON (Gmail IMAP ingest)",
    )
    gmail_workspace_user: str | None = Field(
        default=None,
        description="Workspace mailbox address for Gmail IMAP (e.g. contacto@domain)",
    )
    gmail_token_json: str | None = Field(
        default=None,
        description="Optional path for Gmail OAuth refresh token JSON",
    )
    gmail_oauth_open_browser: bool = Field(
        default=True,
        description="If false, print OAuth URL only (use on WSL when xdg-open/gio fails)",
    )

    # Tatiana copilot — OpenAI-compatible chat (optional; see docs/dataset/TATIANA_DRAFTING_COPILOT.md)
    tatiana_openai_api_key: str | None = Field(
        default=None,
        description="API key for OpenAIChatDraftGenerator (falls back to OPENAI_API_KEY if unset)",
    )
    tatiana_openai_model: str = Field(
        default="gpt-4o-mini",
        description="Chat model id for Tatiana draft generation",
    )
    tatiana_openai_base_url: str | None = Field(
        default=None,
        description="Optional OpenAI API base URL (proxies, Azure-style endpoints)",
    )
    tatiana_openai_timeout_seconds: float = Field(
        default=60.0,
        ge=5.0,
        le=600.0,
        description="HTTP timeout for OpenAI chat completion calls",
    )
    tatiana_llm_min_body_chars: int = Field(
        default=40,
        ge=0,
        le=10_000,
        description="Abstain if case body_text is shorter than this (chars)",
    )
    tatiana_llm_abstain_on_empty_retrieval: bool = Field(
        default=True,
        description="If true, abstain when both style and precedent lists in the prompt are empty",
    )

    def resolved_tatiana_openai_api_key(self) -> str | None:
        k = (self.tatiana_openai_api_key or "").strip()
        if k:
            return k
        env = (os.environ.get("OPENAI_API_KEY") or "").strip()
        return env or None

    def resolved_raw_pst_dir(self) -> Path:
        return self.raw_pst_dir or (self.data_root / "raw_pst")

    def resolved_mbox_dir(self) -> Path:
        return self.mbox_dir or (self.data_root / "mbox")

    def resolved_sqlite_path(self) -> Path:
        return self.sqlite_path or (self.data_root / "sqlite" / "emails.sqlite")

    def resolved_jsonl_path(self) -> Path:
        return self.jsonl_path or (self.data_root / "jsonl" / "emails.jsonl")

    def resolved_reports_dir(self) -> Path:
        # Default to repo-local reports to keep outputs discoverable.
        # Can be overridden via ORIGENLAB_REPORTS_DIR for large runs outside repo.
        return self.reports_dir or (_repo_root() / "reports" / "out")


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_flag_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def recovery_mode_requested_from_environ() -> bool:
    """Match API recovery detection: both immutable + offline-confirm process flags."""
    return _env_flag_truthy("ORIGENLAB_SQLITE_IMMUTABLE_RO") and _env_flag_truthy(
        "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY"
    )


def dotenv_disabled_from_environ() -> bool:
    """Disable project dotenv when recovery is requested or explicitly opted out."""
    return recovery_mode_requested_from_environ() or _env_flag_truthy(
        "ORIGENLAB_DISABLE_DOTENV"
    )


def load_settings(*, enable_dotenv: bool | None = None) -> Settings:
    """
    Load email-pipeline settings.

    When recovery flags (or ``ORIGENLAB_DISABLE_DOTENV=1``) are present in the
    process environment, skip ``load_dotenv`` and construct Settings with
    ``_env_file=None`` so repo ``.env`` files cannot inject Postgres/Gmail/tokens.
    Process environment variables remain visible.
    """
    use_dotenv = (not dotenv_disabled_from_environ()) if enable_dotenv is None else bool(
        enable_dotenv
    )
    if use_dotenv:
        # Pydantic only binds ORIGENLAB_* fields from .env; OPENAI_API_KEY must hit os.environ.
        env_path = _repo_root() / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
        return Settings()
    return Settings(_env_file=None)
