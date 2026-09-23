"""Админка платформы: СБ, модерация, операции, финансы, аудит (раздел 27)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (Application, AuditLog, Location, Organization, Payment, Payout,
                      SecurityCheck, Setting, Station, StationParticipant, StationType, User)
from ..services import finance
from ..services.audit import audit, notify_org
from ..services.security import (get_current_user, require_admin, require_finance,
                                 require_moderator, require_ops, require_roles,
                                 require_security, require_staff)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _require_moderator(user: User):
    if user.system_role not in ("moderator", "admin"):
        raise HTTPException(403, "Только для модератора/администратора")


# ------------------------------------------------------------------
# Дашборд
# ------------------------------------------------------------------

@router.get("/dashboard")
def dashboard(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    from ..models import Application as A
    gmv = sum(p.amount for p in db.query(Payment).filter_by(status="RECEIVED").all())
    return {
        "organizations": db.query(Organization).count(),
        "sb_pending": db.query(Organization).filter_by(sb_status="PENDING").count(),
        "applications_on_exchange": db.query(A).filter(A.status.in_(["ON_EXCHANGE", "REASSEMBLY"])).count(),
        "stations_active": db.query(Station).filter_by(status="ACTIVE").count(),
        "gmv_total": gmv,
        "platform_fee": finance.get_account(db, "PLATFORM_FEE", 0, "fee").balance_available,
    }


# ------------------------------------------------------------------
# Безопасность (27.4): проверки СБ, блокировки
# ------------------------------------------------------------------

@router.get("/security/pending")
def security_queue(user: User = Depends(require_security), db: Session = Depends(get_db)):
    orgs = db.query(Organization).filter_by(sb_status="PENDING").all()
    return [{"id": o.id, "legal_name": o.legal_name, "inn": o.inn,
             "roles": [r.role_type for r in o.roles], "created_at": o.created_at.isoformat()}
            for o in orgs]


class SbDecision(BaseModel):
    decision: str  # APPROVED / REJECTED / NEED_DOCS
    risk_score: float | None = None
    comments: str = ""


@router.post("/security/{org_id}/decision")
def sb_decision(org_id: int, data: SbDecision, user: User = Depends(require_security),
                db: Session = Depends(get_db)):
    """Решение по проверке СБ (FR-102, тесты «Безопасность»)."""
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "Организация не найдена")
    if data.decision not in ("APPROVED", "REJECTED", "NEED_DOCS"):
        raise HTTPException(400, "Недопустимое решение")
    old = org.sb_status
    org.sb_status = data.decision
    org.sb_risk_score = data.risk_score
    org.sb_comments = data.comments
    db.add(SecurityCheck(org_id=org.id, status=data.decision, result=data.comments,
                         risk_score=data.risk_score, checked_by=user.id))
    msg = {"APPROVED": f"Проверка СБ пройдена ({org.legal_name}). Полный доступ открыт.",
           "REJECTED": f"Проверка СБ не пройдена. Причина: {data.comments}",
           "NEED_DOCS": f"Для продолжения работы загрузите дополнительные документы. {data.comments}"}
    notify_org(db, org.id, "SB_DECISION", msg[data.decision], link="/#/profile")
    audit(db, user, "SECURITY_DECISION", "organization", org.id, old_value=old,
          new_value=data.decision, reason=data.comments)
    db.commit()
    return {"ok": True, "sb_status": org.sb_status}


class BlockIn(BaseModel):
    block: bool
    reason: str


@router.post("/organizations/{org_id}/block")
def block_org(org_id: int, data: BlockIn, user: User = Depends(require_security),
              db: Session = Depends(get_db)):
    """Блокировка участника (FR-605, 17.5): новые действия запрещены, выплаты on hold."""
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "Организация не найдена")
    if not data.reason:
        raise HTTPException(400, "Причина блокировки обязательна")
    org.blocked = data.block
    if data.block:
        for po in db.query(Payout).filter(Payout.org_id == org.id,
                                          Payout.status.in_(["DRAFT", "PENDING_APPROVAL",
                                                             "APPROVED"])).all():
            po.status = "ON_HOLD"
        for st_part in db.query(StationParticipant).filter_by(org_id=org.id, status="ACTIVE").all():
            st = db.get(Station, st_part.station_id)
            if st and st.status == "ACTIVE":
                pass  # станция продолжает работать; приостановка — отдельное решение (17.5)
    notify_org(db, org.id, "ACCOUNT_BLOCKED" if data.block else "ACCOUNT_UNBLOCKED",
               f"Аккаунт {'заблокирован' if data.block else 'разблокирован'}. Причина: {data.reason}",
               link="/#/profile")
    audit(db, user, "ORG_BLOCK_CHANGED", "organization", org.id,
          new_value={"blocked": data.block}, reason=data.reason)
    db.commit()
    return {"ok": True, "blocked": org.blocked}


@router.get("/organizations")
def list_orgs(q: str = "", user: User = Depends(require_staff), db: Session = Depends(get_db)):
    query = db.query(Organization)
    if q:
        query = query.filter((Organization.legal_name.ilike(f"%{q}%")) |
                             (Organization.inn.ilike(f"%{q}%")))
    return [{"id": o.id, "legal_name": o.legal_name, "inn": o.inn, "sb_status": o.sb_status,
             "blocked": o.blocked, "rating": o.rating,
             "roles": [r.role_type for r in o.roles]} for o in query.limit(200).all()]


# ------------------------------------------------------------------
# Модерация заявок и локаций (27.3)
# ------------------------------------------------------------------

@router.get("/moderation/applications")
def moderation_queue(user: User = Depends(require_moderator), db: Session = Depends(get_db)):
    apps = db.query(Application).filter_by(status="UNDER_REVIEW").all()
    out = []
    for a in apps:
        loc = db.get(Location, a.location_id)
        org = db.get(Organization, a.created_by_org_id)
        out.append({"id": a.id, "address": loc.address, "author": org.legal_name,
                    "forecast_gmv": a.forecast_gmv, "note": a.author_note})
    return out


class LocVerifyIn(BaseModel):
    verification_status: str  # VERIFIED / NEW_DECLARED
    rights_status: str | None = None


@router.post("/locations/{location_id}/verify")
def verify_location(location_id: int, data: LocVerifyIn,
                    user: User = Depends(require_moderator), db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if not loc:
        raise HTTPException(404, "Локация не найдена")
    loc.verification_status = data.verification_status
    if data.rights_status:
        loc.rights_status = data.rights_status
    audit(db, user, "LOCATION_VERIFIED", "location", loc.id, new_value=data.verification_status)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Операции (27.5): типы станций, статусы
# ------------------------------------------------------------------

class StationTypeIn(BaseModel):
    name: str
    slots: int
    capex_cost: int
    model: str = ""
    min_forecast_gmv: int = 0
    max_forecast_gmv: int = 0
    supply_days: int = 14
    is_active: bool = True


@router.get("/station-types")
def station_types(db: Session = Depends(get_db)):
    """Публичный справочник типов (FR-901)."""
    return [{"id": t.id, "name": t.name, "slots": t.slots, "capex_cost": t.capex_cost,
             "model": t.model, "power_requirements": t.power_requirements,
             "supply_days": t.supply_days, "is_active": t.is_active}
            for t in db.query(StationType).all()]


@router.post("/station-types")
def create_station_type(data: StationTypeIn, user: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    t = StationType(**data.model_dump())
    db.add(t)
    audit(db, user, "STATION_TYPE_CREATED", "station_type", -1, new_value=data.model_dump())
    db.commit()
    return {"ok": True, "id": t.id}


@router.patch("/station-types/{type_id}")
def update_station_type(type_id: int, data: dict, user: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    t = db.get(StationType, type_id)
    if not t:
        raise HTTPException(404, "Тип не найден")
    allowed = {"name", "slots", "capex_cost", "model", "min_forecast_gmv",
               "max_forecast_gmv", "supply_days", "is_active", "power_requirements",
               "area_requirements"}
    for k, v in data.items():
        if k in allowed:
            setattr(t, k, v)
    audit(db, user, "STATION_TYPE_UPDATED", "station_type", t.id, new_value=data)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Финансовый контур администратора (27.6)
# ------------------------------------------------------------------

@router.post("/finance/verify-bank-details/{org_id}")
def verify_bank(org_id: int, user: User = Depends(require_finance), db: Session = Depends(get_db)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "Организация не найдена")
    org.bank_details_verified = True
    audit(db, user, "BANK_DETAILS_VERIFIED", "organization", org.id)
    db.commit()
    return {"ok": True}


@router.post("/finance/release-pending/{station_id}")
def release_pending(station_id: int, user: User = Depends(require_finance),
                    db: Session = Depends(get_db)):
    """Сверка периода: pending -> available (8. Финансы-3)."""
    n = finance.release_pending_for_station_period(db, station_id)
    audit(db, user, "PENDING_RELEASED", "station", station_id, new_value={"accounts": n})
    db.commit()
    return {"ok": True, "accounts_released": n}


# ------------------------------------------------------------------
# Аудит (27.8) и настройки (27.2)
# ------------------------------------------------------------------

@router.get("/audit")
def audit_logs(entity_type: str = "", entity_id: str = "", limit: int = 200,
               user: User = Depends(require_roles("admin", "auditor")),
               db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if entity_type:
        q = q.filter_by(entity_type=entity_type)
    if entity_id:
        q = q.filter_by(entity_id=entity_id)
    logs = q.order_by(AuditLog.id.desc()).limit(min(limit, 1000)).all()
    return [{"id": l.id, "actor_id": l.actor_id, "actor_type": l.actor_type,
             "action": l.action, "entity": f"{l.entity_type}:{l.entity_id}",
             "reason": l.reason, "created_at": l.created_at.isoformat(),
             "old": l.old_value, "new": l.new_value} for l in logs]


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: User = Depends(require_staff)):
    return {s.key: s.value for s in db.query(Setting).all()}


@router.put("/settings")
def put_settings(data: dict, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    for k, v in data.items():
        s = db.get(Setting, k)
        if s:
            s.value = str(v)
        else:
            db.add(Setting(key=k, value=str(v)))
        audit(db, user, "SETTING_CHANGED", "setting", k, new_value=v)
    db.commit()
    return {"ok": True}
