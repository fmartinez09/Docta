"""Generate a private, reusable VM Compose environment; no network calls."""

import argparse
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit


def _domain(value: str) -> str:
    if len(value) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z][a-z0-9-]{0,61}[a-z0-9]", value
    ):
        raise ValueError("Use a lowercase public DNS name for each domain.")
    return value


def _https_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(c.isspace() for c in value)
            or any(c in value for c in "$\\\"'")):
        raise ValueError("OIDC endpoints must be safe HTTPS URLs without credentials or query.")
    return value


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9@._:/-]{1,200}", value):
        raise ValueError("OIDC identifiers contain unsupported characters.")
    return value


def initialize(path: Path, *, web_domain: str, storage_domain: str, issuer: str,
               jwks: str, audience: str, client_id: str) -> bool:
    """Create once, or verify the public identity of an existing profile."""
    selected = {
        "DOCTA_WEB_DOMAIN": _domain(web_domain),
        "DOCTA_STORAGE_DOMAIN": _domain(storage_domain),
        "DOCTA_OIDC_ISSUER": _https_url(issuer),
        "DOCTA_OIDC_JWKS_URL": _https_url(jwks),
        "DOCTA_OIDC_AUDIENCE": _identifier(audience),
        "DOCTA_WEB_OIDC_CLIENT_ID": _identifier(client_id),
    }
    if selected["DOCTA_WEB_DOMAIN"] == selected["DOCTA_STORAGE_DOMAIN"]:
        raise ValueError("Web and storage require distinct DNS names.")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Existing VM configuration must be a regular file.")
        existing = dict(line.split("=", 1) for line in path.read_text().splitlines()
                        if "=" in line and not line.startswith("#"))
        if any(existing.get(key) != value for key, value in selected.items()):
            raise ValueError("Existing VM configuration differs; it was preserved.")
        required = (
            "DOCTA_WEB_SESSION_SECRET", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
            "DOCTA_VM_DATABASE_URL", "RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY", "DOCTA_S3_BUCKET",
        )
        if any(not existing.get(key) for key in required):
            raise ValueError("Existing VM configuration is incomplete; it was preserved.")
        if path.stat().st_mode & 0o077:
            raise ValueError("VM configuration permissions must restrict access to the owner.")
        return False
    db_password = secrets.token_urlsafe(32)
    values = selected | {
        "DOCTA_WEB_SESSION_SECRET": secrets.token_urlsafe(48),
        "POSTGRES_DB": "docta",
        "POSTGRES_USER": "docta",
        "POSTGRES_PASSWORD": db_password,
        "DOCTA_VM_DATABASE_URL": f"postgresql://docta:{db_password}@postgres:5432/docta",
        "RUSTFS_ACCESS_KEY": "docta-" + secrets.token_hex(12),
        "RUSTFS_SECRET_KEY": secrets.token_urlsafe(32),
        "DOCTA_S3_BUCKET": "docta-documents-" + secrets.token_hex(6),
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
        output.flush()
        os.fsync(output.fileno())
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("web-domain", "storage-domain", "issuer", "jwks", "audience", "client-id"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        created = initialize(Path('.env.vm'), web_domain=args.web_domain,
                             storage_domain=args.storage_domain, issuer=args.issuer,
                             jwks=args.jwks, audience=args.audience, client_id=args.client_id)
    except (OSError, ValueError) as error:
        parser.exit(1, f"VM configuration failed: {error}\n")
    print("Private VM configuration " + ("created." if created else "already exists and matches."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
