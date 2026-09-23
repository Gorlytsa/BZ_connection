"""Биржа: список заявок, отклики, скоринг кандидатов, назначение (раздел 8)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (Application, ApplicationRole, Location, Organization,
                      Proposal, StationType, User)
from ..services import matching
from ..services.audit import audit, notify_org
from ..services.security import get_current_user

router = APIRouter(prefix="/api/v1/exchange", tags=["exchange"])


@router.get("/applications")
def exchange_list(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Публикации со свободными ролями. Блокированные и непроверенные не видят биржу (FR-402)."""
    org = db.get(Organization, user.org_id) if user.org_id else None
    if not org or org.blocked or org.sb_status != "APPROVED":
        raise HTTPException(403, "Биржа доступна только проверенным активным участникам (FR-402)")
    apps = db.query(Application).filter(
        Application.status.in_(["ON_EXCHANGE", "REASSEMBLY"])).all()
    out = []
    for a in apps:
        open_roles = [r.role_type for r in a.roles if not r.is_filled]
        if not open_roles:
            continue
        if org.id in {r.assigned_org_id for r in a.roles if r.assigned_org_id}:
            continue  # автор не откликается на свою заявку (FR-403)
        loc = db.get(Location, a.location_id)
        t = db.get(StationType, a.station_type_id)
        author = db.get(Organization, a.created_by_org_id)
        out.append({
            "id": a.id, "status": a.status, "address": loc.address, "city": loc.city,
            "permalink": loc.permalink, "category": loc.category,
            "station_type": t.name, "slots": t.slots, "capex_cost": t.capex_cost,
            "forecast_gmv": a.forecast_gmv, "open_roles": open_roles,
            "author_rating": author.rating,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        })
    return sorted(out, key=lambda x: -(x["forecast_gmv"] or 0))


class ProposalIn(BaseModel):
    role_type: str
    commercial_offer: str = ""
    comment: str = ""
    ready_to_start: str = ""


@router.post("/applications/{app_id}/proposals")
def submit_proposal(app_id: int, data: ProposalIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    org = db.get(Organization, user.org_id) if user.org_id else None
    if not org or org.blocked or org.sb_status != "APPROVED":
        raise HTTPException(403, "Откликаться могут только проверенные участники (FR-402)")
    app = db.get(Application, app_id)
    if not app or app.status not in ("ON_EXCHANGE", "REASSEMBLY"):
        raise HTTPException(409, "Заявка не принимает отклики")
    if app.created_by_org_id == org.id:
        raise HTTPException(403, "Нельзя откликаться на собственную заявку (FR-403)")
    ar = db.query(ApplicationRole).filter_by(application_id=app.id,
                                             role_type=data.role_type).first()
    if not ar or ar.is_filled:
        raise HTTPException(409, "Роль уже занята или отсутствует")
    has_role = any(r.role_type == data.role_type and r.is_active for r in org.roles)
    if not has_role:
        raise HTTPException(403, f"У организации нет разрешенной роли {data.role_type} (FR-403)")
    exists = db.query(Proposal).filter_by(application_id=app.id, org_id=org.id,
                                          role_type=data.role_type).first()
    if exists and exists.status in ("SUBMITTED", "UNDER_REVIEW", "RESERVED"):
        raise HTTPException(409, "Отклик уже отправлен (можно отозвать и отправить снова)")
    score = matching.candidate_score(db, org, db.get(Location, app.location_id))
    p = Proposal(application_id=app.id, org_id=org.id, role_type=data.role_type,
                 status="SUBMITTED", commercial_offer=data.commercial_offer,
                 comment=data.comment, ready_to_start=data.ready_to_start, score=score)
    db.add(p)
    notify_org(db, app.created_by_org_id, "CANDIDATE_APPEARED",
               f"На заявку #{app.id} (роль {data.role_type}) появился кандидат: "
               f"{org.short_name or org.legal_name}, рейтинг {org.rating}.",
               link=f"/#/applications/{app.id}")
    audit(db, user, "PROPOSAL_SUBMITTED", "proposal", -1,
          new_value={"app": app.id, "org": org.id, "role": data.role_type, "score": score})
    db.commit()
    return {"ok": True, "score": score}


@router.post("/proposals/{proposal_id}/withdraw")
def withdraw(proposal_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Proposal, proposal_id)
    if not p or p.org_id != user.org_id:
        raise HTTPException(404, "Отклик не найден")
    if p.status not in ("SUBMITTED", "UNDER_REVIEW"):
        raise HTTPException(409, "Этот отклик уже обработан")
    old = p.status
    p.status = "WITHDRAWN"
    audit(db, user, "PROPOSAL_WITHDRAWN", "proposal", p.id, old_value=old)
    db.commit()
    return {"ok": True}


@router.get("/applications/{app_id}/candidates")
def candidates(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Кандидаты с ранжированием по скорингу (FR-502). Видно автору заявки и платформе."""
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Заявка не найдена")
    if user.system_role not in ("admin", "moderator") and app.created_by_org_id != user.org_id:
        raise HTTPException(403, "Список кандидатов доступен автору заявки и платформе")
    ps = db.query(Proposal).filter_by(application_id=app.id). \
        filter(Proposal.status.in_(["SUBMITTED", "UNDER_REVIEW", "APPROVED", "RESERVED"])).all()
    out = []
    for p in ps:
        org = db.get(Organization, p.org_id)
        out.append({"proposal_id": p.id, "org_id": org.id,
                    "name": org.short_name or org.legal_name, "rating": org.rating,
                    "role_type": p.role_type, "status": p.status, "score": p.score,
                    "commercial_offer": p.commercial_offer, "comment": p.comment,
                    "ready_to_start": p.ready_to_start})
    return sorted(out, key=lambda x: -(x["score"] or 0))


class AssignIn(BaseModel):
    role_type: str
    org_id: int
    reason: str = ""


@router.post("/applications/{app_id}/assign")
def assign(app_id: int, data: AssignIn, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Назначение участника на роль — платформа или автор заявки (FR-504/FR-505)."""
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Заявка не найдена")
    is_staff = user.system_role in ("admin", "moderator")
    if not is_staff and app.created_by_org_id != user.org_id:
        raise HTTPException(403, "Назначать может автор заявки или платформа")
    if app.status not in ("ON_EXCHANGE", "REASSEMBLY"):
        raise HTTPException(409, f"Назначение недоступно в статусе {app.status}")
    matching.assign_role(db, app, data.role_type, data.org_id,
                         "platform" if is_staff else "self", user, data.reason)
    if matching.chain_is_complete(db, app):
        notify_org(db, app.created_by_org_id, "CHAIN_READY",
                   f"Все роли заявки #{app.id} заполнены. Платформа собирает цепочку.",
                   link=f"/#/applications/{app.id}")
    return {"ok": True, "chain_complete": matching.chain_is_complete(db, app)}


@router.post("/applications/{app_id}/assemble")
def assemble(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Платформа утверждает состав и собирает цепочку → Station ID + документы (FR-501, FR-601)."""
    from .admin import _require_moderator
    _require_moderator(user)
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Заявка не найдена")
    station = matching.assemble_station(db, app, user)
    # Генерация пакета документов по правилам сценариев (FR-709/FR-710)
    from ..services import documents
    from ..models import Document
    pkg = documents.generate_package(db, station, user)
    documents.send_package(db, pkg, user)
    app.status = "SIGNING"
    db.commit()
    return {"station_id": station.id, "external_code": station.external_code,
            "package_id": pkg.id,
            "documents": [d.id for d in db.query(Document).filter_by(package_id=pkg.id).all()]}
