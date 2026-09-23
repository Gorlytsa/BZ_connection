"""Финансовый API: балансы, выплаты, возвраты, агентские отчеты (FR-806..FR-810)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (LedgerTransaction, Organization, Payment, Payout, Station,
                      StationParticipant, User)
from ..services import finance
from ..services.audit import audit
from ..services.security import get_current_user, require_finance, require_staff

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


@router.get("/balance")
def balance(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Баланс участника (FR-806): доступно / ожидает / заблокировано / долг."""
    if not user.org_id:
        raise HTTPException(400, "Нет организации")
    acc = finance.partner_account(db, user.org_id)
    db.commit()
    return {"available": acc.balance_available, "pending": acc.balance_pending,
            "blocked": acc.balance_blocked, "debt": min(acc.balance_available, 0)}


@router.get("/transactions")
def transactions(limit: int = 100, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    q = db.query(LedgerTransaction)
    if user.system_role not in ("finance", "admin", "auditor"):
        if not user.org_id:
            return []
        acc = finance.partner_account(db, user.org_id)
        db.commit()
        q = q.filter((LedgerTransaction.credit_account_id == acc.id) |
                     (LedgerTransaction.debit_account_id == acc.id))
    txs = q.order_by(LedgerTransaction.id.desc()).limit(min(limit, 500)).all()
    return [{"id": t.id, "op": t.operation_type, "amount": t.amount,
             "debit": t.debit_account_id, "credit": t.credit_account_id,
             "station_id": t.station_id, "purpose": t.purpose,
             "created_at": t.created_at.isoformat()} for t in txs]


@router.post("/payouts")
def create_payout(period: str, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Финансист формирует выплату по реестру (FR-809)."""
    org_ids = {p.org_id for p in db.query(StationParticipant).filter_by(status="ACTIVE").all()}
    created = []
    for oid in sorted(org_ids):
        acc = finance.partner_account(db, oid)
        if acc.balance_available > 0:
            try:
                po = finance.create_payout(db, oid, period, user)
                created.append(po.id)
            except HTTPException:
                continue  # нет реквизитов/заблокирован — payout ON_HOLD решается вручную
    return {"ok": True, "payout_ids": created}


class PayoutApproveIn(BaseModel):
    pass


@router.post("/payouts/{payout_id}/approve")
def approve_payout_ep(payout_id: int, user: User = Depends(require_staff),
                      db: Session = Depends(get_db)):
    po = finance.approve_payout(db, payout_id, user)
    return {"ok": True, "status": po.status}


@router.post("/payouts/{payout_id}/sent")
def sent_payout(payout_id: int, bank_ref: str = "", user: User = Depends(require_finance),
                db: Session = Depends(get_db)):
    po = finance.mark_payout_sent(db, payout_id, bank_ref or f"BANK-{payout_id}", user)
    return {"ok": True, "status": po.status}


@router.post("/payouts/{payout_id}/paid")
def paid_payout(payout_id: int, user: User = Depends(require_finance),
                db: Session = Depends(get_db)):
    po = finance.mark_payout_paid(db, payout_id, user)
    return {"ok": True, "status": po.status}


@router.get("/payouts")
def payouts(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    out = []
    for po in db.query(Payout).order_by(Payout.id.desc()).limit(200).all():
        org = db.get(Organization, po.org_id)
        out.append({"id": po.id, "org": org.legal_name, "period": po.period,
                    "amount": po.amount, "status": po.status,
                    "created_by": po.created_by, "approved_by": po.approved_by})
    return out


class RefundIn(BaseModel):
    reason: str
    chargeback: bool = False


@router.post("/payments/{payment_id}/refund")
def refund(payment_id: int, data: RefundIn, user: User = Depends(require_staff),
           db: Session = Depends(get_db)):
    """Возврат/чарджбек со сторно распределения (FR-807, FR-808)."""
    pay = finance.refund_payment(db, payment_id, data.reason, user, data.chargeback)
    return {"ok": True, "status": pay.status}


class AdjustIn(BaseModel):
    org_id: int
    amount: int   # копейки; + доначисление, - удержание
    reason: str


@router.post("/adjustments")
def adjustment(data: AdjustIn, user: User = Depends(require_finance),
               db: Session = Depends(get_db)):
    tx = finance.manual_adjustment(db, data.org_id, data.amount, data.reason, user)
    return {"ok": True, "tx_id": tx.id}


@router.get("/agent-report")
def agent_report(org_id: int | None = None, period: str = "",
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Агентский отчет за месяц (FR-810). Участник видит свой, финансист — любой."""
    target = org_id or user.org_id
    if not target:
        raise HTTPException(400, "Укажите период и организацию")
    if user.system_role not in ("finance", "admin", "auditor") and target != user.org_id:
        raise HTTPException(403, "Отчет доступен только вашему участнику")
    if not period:
        from datetime import datetime, timezone
        period = datetime.now(timezone.utc).strftime("%Y-%m")
    data = finance.build_agent_report_data(db, target, period)
    data["money"] = {k: finance.money_format(v) for k, v in
                     [("accrued", data["accrued"]), ("paid", data["paid"]),
                      ("to_pay", data["to_pay"]), ("available", data["balance_available"])]}
    return data


@router.get("/stations/{station_id}/revenue")
def station_revenue(station_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """GMV станции и распределение (участники видят только свои доли, 26.3)."""
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, "Станция не найдена")
    parts = db.query(StationParticipant).filter_by(station_id=st.id, status="ACTIVE").all()
    if user.system_role not in ("finance", "admin", "auditor", "ops"):
        my = [p for p in parts if p.org_id == user.org_id]
        if not my:
            raise HTTPException(403, "Вы не участник станции")
    payments = db.query(Payment).filter_by(station_id=st.id).all()
    gmv = sum(p.amount for p in payments if p.status == "RECEIVED")
    refunds = sum(p.amount for p in payments if p.status in ("REFUNDED", "CHARGEBACK"))
    shares = {}
    for p in parts:
        txs = db.query(LedgerTransaction).filter_by(station_id=st.id,
                                                    credit_account_id=finance.partner_account(db, p.org_id).id,
                                                    operation_type="PARTNER_SHARE").all()
        shares[p.org_id] = sum(t.amount for t in txs)
    db.commit()
    return {"gmv": gmv, "refunds": refunds, "net": gmv - refunds,
            "platform_fee": gmv * 35 // 100, "shares_by_org": shares}
