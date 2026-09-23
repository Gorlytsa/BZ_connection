"""API заявок: мастер 6 шагов, экономика-шаг, публикация (FR-301..FR-310)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (Application, ApplicationRole, Location, Organization,
                      StationType, User)
from ..services import economics
from ..services.audit import audit, notify_org
from ..services.security import get_current_user

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def _sb_ok(user: User, db: Session):
    org = db.get(Organization, user.org_id) if user.org_id else None
    if not org or org.sb_status != "APPROVED" or org.blocked:
        raise HTTPException(403, "Создание заявок доступно только организациям, "
                                 "прошедшим проверку СБ (FR-102)")
    return org


class AppCreate(BaseModel):
    location_id: int
    roles: list[str]  # роли, которые автор берет на себя: subset of location/capex/service
    working_hours: str = ""
    traffic: str = "medium"
    venue_category: str = "other"
    has_socket: bool = True
    has_internet: bool = True
    mount_possible: bool = True
    author_note: str = ""


@router.post("")
def create_app(data: AppCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = _sb_ok(user, db)
    loc = db.get(Location, data.location_id)
    if not loc:
        raise HTTPException(404, "Локация не найдена")
    if loc.verification_status == "MODERATION":
        raise HTTPException(409, "Локация на модерации — создание заявки ограничено (FR-203)")
    roles = set(data.roles)
    if not roles or not roles.issubset({"location", "capex", "service"}):
        raise HTTPException(400, "Укажите 1–3 корректные роли автора")
    allowed = {r.role_type for r in org.roles if r.is_active}
    if not roles.issubset(allowed):
        raise HTTPException(400, f"У организации нет разрешений на роли {roles - allowed}")
    first_type = db.query(StationType).filter_by(is_active=True).order_by(StationType.id).first()
    app = Application(location_id=loc.id, created_by_org_id=org.id, status="DRAFT",
                      station_type_id=first_type.id, venue_category=data.venue_category,
                      traffic=data.traffic, working_hours=data.working_hours,
                      has_socket=data.has_socket, has_internet=data.has_internet,
                      mount_possible=data.mount_possible, author_note=data.author_note)
    db.add(app)
    db.flush()
    for r in ("location", "capex", "service"):
        filled = r in roles
        db.add(ApplicationRole(application_id=app.id, role_type=r, is_filled=filled,
                               assigned_org_id=org.id if filled else None,
                               assigned_by="self" if filled else "",
                               status="FILLED" if filled else "OPEN"))
    audit(db, user, "APPLICATION_CREATED", "application", app.id, new_value={"roles": list(roles)})
    db.commit()
    return {"id": app.id, "status": app.status,
            "hint": "Шаг 5–6: проверьте экономику и опубликуйте заявку"}


class TypeChange(BaseModel):
    station_type_id: int


@router.post("/{app_id}/economics")
def app_economics(app_id: int, data: TypeChange | None = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Шаг 5 мастера: выбор типа станции + прогноз экономики (FR-305..FR-309)."""
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Заявка не найдена")
    if data and data.station_type_id:
        if app.status not in ("DRAFT", "UNDER_REVIEW", "ON_EXCHANGE"):
            raise HTTPException(409, "Тип станции можно менять до публикации; "
                                     "после сборки — только модернизация (FR-905..FR-908)")
        app.station_type_id = data.station_type_id
        db.commit()
    types = db.query(StationType).all()
    loc = db.get(Location, app.location_id)
    result = economics.economics(types, app.station_type_id, app.traffic,
                                 loc.category or app.venue_category)
    if "error" not in result:
        app.forecast_gmv = result["forecast_gmv"]
        rec = result["recommendation"]
        app.recommendation = (f"Рекомендация платформы: тип #{rec['station_type_id']} "
                              f"({rec['slots']} слотов). {rec['reason']}")
        warnings = list(app.warnings_json or [])
        for w in result["warnings"]:
            if w not in warnings:
                warnings.append(w)
        app.warnings_json = warnings
        db.commit()
    return result


@router.get("")
def list_apps(mine: bool = False, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    q = db.query(Application)
    if mine:
        q = q.filter_by(created_by_org_id=user.org_id)
    out = []
    for a in q.order_by(Application.id.desc()).limit(100).all():
        loc = db.get(Location, a.location_id)
        t = db.get(StationType, a.station_type_id)
        out.append({"id": a.id, "status": a.status, "address": loc.address,
                    "city": loc.city, "permalink": loc.permalink,
                    "station_type": t.name, "slots": t.slots,
                    "forecast_gmv": a.forecast_gmv,
                    "roles": [{"role": r.role_type, "filled": r.is_filled,
                               "assigned_org_id": r.assigned_org_id} for r in a.roles],
                    "published_at": a.published_at.isoformat() if a.published_at else None,
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None})
    return out


@router.get("/{app_id}")
def get_app(app_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Заявка не найдена")
    loc = db.get(Location, app.location_id)
    t = db.get(StationType, app.station_type_id)
    org = db.get(Organization, app.created_by_org_id)
    from ..models import Proposal
    proposals = db.query(Proposal).filter_by(application_id=app.id).all()
    return {
        "id": app.id, "status": app.status,
        "author": {"id": org.id, "name": org.short_name or org.legal_name,
                   "rating": org.rating},
        "location": {"id": loc.id, "address": loc.address, "city": loc.city,
                     "permalink": loc.permalink, "category": loc.category},
        "station_type": {"id": t.id, "name": t.name, "slots": t.slots,
                         "capex_cost": t.capex_cost},
        "working_hours": app.working_hours, "has_socket": app.has_socket,
        "has_internet": app.has_internet, "mount_possible": app.mount_possible,
        "traffic": app.traffic, "forecast_gmv": app.forecast_gmv,
        "recommendation": app.recommendation, "warnings": app.warnings_json,
        "author_note": app.author_note,
        "roles": [{"role": r.role_type, "filled": r.is_filled,
                   "assigned_org_id": r.assigned_org_id, "status": r.status}
                  for r in app.roles],
        "proposals_count": len(proposals),
        "published_at": app.published_at.isoformat() if app.published_at else None,
        "expires_at": app.expires_at.isoformat() if app.expires_at else None,
    }


@router.post("/{app_id}/publish")
def publish(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Публикация: DRAFT → UNDER_REVIEW → (модератор) ON_EXCHANGE (FR-310)."""
    _sb_ok(user, db)
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Заявка не найдена")
    if app.status != "DRAFT":
        raise HTTPException(409, f"Нельзя опубликовать заявку со статусом {app.status}")
    loc = db.get(Location, app.location_id)
    missing = []
    if not app.working_hours:
        missing.append("часы работы точки")
    if not app.has_socket:
        missing.append("нет розетки 220V — согласуйте электромонтаж")
    if not app.has_internet:
        missing.append("нет интернета — требуется SIM-роутер")
    if not app.mount_possible:
        missing.append("монтаж к стене/стойке невозможен")
    if loc.rights_status == "NONE":
        missing.append("не задекларированы права на площадь (FR-204)")
    if missing:
        raise HTTPException(400, "Не хватает данных для публикации: " + "; ".join(missing))
    app.status = "UNDER_REVIEW"
    audit(db, user, "APPLICATION_SUBMITTED", "application", app.id)
    db.commit()
    return {"ok": True, "status": app.status,
            "hint": "Заявка отправлена на модерацию платформы"}


class RejectIn(BaseModel):
    reason: str


@router.post("/{app_id}/approve")
def approve(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Модератор одобряет → ON_EXCHANGE (FR-310)."""
    from .admin import _require_moderator
    _require_moderator(user)
    app = db.get(Application, app_id)
    if not app or app.status != "UNDER_REVIEW":
        raise HTTPException(409, "Заявка не на модерации")
    app.status = "ON_EXCHANGE"
    app.published_at = datetime.now(timezone.utc)
    app.expires_at = app.published_at + timedelta(days=14)
    org = db.get(Organization, app.created_by_org_id)
    notify_org(db, org.id, "APPLICATION_PUBLISHED",
               f"Заявка #{app.id} опубликована на бирже.", link="/#/exchange")
    audit(db, user, "APPLICATION_APPROVED", "application", app.id)
    db.commit()
    return {"ok": True, "status": app.status}


@router.post("/{app_id}/reject")
def reject(app_id: int, data: RejectIn, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    from .admin import _require_moderator
    _require_moderator(user)
    app = db.get(Application, app_id)
    if not app or app.status != "UNDER_REVIEW":
        raise HTTPException(409, "Заявка не на модерации")
    app.status = "REJECTED"
    org = db.get(Organization, app.created_by_org_id)
    notify_org(db, org.id, "APPLICATION_REJECTED",
               f"Заявка #{app.id} отклонена. Причина: {data.reason}",
               link=f"/#/applications/{app.id}")
    audit(db, user, "APPLICATION_REJECTED", "application", app.id, reason=data.reason)
    db.commit()
    return {"ok": True}


@router.post("/{app_id}/extend")
def extend(app_id: int, days: int = 14, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Продление срока ответа (FR-310)."""
    app = db.get(Application, app_id)
    if not app or app.status not in ("ON_EXCHANGE", "REASSEMBLY"):
        raise HTTPException(409, "Продлить можно только активную заявку на бирже")
    base = app.expires_at or datetime.now(timezone.utc)
    app.expires_at = base + timedelta(days=days)
    audit(db, user, "APPLICATION_EXTENDED", "application", app.id, new_value={"days": days})
    db.commit()
    return {"ok": True, "expires_at": app.expires_at.isoformat()}
