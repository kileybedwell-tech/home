"""OAuth 2.0 for eBay: application tokens, the user consent flow, refresh.

eBay issues two kinds of token:

* an **application token** (client-credentials grant) — identifies your app,
  enough for public data such as the Browse API;
* a **user token** (authorization-code grant) — identifies *you as a seller*,
  and is what every Sell API call requires.

User access tokens expire after roughly two hours; the refresh token that
comes with them lasts about 18 months.  :class:`TokenStore` keeps both on
disk and refreshes the access token transparently.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .http import request

#: Refresh this many seconds before the token actually expires.
EXPIRY_SKEW = 300


class AuthError(RuntimeError):
    """Raised when a token cannot be obtained or has irrecoverably expired."""


def _basic_auth(config: Config) -> str:
    raw = f"{config.client_id}:{config.client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _token_request(config: Config, form: dict[str, str]) -> dict[str, Any]:
    payload = request(
        "POST",
        config.token_url,
        headers={"Authorization": _basic_auth(config)},
        form_body=form,
    )
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise AuthError(f"unexpected token response from eBay: {payload!r}")
    return payload


def application_token(config: Config, scopes: tuple[str, ...] | None = None) -> str:
    """Client-credentials grant. No user consent, no access to seller data."""
    scope = " ".join(scopes or (config.scopes[0],))
    payload = _token_request(
        config, {"grant_type": "client_credentials", "scope": scope}
    )
    return payload["access_token"]


def authorization_url(
    config: Config,
    *,
    scopes: tuple[str, ...] | None = None,
    state: str | None = None,
    prompt_login: bool = False,
) -> str:
    """Build the consent URL the seller opens in a browser.

    ``redirect_uri`` must be the app's **RuName** (eBay's opaque name for the
    redirect entry), not the https URL it points at.
    """
    if not config.redirect_uri:
        raise AuthError(
            "EBAY_REDIRECT_URI is not set. Use the RuName from "
            "developer.ebay.com > Application Keys > User Tokens, not the URL."
        )
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes or config.scopes),
    }
    if state:
        params["state"] = state
    if prompt_login:
        params["prompt"] = "login"
    return f"{config.authorize_url}?{urllib.parse.urlencode(params)}"


def parse_authorization_code(pasted: str) -> str:
    """Accept either a bare code or the whole redirect URL and return the code.

    eBay percent-encodes the code in the redirect; decoding it before the token
    exchange is required, and forgetting to is the usual cause of
    ``invalid_grant``.
    """
    value = pasted.strip()
    if not value:
        raise AuthError("no authorization code supplied")
    if "?" in value or value.lower().startswith("http"):
        query = urllib.parse.urlparse(value).query
        params = urllib.parse.parse_qs(query)
        if "error" in params:
            description = params.get("error_description", [""])[0]
            raise AuthError(
                f"eBay declined the authorization: {params['error'][0]} {description}".strip()
            )
        codes = params.get("code")
        if not codes:
            raise AuthError(f"no 'code' parameter found in {value!r}")
        return codes[0]
    return urllib.parse.unquote(value)


@dataclass
class Tokens:
    """A user token pair plus the absolute times at which each expires."""

    access_token: str
    refresh_token: str
    access_expires_at: float
    refresh_expires_at: float
    scopes: tuple[str, ...] = ()

    @property
    def access_expired(self) -> bool:
        return time.time() >= self.access_expires_at - EXPIRY_SKEW

    @property
    def refresh_expired(self) -> bool:
        return time.time() >= self.refresh_expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "scopes": list(self.scopes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tokens":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            access_expires_at=float(data["access_expires_at"]),
            refresh_expires_at=float(data["refresh_expires_at"]),
            scopes=tuple(data.get("scopes", ())),
        )

    @classmethod
    def from_response(
        cls,
        payload: dict[str, Any],
        *,
        scopes: tuple[str, ...],
        previous: "Tokens | None" = None,
        now: float | None = None,
    ) -> "Tokens":
        """Build tokens from an eBay token response.

        A refresh response omits ``refresh_token``/``refresh_token_expires_in``;
        in that case the previous refresh token and its expiry carry over.
        """
        moment = time.time() if now is None else now
        refresh_token = payload.get("refresh_token") or (
            previous.refresh_token if previous else ""
        )
        if not refresh_token:
            raise AuthError("token response contained no refresh token")
        if "refresh_token_expires_in" in payload:
            refresh_expires_at = moment + float(payload["refresh_token_expires_in"])
        elif previous is not None:
            refresh_expires_at = previous.refresh_expires_at
        else:
            raise AuthError("token response contained no refresh token expiry")
        return cls(
            access_token=payload["access_token"],
            refresh_token=refresh_token,
            access_expires_at=moment + float(payload.get("expires_in", 7200)),
            refresh_expires_at=refresh_expires_at,
            scopes=scopes,
        )


def exchange_code(config: Config, code: str) -> Tokens:
    """Trade a consent code for a user access + refresh token pair."""
    payload = _token_request(
        config,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
        },
    )
    return Tokens.from_response(payload, scopes=config.scopes)


def refresh_tokens(config: Config, tokens: Tokens) -> Tokens:
    """Mint a fresh access token from a still-valid refresh token."""
    if tokens.refresh_expired:
        raise AuthError("refresh token has expired; run `python -m ebay login` again")
    scopes = tokens.scopes or config.scopes
    payload = _token_request(
        config,
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "scope": " ".join(scopes),
        },
    )
    return Tokens.from_response(payload, scopes=scopes, previous=tokens)


def default_token_path(environment: str) -> Path:
    """Where tokens live: ``$EBAY_TOKEN_FILE`` or under XDG config."""
    override = os.environ.get("EBAY_TOKEN_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "ebay-connect" / f"{environment}.json"


class TokenStore:
    """Loads, refreshes and persists the seller's token pair."""

    def __init__(self, config: Config, path: Path | None = None) -> None:
        self.config = config
        self.path = path or default_token_path(config.environment)
        self._tokens: Tokens | None = None

    def load(self) -> Tokens | None:
        """Load tokens from disk, or seed them from EBAY_REFRESH_TOKEN.

        Seeding from the environment is what lets an ephemeral container work
        without a browser consent step: the refresh token is the durable half
        of the pair, and one refresh call turns it into a usable access token.
        The seeded access token is deliberately expired so the first API call
        fetches a real one.
        """
        if self._tokens is None and self.path.is_file():
            self._tokens = Tokens.from_dict(json.loads(self.path.read_text("utf-8")))
        if self._tokens is None:
            self._tokens = self._from_environment()
        return self._tokens

    def _from_environment(self) -> Tokens | None:
        seeded = os.environ.get("EBAY_REFRESH_TOKEN", "").strip()
        if not seeded:
            return None
        return Tokens(
            access_token="",
            refresh_token=seeded,
            access_expires_at=0.0,  # expired, so the first call refreshes
            # The real expiry is unknown from the token alone; eBay rejects a
            # dead refresh token clearly enough, so do not guess short.
            refresh_expires_at=time.time() + 86400 * 550,
            scopes=self.config.scopes,
        )

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a private temp file so the secret is never briefly
        # world-readable, then swap it into place.
        temp = self.path.with_suffix(".tmp")
        temp.touch(mode=0o600, exist_ok=True)
        os.chmod(temp, 0o600)
        temp.write_text(json.dumps(tokens.to_dict(), indent=2), encoding="utf-8")
        temp.replace(self.path)
        os.chmod(self.path, 0o600)
        self._tokens = tokens

    def clear(self) -> bool:
        self._tokens = None
        if self.path.is_file():
            self.path.unlink()
            return True
        return False

    def access_token(self) -> str:
        """Return a usable access token, refreshing and persisting if needed."""
        tokens = self.load()
        if tokens is None:
            raise AuthError(
                f"no saved eBay tokens at {self.path}, and EBAY_REFRESH_TOKEN is "
                "not set. Run `python -m ebay login` first, or set that variable "
                "to the refresh token from an earlier login."
            )
        if tokens.access_expired:
            tokens = refresh_tokens(self.config, tokens)
            self.save(tokens)
        return tokens.access_token
