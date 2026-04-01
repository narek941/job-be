from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from armapply.config import ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALG, JWT_SECRET
from armapply.users_db import get_user_by_id

# bcrypt limits passwords to 72 bytes; truncate consistently on hash and verify
_MAX_PW_BYTES = 72

bearer = HTTPBearer(auto_error=False)


def _password_bytes(password: str) -> bytes:
    b = password.encode("utf-8")
    if len(b) > _MAX_PW_BYTES:
        return b[:_MAX_PW_BYTES]
    return b


def hash_password(p: str) -> str:
    """Bcrypt hash (no passlib; compatible with bcrypt 4.x on Python 3.13)."""
    digest = bcrypt.hashpw(_password_bytes(p), bcrypt.gensalt(rounds=12))
    return digest.decode("ascii")


def verify_password(p: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(p), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(sub: str, user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": sub, "uid": user_id, "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALG,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])


async def current_user(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(cred.credentials)
        uid = int(payload.get("uid", 0))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
