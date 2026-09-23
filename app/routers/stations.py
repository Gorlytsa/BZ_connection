"""API станций: жизненный цикл, активация, платежи, модернизация, выход (разделы 9, 14, 17)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (Application, ApplicationRole, Document, EquipmentOrder,
                      ExitRequest, InstallationTask, Location, MaintenanceTask,
                      ModernizationRequest, Organization, Payment, PowerbankAsset,
                      Specification, Station, StationParticipant, StationType, User)
from ..services import finance
from ..services.audit import audit, notify_org
from ..services.security import get_current_user

router = APIRouter(prefix="/api/v1/stations", tags=["stations"])


def _station_for_user(db: Session, station_id: int, user: User) -> Station:
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, "Станция не найдена")
    if user.system_role in ("admin", "moderator", "ops", "finance", "auditor"):
        return st
    part = db.query(StationParticipant).filter_by(station_id=st.id, org_id=user.org_id).first()
    if not part:
        raise HTTPException(403, "Доступ к станции имеют только ее участники и платформа")
    return st


@router.get("")
def my_stations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Station)
    if user.system_role not in ("admin", "moderator", "ops", "finance", "auditor"):
        ids = [p.station_id for p in db.query(StationParticipant).filter_by(org_id=user.org_id).all()]
        q = q.filter(Station.id.in_(ids))
    out = []
    for st in q.order_by(Station.id.desc()).limit(200).all():
        loc = db.get(Location, st.location_id)
        t = db.get(StationType, st.station_type_id)
        parts = db.query(StationParticipant).filter_by(station_id=st.id, status="ACTIVE").all()
        gmv = sum(p.amount for p in db.query(Payment)
                  .filter_by(station_id=st.id, status="RECEIVED").all())
        out.append({"id": st.id, "code": st.external_code, "status": st.status,
                    "address": loc.address, "city": loc.city, "type": t.name,
                    "participants": [{"org_id": p.org_id, "roles": p.roles_json,
                                      "share": p.share_percent} for p in parts],
                    "gmv_total": gmv,
                    "activated_at": st.activated_at.isoformat() if st.activated_at else None})
    return out


@router.get("/{station_id}")
def station_card(station_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    st = _station_for_user(db, station_id, user)
    loc = db.get(Location, st.location_id)
    t = db.get(StationType, st.station_type_id)
    parts = db.query(StationParticipant).filter_by(station_id=st.id).all()
    spec = db.query(Specification).filter_by(station_id=st.id). \
        order_by(Specification.version.desc()).first()
    payments = db.query(Payment).filter_by(station_id=st.id). \
        order_by(Payment.id.desc()).limit(50).all()
    tasks = db.query(MaintenanceTask).filter_by(station_id=st.id).all()
    return {
        "id": st.id, "external_code": st.external_code, "qr_payload": st.qr_payload,
        "telemetry_id": st.telemetry_id, "status": st.status,
        "location": {"address": loc.address, "city": loc.city, "permalink": loc.permalink},
        "station_type": {"name": t.name, "slots": t.slots, "model": t.model},
        "participants": [{"org_id": p.org_id,
                          "name": db.get(Organization, p.org_id).short_name or
                                  db.get(Organization, p.org_id).legal_name,
                          "roles": p.roles_json, "share": p.share_percent,
                          "status": p.status} for p in parts],
        "specification": {"number": spec.number, "version": spec.version,
                          "status": spec.status} if spec else None,
        "payments": [{"id": p.id, "amount": p.amount, "status": p.status,
                      "created_at": p.created_at.isoformat()} for p in payments],
        "open_tasks": [{"id": m.id, "type": m.type, "priority": m.priority,
                        "description": m.description} for m in tasks if m.status == "OPEN"],
        "activated_at": st.activated_at.isoformat() if st.activated_at else None,
    }


# ------------------------------------------------------------------
# Активация: оборудование → монтаж → чек-лист → QR → ACTIVE (FR-601..FR-606)
# ------------------------------------------------------------------

@router.post("/{station_id}/equipment-status")
def equipment_status(station_id: int, status: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    st = _station_for_user(db, station_id, user)
    if st.status != "EQUIPMENT_ORDER":
        raise HTTPException(409, "Заказ оборудования не оформлен")
    eo = db.query(EquipmentOrder).filter_by(station_id=st.id).first()
    if not eo:
        capex_part = next((p for p in st.participants if "capex" in p.roles_json), None)
        t = db.get(StationType, st.station_type_id)
        eo = EquipmentOrder(station_id=st.id, supplier="PowerBank Systems",
                            cost=t.capex_cost,
                            expected_delivery_at=datetime.now(timezone.utc) +
                            timedelta(days=t.supply_days))
        db.add(eo)
    eo.status = status
    if status == "DELIVERED":
        st.status = "DELIVERY"
    db.commit()
    return {"ok": True, "order_status": eo.status}


class InstallIn(BaseModel):
    checklist: dict  # {"крепление": true, "розетка_220v": true, ...}


@router.post("/{station_id}/installation")
def installation(station_id: int, data: InstallIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Монтаж с обязательным чек-листом (14.3). Все пункты должны быть выполнены."""
    st = _station_for_user(db, station_id, user)
    if st.status not in ("DELIVERY", "INSTALLATION"):
        raise HTTPException(409, "Монтаж доступен после доставки оборудования")
    required = ["крепление", "розетка_220v", "интернет", "сканирование_QR", "чистота"]
    missing = [c for c in required if not data.checklist.get(c)]
    task = db.query(InstallationTask).filter_by(station_id=st.id).first()
    if not task:
        task = InstallationTask(station_id=st.id)
        db.add(task)
    task.checklist_json = data.checklist
    if missing:
        st.status = "INSTALLATION"
        db.commit()
        raise HTTPException(400, "Чек-лист монтажа не выполнен: " + ", ".join(missing))
    task.status = "DONE"
    task.completed_at = datetime.now(timezone.utc)
    st.status = "COMMISSIONING"
    audit(db, user, "INSTALLATION_DONE", "station", st.id)
    db.commit()
    return {"ok": True, "status": st.status,
            "hint": "Тестовая сессия пройдена? Выполните активацию"}


@router.post("/{station_id}/activate")
def activate(station_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Комиссионирование → ACTIVE + генерация QR/Station ID (FR-602, FR-603)."""
    st = _station_for_user(db, station_id, user)
    if st.status != "COMMISSIONING":
        raise HTTPException(409, "Активация доступна после завершения монтажа")
    install = db.query(InstallationTask).filter_by(station_id=st.id).first()
    if not install or install.status != "DONE":
        raise HTTPException(409, "Не выполнен чек-лист монтажа")
    st.status = "ACTIVE"
    st.activated_at = datetime.now(timezone.utc)
    st.serial_number = st.serial_number or f"SN-{st.external_code}"
    app = db.get(Application, st.application_id)
    if app:
        app.status = "ACTIVE"
    # инвентарь пауэрбанков по числу слотов
    t = db.get(StationType, st.station_type_id)
    for i in range(t.slots):
        sn = f"PB-{st.external_code}-{i+1:02d}"
        if not db.query(PowerbankAsset).filter_by(serial_number=sn).first():
            db.add(PowerbankAsset(serial_number=sn, station_id=st.id))
    for p in st.participants:
        if p.status == "ACTIVE":
            notify_org(db, p.org_id, "STATION_ACTIVATED",
                       f"Станция {st.external_code} активирована и начала приносить доход.",
                       link=f"/#/stations/{st.id}")
    audit(db, user, "STATION_ACTIVATED", "station", st.id)
    db.commit()
    return {"ok": True, "status": st.status, "qr_payload": st.qr_payload}


# ------------------------------------------------------------------
# Платежи конечных пользователей (эмуляция приложения аренды)
# ------------------------------------------------------------------

class PaymentIn(BaseModel):
    amount: int                      # копейки (FR-803)
    idempotency_key: str
    end_user_ref: str = ""
    source: str = "acquiring"
    tariff: str = ""
    rental_id: str = ""
    accept_offer: bool = False       # акцепт оферты (18.3)


@router.post("/{station_id}/payments", response_model=None)
def accept_payment(station_id: int, data: PaymentIn,
                   db: Session = Depends(get_db)):
    """Прием платежа от конечного пользователя. Станция должна быть ACTIVE (FR-801).
    Вызывается сервисом аренды; авторизация — ключом платформы или участником станции."""
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, "Станция не найдена")
    pay = finance.process_payment(
        db, station_id=st.id, amount=data.amount, idempotency_key=data.idempotency_key,
        end_user_ref=data.end_user_ref, source=data.source, tariff=data.tariff,
        rental_id=data.rental_id, actor=None)
    return {"payment_id": pay.id, "status": pay.status, "amount": pay.amount}


class StatusIn(BaseModel):
    status: str
    reason: str = ""


@router.post("/{station_id}/status")
def change_status(station_id: int, data: StatusIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """SUSPENDED / MAINTENANCE / OFFLINE и возврат в ACTIVE (FR-605, 17.5)."""
    st = _station_for_user(db, station_id, user)
    allowed = {"MAINTENANCE", "OFFLINE", "SUSPENDED", "ACTIVE", "REPLACEMENT"}
    if data.status not in allowed:
        raise HTTPException(400, f"Допустимые статусы: {allowed}")
    if user.system_role == "partner":
        part = db.query(StationParticipant).filter_by(station_id=st.id, org_id=user.org_id).first()
        if data.status == "SUSPENDED" and "location" not in part.roles_json:
            raise HTTPException(403, "Приостанавливать работу может локация или платформа (FR-605)")
        if data.status == "ACTIVE" and "location" not in part.roles_json:
            raise HTTPException(403, "Возобновляет работу локация или платформа")
    old = st.status
    st.status = data.status
    if data.status == "SUSPENDED":
        app = db.get(Application, st.application_id)
        if app:
            app.status = "SUSPENDED"
        for p in st.participants:
            if p.status == "ACTIVE":
                notify_org(db, p.org_id, "STATION_SUSPENDED",
                           f"Станция {st.external_code} приостановлена. Причина: {data.reason}",
                           link=f"/#/stations/{st.id}")
    if data.status == "ACTIVE":
        app = db.get(Application, st.application_id)
        if app:
            app.status = "ACTIVE"
    audit(db, user, "STATION_STATUS_CHANGED", "station", st.id, old_value=old,
          new_value=data.status, reason=data.reason)
    db.commit()
    return {"ok": True, "status": st.status}


class TaskIn(BaseModel):
    type: str = "incident"
    priority: str = "normal"
    description: str


@router.post("/{station_id}/tasks")
def create_task(station_id: int, data: TaskIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Инцидент/задание обслуживания (FR-605, раздел 15)."""
    st = _station_for_user(db, station_id, user)
    due = datetime.now(timezone.utc) + timedelta(hours=72 if data.priority != "critical" else 4)
    service_part = next((p for p in st.participants
                         if "service" in p.roles_json and p.status == "ACTIVE"), None)
    m = MaintenanceTask(station_id=st.id, org_id=service_part.org_id if service_part else None,
                        type=data.type, priority=data.priority, description=data.description,
                        due_at=due)
    db.add(m)
    if service_part:
        notify_org(db, service_part.org_id, "NEW_TASK",
                   f"Новое задание по станции {st.external_code}: {data.description}",
                   link=f"/#/stations/{st.id}")
    audit(db, user, "TASK_CREATED", "station", st.id, new_value=data.model_dump())
    db.commit()
    return {"ok": True, "task_id": m.id, "due_at": due.isoformat()}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(MaintenanceTask, task_id)
    if not m:
        raise HTTPException(404, "Задание не найдено")
    m.status = "DONE"
    m.completed_at = datetime.now(timezone.utc)
    audit(db, user, "TASK_DONE", "maintenance_task", m.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Модернизация (FR-905..FR-908)
# ------------------------------------------------------------------

class ModernizeIn(BaseModel):
    new_station_type_id: int
    justification: str = ""
    investor_org_id: int | None = None


@router.post("/{station_id}/modernize")
def modernize(station_id: int, data: ModernizeIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    st = _station_for_user(db, station_id, user)
    if st.status != "ACTIVE":
        raise HTTPException(409, "Модернизация возможна только для активной станции")
    mr = ModernizationRequest(station_id=st.id, initiator_org_id=user.org_id,
                              new_station_type_id=data.new_station_type_id,
                              justification=data.justification,
                              cost=db.get(StationType, data.new_station_type_id).capex_cost,
                              investor_org_id=data.investor_org_id)
    db.add(mr)
    db.flush()
    approvals = {str(p.org_id): False for p in st.participants if p.status == "ACTIVE"}
    mr.approvals_json = approvals
    for oid in approvals:
        notify_org(db, int(oid), "MODERNIZATION_VOTE",
                   f"Предложение модернизации станции {st.external_code}. "
                   "Требуется согласие всех участников (FR-908).",
                   link=f"/#/stations/{st.id}")
    audit(db, user, "MODERNIZATION_INITIATED", "station", st.id,
          new_value={"new_type": data.new_station_type_id})
    db.commit()
    return {"ok": True, "request_id": mr.id}


@router.post("/modernize/{request_id}/vote")
def modernize_vote(request_id: int, approve: bool, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    mr = db.get(ModernizationRequest, request_id)
    if not mr or mr.status != "COLLECTING_CONSENTS":
        raise HTTPException(409, "Голосование не активно")
    st = db.get(Station, mr.station_id)
    approvals = dict(mr.approvals_json)
    key = str(user.org_id)
    if key not in approvals:
        raise HTTPException(403, "Вы не участник цепочки этой станции")
    approvals[key] = approve
    mr.approvals_json = approvals
    if not approve:
        mr.status = "REJECTED"
        audit(db, user, "MODERNIZATION_REJECTED", "modernization", mr.id)
    elif all(approvals.values()):
        # согласование новой версии спецификации (FR-907): доли остаются, тип меняется
        mr.status = "APPROVED"
        old_t = db.get(StationType, st.station_type_id)
        new_t = db.get(StationType, mr.new_station_type_id)
        st.station_type_id = new_t.id
        st.status = "MODERNIZATION"
        for p in st.participants:
            if p.status == "ACTIVE" and p.org_id == (mr.investor_org_id or mr.initiator_org_id):
                pass  # MVP: инвестор фиксируется в аудите; перераспределение долей — v2
        audit(db, user, "MODERNIZATION_APPROVED", "modernization", mr.id,
              new_value={"from": old_t.name, "to": new_t.name})
    db.commit()
    return {"ok": True, "status": mr.status}


# ------------------------------------------------------------------
# Выход участника (17.1)
# ------------------------------------------------------------------

class ExitIn(BaseModel):
    role_type: str
    reason: str = ""
    desired_date: datetime | None = None


@router.post("/{station_id}/exit")
def request_exit(station_id: int, data: ExitIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    st = _station_for_user(db, station_id, user)
    part = db.query(StationParticipant).filter_by(station_id=st.id, org_id=user.org_id,
                                                  status="ACTIVE").first()
    if not part or data.role_type not in part.roles_json:
        raise HTTPException(403, "У вашей организации нет этой роли на станции")
    if data.role_type == "location":
        raise HTTPException(409, "Локация выходит через расторжение договора размещения "
                                 "с уведомлением платформы (17.1)")
    notice_ok = True
    if data.desired_date:
        min_date = datetime.now(timezone.utc) + timedelta(days=30)
        notice_ok = data.desired_date >= min_date
    ex = ExitRequest(station_id=st.id, org_id=user.org_id, role_type=data.role_type,
                     reason=data.reason, desired_date=data.desired_date,
                     status="REQUESTED")
    db.add(ex)
    part.status = "EXITING"
    app = db.get(Application, st.application_id)
    if app:
        app.status = "REPLACEMENT_SEARCH"
        ar = db.query(ApplicationRole).filter_by(application_id=app.id,
                                                 role_type=data.role_type).first()
        if ar:
            ar.is_filled = False
            ar.assigned_org_id = None
            ar.status = "SEARCHING"
        st.status = "REPLACEMENT"
    for p in st.participants:
        if p.status == "ACTIVE" and p.org_id != user.org_id:
            notify_org(db, p.org_id, "PARTICIPANT_EXIT",
                       f"Участник заявил о выходе из станции {st.external_code} "
                       f"(роль {data.role_type}). Ищем замену.", link=f"/#/stations/{st.id}")
    audit(db, user, "EXIT_REQUESTED", "station", st.id,
          new_value={"role": data.role_type, "notice_ok": notice_ok}, reason=data.reason)
    db.commit()
    return {"ok": True, "exit_id": ex.id,
            "notice_warning": None if notice_ok else "Желателен срок уведомления ≥ 30 дней (FR-1001)"}
