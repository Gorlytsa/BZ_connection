"""Безопасность: пароли (sha256+salt для MVP), JWT, RBAC, проверка ИНН."""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User

SECRET = settings.secret_key.encode()


def hash_password(password: str) -> str:
    salt = hashlib.sha256(SECRET).hexdigest()[:16]
    return salt + "$" + hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    parts = hashed.split("$")
    if len(parts) != 2:
        return False
    return hmac.compare_digest(hashed, hash_password(password))


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.system_role,
        "org": user.org_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, SECRET, algorithm=settings.jwt_algorithm)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Требуется авторизация")
    try:
        payload = jwt.decode(auth[7:], SECRET, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(401, "Сессия истекла, войдите заново")
    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(401, "Пользователь не найден")
    return user


STAFF_ROLES = {"admin", "moderator", "security", "finance", "ops", "support", "auditor"}


def require_roles(*roles: str):
    """RBAC-зависимость: доступ только с указанными системными ролями."""
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.system_role not in roles:
            raise HTTPException(403, "Недостаточно прав для этого действия")
        return user
    return checker


require_staff = require_roles(*STAFF_ROLES)
require_admin = require_roles("admin")
require_security = require_roles("security", "admin")
require_finance = require_roles("finance", "admin")
require_moderator = require_roles("moderator", "admin")
require_ops = require_roles("ops", "admin")


# ------------------------------------------------------------------
# Валидация ИНН (FR-101)
# ------------------------------------------------------------------

COEF_10 = [2, 4, 10, 3, 5, 9, 4, 6, 8]
COEF_11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]


def validate_inn(inn: str) -> bool:
    if not re.fullmatch(r"\d{10}|\d{12}", inn):
        return False
    digits = [int(c) for c in inn]
    if len(digits) == 10:
        control = sum(d * c for d, c in zip(digits, COEF_10)) % 11 % 10
        return control == digits[9]
    c1 = sum(d * c for d, c in zip(digits[:10], COEF_11)) % 11 % 10
    c2 = sum(d * c for d, c in zip(digits[:11], COEF_10)) % 11 % 10
    return c1 == digits[10] and c2 == digits[11]
