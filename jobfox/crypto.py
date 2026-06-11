"""Encryption-at-rest for Gmail refresh tokens.

Fernet (AES-128-CBC + HMAC) keyed from TOKEN_ENCRYPTION_KEY. Ciphertext is
stored with an `enc:` prefix so legacy plaintext rows keep working: reads
decrypt only prefixed values, writes always encrypt when a key is set.
That makes rollout a no-op — old tokens migrate naturally the next time
they're written (e.g. on /connect_gmail re-auth).

Generate a key once:  python -c "from cryptography.fernet import Fernet;
print(Fernet.generate_key().decode())"

Without the env var everything passes through as plaintext (dev mode) —
but `enc:` values then raise, which is the correct failure: losing the key
must be loud, not silently treated as a token value.
"""

from __future__ import annotations

import logging

from jobfox import config

log = logging.getLogger(__name__)

_PREFIX = "enc:"


def _fernet():
    key = config.settings().token_encryption_key
    if not key:
        return None
    from cryptography.fernet import Fernet  # type: ignore[import-not-found]

    return Fernet(key.encode())


def encrypt_token(token: str | None) -> str | None:
    """Plaintext → `enc:<ciphertext>`. Passthrough when no key configured."""
    if token is None or token == "":
        return token
    f = _fernet()
    if f is None:
        return token
    return _PREFIX + f.encrypt(token.encode()).decode()


def decrypt_token(value: str | None) -> str | None:
    """`enc:<ciphertext>` → plaintext. Legacy plaintext passes through."""
    if value is None or value == "" or not value.startswith(_PREFIX):
        return value
    f = _fernet()
    if f is None:
        raise RuntimeError(
            "found an encrypted token but TOKEN_ENCRYPTION_KEY is not set"
        )
    return f.decrypt(value[len(_PREFIX):].encode()).decode()
