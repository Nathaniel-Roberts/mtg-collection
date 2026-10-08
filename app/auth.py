"""Cloudflare Access JWT verification and the request identity.

Access sends an application token in the ``Cf-Access-Jwt-Assertion`` header (browsers
also get a ``CF_Authorization`` cookie). Verification follows
https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/validating-json/
: RS256 keys from ``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`` matched
by ``kid``, ``aud`` equal to the application's AUD tag, ``iss`` equal to the team domain.

User tokens carry ``email``. Service tokens carry ``common_name`` and no email.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from app.config import Settings

log = logging.getLogger(__name__)

ACCESS_HEADER = "Cf-Access-Jwt-Assertion"
ACCESS_COOKIE = "CF_Authorization"


class AccessError(Exception):
    """The Access token was missing, malformed or failed verification."""


@dataclass(frozen=True)
class Identity:
    email: str | None
    common_name: str | None = None
    via: str = "access"  # access, service_token, bearer, dev
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.email or self.common_name or self.via


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class AccessVerifier:
    def __init__(self, settings: Settings, key_source: SigningKeySource | None = None) -> None:
        self.settings = settings
        self._keys = key_source
        if self._keys is None and settings.access_certs_url:
            self._keys = PyJWKClient(
                settings.access_certs_url, cache_keys=True, lifespan=3600, timeout=10
            )

    @property
    def enabled(self) -> bool:
        return self.settings.access_enabled and self._keys is not None

    def verify(self, token: str) -> Identity:
        if not self.enabled or self._keys is None:
            raise AccessError("Cloudflare Access is not configured")
        try:
            signing_key = self._keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.cf_access_aud,
                issuer=self.settings.access_issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AccessError(f"Access token rejected: {exc}") from exc
        email = claims.get("email")
        common_name = claims.get("common_name")
        if email:
            return Identity(email=email.lower(), via="access", claims=claims)
        if common_name:
            return Identity(email=None, common_name=common_name, via="service_token", claims=claims)
        raise AccessError("Access token has neither email nor common_name")


def token_from_request(request: Request) -> str | None:
    return request.headers.get(ACCESS_HEADER) or request.cookies.get(ACCESS_COOKIE)


def resolve_identity(request: Request) -> Identity | None:
    """Who is making this request, or None when nobody verifiable is."""
    settings: Settings = request.app.state.settings
    verifier: AccessVerifier = request.app.state.access_verifier
    token = token_from_request(request)
    if token and verifier.enabled:
        try:
            return verifier.verify(token)
        except AccessError as exc:
            log.info("%s", exc)
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    if settings.dev_mode:
        return Identity(email=settings.dev_user_email, via="dev")
    return None


def require_user(request: Request) -> Identity:
    """FastAPI dependency: a verified person (email), else 401."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        identity = resolve_identity(request)
        request.state.identity = identity
    if identity is None or identity.email is None:
        raise HTTPException(status_code=401, detail="Not signed in through Cloudflare Access")
    return identity
