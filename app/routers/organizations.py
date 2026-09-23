"""API организаций: карточка, роли, банковские реквизиты (FR-104, FR-105, 31.5)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Organization, OrganizationRole, User
from ..services.audit import audit
from ..services.security import get_current_user

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def _my_org(user: User, db: Session) -> Organization:
    if not user.org_id:
        raise HTTPException(400, "К аккаунту не привязана организация")
    org = db.get(Organization, user.org_id)
    if not org:
        raise HTTPException(404, "Организация не найдена")
    return org


@router.get("/me")
def my_org(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = _my_org(user, db)
    return {
        "id": org.id, "legal_name": org.legal_name, "short_name": org.short_name,
        "inn": org.inn, "kpp": org.kpp, "ogrn": org.ogrn, "legal_form": org.legal_form,
        "tax_regime": org.tax_regime, "legal_address": org.legal_address,
        "bank_details": org.bank_details, "bank_details_verified": org.bank_details_verified,
        "edo_id": org.edo_id, "signer_name": org.signer_name,
        "signer_authority": org.signer_authority, "sb_status": org.sb_status,
        "rating": org.rating,
        "roles": [{"role_type": r.role_type, "is_active": r.is_active,
                   "regions": r.regions, "max_stations": r.max_stations} for r in org.roles],
    }


class OrgUpdate(BaseModel):
    short_name: str | None = None
    kpp: str | None = None
    ogrn: str | None = None
    tax_regime: str | None = None
    legal_address: str | None = None
    edo_id: str | None = None
    signer_name: str | None = None
    signer_authority: str | None = None


@router.patch("/me")
def update_org(data: OrgUpdate, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    org = _my_org(user, db)
    changes = {}
    for k, v in data.model_dump(exclude_none=True).items():
        changes[k] = getattr(org, k)
        setattr(org, k, v)
    audit(db, user, "ORG_UPDATED", "organization", org.id, old_value=changes, new_value=
          {k: v for k, v in data.model_dump(exclude_none=True).items()})
    db.commit()
    return {"ok": True}


class BankDetailsIn(BaseModel):
    bank_details: str


@router.post("/me/bank-details")
def update_bank(data: BankDetailsIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Изменение реквизитов требует повторной верификации (FR-809, 31.5)."""
    org = _my_org(user, db)
    old = org.bank_details
    org.bank_details = data.bank_details
    org.bank_details_verified = False  # до подтверждения финансистом
    audit(db, user, "BANK_DETAILS_CHANGED", "organization", org.id,
          old_value=old, new_value=data.bank_details,
          reason="Требуется повторная верификация реквизитов")
    db.commit()
    return {"ok": True, "verified": False,
            "hint": "Реквизиты изменены. Выплаты заблокированы до подтверждения (31.5)"}


class RoleIn(BaseModel):
    role_type: str
    regions: str = ""
    max_stations: int = 100


@router.post("/me/roles")
def add_role(data: RoleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.role_type not in ("location", "capex", "service"):
        raise HTTPException(400, "Недопустимая роль")
    org = _my_org(user, db)
    exists = db.query(OrganizationRole).filter_by(org_id=org.id, role_type=data.role_type).first()
    if exists:
        exists.is_active = True
        exists.regions = data.regions
        exists.max_stations = data.max_stations
    else:
        db.add(OrganizationRole(org_id=org.id, role_type=data.role_type,
                                regions=data.regions, max_stations=data.max_stations))
    audit(db, user, "ORG_ROLE_SET", "organization", org.id, new_value=data.role_type)
    db.commit()
    return {"ok": True}
