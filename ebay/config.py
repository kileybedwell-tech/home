"""Configuration and endpoint resolution for the eBay Sell APIs.

Credentials are read from the environment (optionally seeded from a ``.env``
file).  Nothing in this module ever writes a secret to disk or to stdout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PRODUCTION = "production"
SANDBOX = "sandbox"

# The Media API is served from apim.ebay.com, not the api.ebay.com host every
# other Sell API uses. Pointing image uploads at api.ebay.com 404s.
_HOSTS = {
    PRODUCTION: {
        "api": "https://api.ebay.com",
        "auth": "https://auth.ebay.com",
        "media": "https://apim.ebay.com",
    },
    SANDBOX: {
        "api": "https://api.sandbox.ebay.com",
        "auth": "https://auth.sandbox.ebay.com",
        "media": "https://apim.sandbox.ebay.com",
    },
}

# Scope identifiers are always minted against api.ebay.com, even for sandbox
# apps.  Using the sandbox host here is a common cause of invalid_scope.
SCOPE_ROOT = "https://api.ebay.com/oauth/api_scope"

#: Scopes needed to read and manage seller listings, orders and policies.
DEFAULT_SCOPES = (
    SCOPE_ROOT,
    f"{SCOPE_ROOT}/sell.inventory",
    f"{SCOPE_ROOT}/sell.account",
    f"{SCOPE_ROOT}/sell.fulfillment",
)

#: Read-only subset, useful for a first connection that cannot change anything.
READONLY_SCOPES = (
    SCOPE_ROOT,
    f"{SCOPE_ROOT}/sell.inventory.readonly",
    f"{SCOPE_ROOT}/sell.account.readonly",
    f"{SCOPE_ROOT}/sell.fulfillment.readonly",
)


class ConfigError(RuntimeError):
    """Raised when required credentials are missing or malformed."""


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Seed ``os.environ`` from a ``.env`` file. Existing vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


#: Order in which setup writes keys, with the comment that precedes each.
ENV_TEMPLATE = (
    ("EBAY_CLIENT_ID", "App ID (Client ID) from developer.ebay.com/my/keys"),
    ("EBAY_CLIENT_SECRET", "Cert ID (Client Secret) from the same keyset"),
    ("EBAY_REDIRECT_URI", "RuName from the User Tokens tab - a name, not a URL"),
    ("EBAY_ENVIRONMENT", "production | sandbox"),
    ("EBAY_MARKETPLACE_ID", "EBAY_US, EBAY_GB, EBAY_DE, EBAY_AU, ..."),
    ("EBAY_CONTENT_LANGUAGE", "language tag matching the marketplace"),
)


def check_credentials(values: dict[str, str]) -> list[str]:
    """Warn about the credential mix-ups that produce opaque eBay errors.

    Returns human-readable warnings; an empty list means nothing looked wrong.
    These are heuristics over eBay's own naming conventions, not validation —
    only eBay can say whether a key really works.
    """
    warnings = []
    redirect = values.get("EBAY_REDIRECT_URI", "")
    if redirect.lower().startswith(("http://", "https://")):
        warnings.append(
            "EBAY_REDIRECT_URI looks like a URL. eBay wants the RuName - the "
            "name shown beside the redirect entry, not the URL it points at."
        )
    environment = values.get("EBAY_ENVIRONMENT", PRODUCTION)
    client_id = values.get("EBAY_CLIENT_ID", "")
    if "SBX-" in client_id.upper() and environment == PRODUCTION:
        warnings.append(
            "the App ID looks like a Sandbox key (SBX) but the environment is "
            "production. Set EBAY_ENVIRONMENT=sandbox, or use the Production keyset."
        )
    if "PRD-" in client_id.upper() and environment == SANDBOX:
        warnings.append(
            "the App ID looks like a Production key (PRD) but the environment is "
            "sandbox. Use the Sandbox keyset, or set EBAY_ENVIRONMENT=production."
        )
    return warnings


def write_env_file(path: str | os.PathLike[str], values: dict[str, str]) -> Path:
    """Write a .env the owner alone can read, and return where it landed."""
    target = Path(path).expanduser()
    lines = ["# Written by `python -m ebay setup`. Never commit this file.", ""]
    for key, comment in ENV_TEMPLATE:
        if key in values:
            lines.extend([f"# {comment}", f"{key}={values[key]}", ""])
    target.parent.mkdir(parents=True, exist_ok=True)
    # Create private up front so the secret is never briefly world-readable.
    temp = target.with_name(target.name + ".tmp")
    temp.touch(mode=0o600, exist_ok=True)
    os.chmod(temp, 0o600)
    temp.write_text("\n".join(lines), encoding="utf-8")
    temp.replace(target)
    os.chmod(target, 0o600)
    return target


@dataclass(frozen=True)
class Config:
    """Everything needed to talk to one eBay environment as one app."""

    client_id: str
    client_secret: str
    redirect_uri: str = ""  # the RuName, not a literal URL (see README)
    environment: str = PRODUCTION
    marketplace_id: str = "EBAY_US"
    content_language: str = "en-US"
    scopes: tuple[str, ...] = field(default=DEFAULT_SCOPES)

    def __post_init__(self) -> None:
        if self.environment not in _HOSTS:
            raise ConfigError(
                f"unknown environment {self.environment!r}; "
                f"expected one of {', '.join(sorted(_HOSTS))}"
            )

    @property
    def api_host(self) -> str:
        return _HOSTS[self.environment]["api"]

    @property
    def auth_host(self) -> str:
        return _HOSTS[self.environment]["auth"]

    @property
    def media_host(self) -> str:
        """Host for the Media API. Override with EBAY_MEDIA_HOST if eBay moves it."""
        return os.environ.get("EBAY_MEDIA_HOST", "").strip() or _HOSTS[self.environment]["media"]

    @property
    def token_url(self) -> str:
        return f"{self.api_host}/identity/v1/oauth2/token"

    @property
    def authorize_url(self) -> str:
        return f"{self.auth_host}/oauth2/authorize"

    @classmethod
    def from_env(cls, environment: str | None = None) -> "Config":
        """Build a Config from EBAY_* environment variables."""
        env = environment or os.environ.get("EBAY_ENVIRONMENT", PRODUCTION)
        client_id = os.environ.get("EBAY_CLIENT_ID", "").strip()
        client_secret = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
        missing = [
            name
            for name, value in (
                ("EBAY_CLIENT_ID", client_id),
                ("EBAY_CLIENT_SECRET", client_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "missing required environment variable(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )
        raw_scopes = os.environ.get("EBAY_SCOPES", "").strip()
        scopes = tuple(raw_scopes.split()) if raw_scopes else DEFAULT_SCOPES
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=os.environ.get("EBAY_REDIRECT_URI", "").strip(),
            environment=env,
            marketplace_id=os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US").strip(),
            content_language=os.environ.get("EBAY_CONTENT_LANGUAGE", "en-US").strip(),
            scopes=scopes,
        )
