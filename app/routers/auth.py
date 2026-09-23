"""API: регистрация, вход, согласия (FR-101..FR-103), profile."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Consent, Notification, Organization, OrganizationRole, User
from ..services.audit import audit
from ..services.security import (create_token, get_current_user, hash_password,
                                 validate_inn, verify_password)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterIn(BaseModel):
    full_name: str
    email: str
    phone: str
    password: str = Field(min_length=8)
    company_name: str
    inn: str
    legal_form: str = "OOO"
    roles: list[str] = ["location"]           # потенциальные роли (FR-101)
    consents: list[str] = []                  # коды согласий (FR-103)
    admin_secret: str | None = None           # служебный ключ для сотрудников платформы

REQUIRED_CONSENTS = {"PD_PROCESSING", "USER_AGREEMENT", "ANTIFRAUD", "COUNTERPARTY_CHECK"}
VALID_ROLES = {"location", "capex", "service"}


@router.post("/register")
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if db.query(User).filter_by(email=data.email.lower()).one_or_none():
        raise HTTPException(409, "Email уже зарегистрирован")
    if not validate_inn(data.inn):
        raise HTTPException(400, "ИНН не проходит проверку контрольного числа (FR-101)")
    unknown_roles = set(data.roles) - VALID_ROLES
    if unknown_roles:
        raise HTTPException(400, f"Неизвестные роли: {unknown_roles}")
    staff = bool(data.admin_secret)
    if not staff and not REQUIRED_CONSENTS.issubset(set(data.consents)):
        raise HTTPException(400, "Необходимо подтвердить все обязательные согласия (FR-103)")

    user = User(email=data.email.lower(), phone=data.phone, full_name=data.full_name,
                password_hash=hash_password(data.password),
                system_role="admin" if staff else "partner")
    db.add(user)
    if not staff:
        org = Organization(legal_name=data.company_name, short_name=data.company_name,
                           inn=data.inn, legal_form=data.legal_form,
                           sb_status="PENDING")  # «Новичок / На проверке» (FR-102)
        db.add(org)
        db.flush()
        user.org_id = org.id
        for r in data.roles:
            db.add(OrganizationRole(org_id=org.id, role_type=r))
    for c in data.consents:
        db.add(Consent(user_id=user.id, doc_code=c, version="1.0",
                       ip=request.client.host if request.client else "",
                       user_agent=request.headers.get("user-agent", "")))
    audit(db, user, "USER_REGISTERED", "user", user.id,
          new_value={"email": user.email, "inn": data.inn})
    db.commit()
    return {"token": create_token(user), "user_id": user.id, "org_id": user.org_id,
            "sb_status": "PENDING" if not staff else "STAFF",
            "hint": "До завершения проверки СБ недоступны заявки, отклики и выплаты (FR-102)"}


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    user = db.query(User).filter_by(email=data.email.lower()).one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Неверный email или пароль")
    user.last_login_at = datetime.now(timezone.utc)
    audit(db, user, "USER_LOGIN", "user", user.id)
    db.commit()
    return {"token": create_token(user), "system_role": user.system_role, "org_id": user.org_id}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id) if user.org_id else None
    notifications = db.query(Notification).filter_by(user_id=user.id). \
        order_by(Notification.id.desc()).limit(50).all()
    return {
        "user": {"id": user.id, "email": user.email, "name": user.full_name,
                 "system_role": user.system_role},
        "org": None if not org else {
            "id": org.id, "legal_name": org.legal_name, "inn": org.inn,
            "sb_status": org.sb_status, "rating": org.rating, "blocked": org.blocked,
            "roles": [{"role_type": r.role_type, "is_active": r.is_active} for r in org.roles],
        },
        "notifications": [{"id": n.id, "code": n.template_code, "message": n.message,
                           "link": n.link, "created_at": n.created_at.isoformat()}
                          for n in notifications],
    }
