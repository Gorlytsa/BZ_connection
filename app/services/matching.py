"""Сборка цепочки, матчинг и Station ID (разделы 8–9)."""
import random

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (Application, ApplicationRole, Location, Organization,
                      Proposal, Station, StationParticipant, User)
from .audit import audit, notify_org
from .documents import compute_shares


def station_code_exists(db: Session, code: str) -> bool:
    return db.query(Station).filter_by(external_code=code).one_or_none() is not None


def make_station_code(db: Session) -> str:
    rng = random.Random()
    while True:
        code = f"ST-{rng.randint(1000, 9999)}"
        if not station_code_exists(db, code):
            return code


def candidate_score(db: Session, org: Organization, location: Location) -> float:
    """Автоматический скоринг (FR-502), MVP-факторы."""
    score = org.rating * 10.0
    active_stations = db.query(StationParticipant).filter_by(org_id=org.id, status="ACTIVE").count()
    score -= active_stations * 2.0  # операционная загрузка
    acc_debt = False
    from .finance import partner_account
    acc = partner_account(db, org.id)
    if acc.balance_available < 0:
        acc_debt = True
        score -= 50.0
    if location.city and org.roles:
        regions = " ".join(r.regions for r in org.roles)
        if location.city.lower() in regions.lower() or not regions.strip():
            score += 15.0
    return round(score, 2)


def chain_is_complete(db: Session, app: Application) -> bool:
    roles = db.query(ApplicationRole).filter_by(application_id=app.id).all()
    return all(r.is_filled for r in roles) and len(roles) == 3


def assemble_station(db: Session, app: Application, actor: User | None = None) -> Station:
    """Переход ON_EXCHANGE/REASSEMBLY → ASSEMBLY: создание станции и участников (FR-601)."""
    if app.status not in ("ON_EXCHANGE", "REASSEMBLY"):
        raise HTTPException(409, f"Нельзя собирать цепочку из статуса {app.status}")
    roles = db.query(ApplicationRole).filter_by(application_id=app.id).all()
    role_map = {r.role_type: r.assigned_org_id for r in roles if r.is_filled}
    if len(role_map) < 3:
        raise HTTPException(409, "Цепочка не собрана: есть незаполненные роли (FR-501)")
    # ограничения FR-503: никто из назначенных не заблокирован и прошел СБ
    for oid in set(role_map.values()):
        org = db.get(Organization, oid)
        if org.blocked or org.sb_status != "APPROVED":
            raise HTTPException(409, f"Участник {org.legal_name} не может входить в цепочку (FR-503)")
    loc = db.get(Location, app.location_id)
    existing = db.query(Station).filter_by(location_id=loc.id). \
        filter(Station.status.notin_(["ARCHIVED", "DECOMMISSIONED"])).count()
    if existing and not loc.allow_multiple_stations:
        raise HTTPException(409, "На этой локации уже есть станция; несколько станций "
                                 "на одном пермалинке только с разрешения платформы (4.1.6)")

    station = Station(external_code=make_station_code(db), application_id=app.id,
                      location_id=loc.id, station_type_id=app.station_type_id,
                      status="PLANNED")
    db.add(station)
    db.flush()
    station.qr_payload = f"https://ps-ex.app/s/{station.external_code}"
    station.telemetry_id = f"TLM-{station.external_code}"

    shares = compute_shares(role_map)
    for oid, info in shares.items():
        db.add(StationParticipant(station_id=station.id, org_id=oid,
                                  roles_json=info["roles"], share_percent=info["share"],
                                  status="ACTIVE"))
    app.status = "ASSEMBLY"
    audit(db, actor, "CHAIN_ASSEMBLED", "station", station.id,
          new_value={"code": station.external_code, "roles": role_map})
    for oid in shares:
        notify_org(db, oid, "CHAIN_ASSEMBLED",
                   f"По вашей заявке #{app.id} собрана цепочка, станция {station.external_code}. "
                   "Ожидайте пакет документов на подпись.", link=f"/#/stations/{station.id}")
    db.commit()
    return station


def assign_role(db: Session, app: Application, role_type: str, org_id: int,
                assigned_by: str, actor: User | None, reason: str = ""):
    """Назначение роли (FR-504 ручное решение / отклик approved)."""
    ar = db.query(ApplicationRole).filter_by(application_id=app.id, role_type=role_type).first()
    if not ar:
        raise HTTPException(404, "Роль не найдена в заявке")
    if ar.is_filled and ar.assigned_org_id == org_id:
        raise HTTPException(409, "Роль уже закреплена за этой организацией")
    org = db.get(Organization, org_id)
    if not org or org.blocked or org.sb_status != "APPROVED":
        raise HTTPException(409, "Нельзя назначать участника без пройденной СБ или блокированного (FR-503)")
    has_role = any(r.role_type == role_type and r.is_active for r in org.roles)
    if not has_role:
        raise HTTPException(409, f"У организации нет разрешения на роль {role_type} (FR-403)")
    old = {"org": ar.assigned_org_id, "filled": ar.is_filled}
    ar.is_filled = True
    ar.assigned_org_id = org_id
    ar.assigned_by = assigned_by
    ar.status = "FILLED"
    # отклоняем прочие отклики на роль
    others = db.query(Proposal).filter(Proposal.application_id == app.id,
                                       Proposal.role_type == role_type,
                                       Proposal.org_id != org_id,
                                       Proposal.status.in_(["SUBMITTED", "UNDER_REVIEW", "RESERVED"])).all()
    for p in others:
        p.status = "EXPIRED"
        notify_org(db, p.org_id, "PROPOSAL_NOT_SELECTED",
                   f"На заявку #{app.id} по роли {role_type} выбран другой кандидат.",
                   link="/#/exchange")
    audit(db, actor, "ROLE_ASSIGNED", "application_role", ar.id, old_value=old,
          new_value={"org_id": org_id}, reason=reason)
    db.commit()
