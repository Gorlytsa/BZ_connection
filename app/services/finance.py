"""Финансовое ядро MVP: ledger, распределение долей, возвраты, выплаты, отчеты.

Соответствие требованиям:
- FR-802: распределение 35/20/25/20, объединенные роли получают суммарную долю;
- FR-803: все суммы в копейках (int), float запрещен;
- FR-804: метод наибольшего остатка, итог всегда равен платежу, корректировка логируется;
- FR-805: неизменяемый ledger двойной записью, исправления — сторно;
- FR-806: балансы участников по счетам;
- FR-807: возврат сторнирует доли, возможен отрицательный баланс (долг);
- FR-809: выплаты реестром с правилом «четыре глаза»;
- FR-810: агентские отчеты.
"""
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (LedgerAccount, LedgerTransaction, Organization, Payment,
                      Payout, Station, StationParticipant, User)
from .audit import audit, notify_org

PLATFORM_ORG = 0  # псевдо-организация платформы


def money_format(kopecks: int) -> str:
    return f"{kopecks // 100} ₽ {kopecks % 100:02d} к."


# --------------------------------------------------------------------------
# Счета
# --------------------------------------------------------------------------

def get_account(db: Session, code: str, org_id: int | None, acc_type: str) -> LedgerAccount:
    acc = db.query(LedgerAccount).filter_by(code=code).one_or_none()
    if not acc:
        acc = LedgerAccount(code=code, org_id=org_id, account_type=acc_type)
        db.add(acc)
        db.flush()
    return acc


def partner_account(db: Session, org_id: int) -> LedgerAccount:
    return get_account(db, f"PARTICIPANT:{org_id}", org_id, "partner")


TRANSIT = "ACQUIRER_TRANSIT"
FEE = "PLATFORM_FEE"


def post_entry(db: Session, op: str, debit: LedgerAccount, credit: LedgerAccount,
               amount: int, payment_id=None, station_id=None, payout_id=None,
               purpose="", metadata=None, reverses_id=None) -> LedgerTransaction:
    """Проводка двойной записью. Изменяет балансы и пишет неизменяемую запись."""
    if amount <= 0 and op != "ADJUSTMENT":
        raise HTTPException(400, "Сумма проводки должна быть положительной")
    debit.balance_available -= amount
    credit.balance_available += amount
    tx = LedgerTransaction(
        operation_type=op, payment_id=payment_id, station_id=station_id, payout_id=payout_id,
        debit_account_id=debit.id, credit_account_id=credit.id, amount=amount,
        purpose=purpose, metadata_json=metadata or {}, reverses_id=reverses_id,
    )
    db.add(tx)
    return tx


# --------------------------------------------------------------------------
# FR-804: распределение методом наибольшего остатка
# --------------------------------------------------------------------------

def largest_remainder_allocation(total: int, weights_bps: dict[str, int]) -> dict[str, int]:
    """total — копейки; weights_bps — базисные пункты (1% = 100 bps), сумма = 10000.
    Возвращает целочисленное распределение, сумма которого точно равна total."""
    assert sum(weights_bps.values()) == 10000, "Доли должны составлять 100%"
    base = {}
    remainders = []
    allocated = 0
    for key, w in weights_bps.items():
        raw = total * w
        q, r = divmod(raw, 10000)
        base[key] = q
        remainders.append((r, key))
        allocated += q
    # раздаём остаток по наибольшему остатку (детерминированный порядок)
    remainders.sort(key=lambda x: (-x[0], x[1]))
    diff = total - allocated
    for i in range(diff):
        base[remainders[i % len(remainders)][1]] += 1
    return base


def station_share_weights(db: Session, station_id: int) -> dict[str, int]:
    """Веса распределения для станции: платформа + участники (объединенные доли)."""
    parts = db.query(StationParticipant).filter_by(station_id=station_id, status="ACTIVE").all()
    platform = settings.share_platform
    used = sum(p.share_percent for p in parts)
    if used + platform != 100:
        raise HTTPException(409, f"Доли станции не дают 100% (сумма {used + platform}%)")
    weights = {"platform": platform * 100}
    for p in parts:
        weights[f"org:{p.org_id}"] = p.share_percent * 100
    return weights


# --------------------------------------------------------------------------
# FR-801: прием платежа и распределение
# --------------------------------------------------------------------------

def process_payment(db: Session, *, station_id: int, amount: int, idempotency_key: str,
                    end_user_ref="", source="acquiring", tariff="", rental_id="",
                    actor: User | None = None) -> Payment:
    existing = db.query(Payment).filter_by(idempotency_key=idempotency_key).one_or_none()
    if existing:
        return existing  # идемпотентность (20.4)
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(404, "Станция не найдена")
    if station.status != "ACTIVE":
        raise HTTPException(409, "Платежи принимаются только активной станцией (FR-801)")
    if amount <= 0:
        raise HTTPException(400, "Сумма платежа должна быть больше нуля")

    pay = Payment(station_id=station_id, end_user_ref=end_user_ref, amount=amount,
                  status="RECEIVED", acquiring_ref=f"ACQ-{idempotency_key[:16]}",
                  fiscal_ref=f"FISC-{idempotency_key[:12]}", tariff=tariff,
                  rental_id=rental_id, source=source, idempotency_key=idempotency_key)
    db.add(pay)
    db.flush()

    transit = get_account(db, TRANSIT, None, "transit")
    external = get_account(db, "EXTERNAL_ACQUIRER", None, "external")
    fee_acc = get_account(db, FEE, PLATFORM_ORG, "fee")

    # 1. Платеж поступил от внешнего эквайринга на транзитный счет платформы
    post_entry(db, "PAYMENT_RECEIVED", external, transit, amount, payment_id=pay.id,
               station_id=station_id, purpose=f"Прием платежа {money_format(amount)}",
               metadata={"source": source})

    weights = station_share_weights(db, station_id)
    alloc = largest_remainder_allocation(amount, weights)

    # 2. Агентское вознаграждение платформы (FR-802)
    fee_amt = alloc["platform"]
    post_entry(db, "PLATFORM_FEE", transit, fee_acc, fee_amt, payment_id=pay.id,
               station_id=station_id, purpose=f"Агентское вознаграждение {settings.share_platform}%",
               metadata={"basis_points": weights["platform"]})

    # 3. Доли участникам (pending: доступны для выплаты после сверки)
    for key, amt in alloc.items():
        if key == "platform" or amt == 0:
            continue
        org_id = int(key.split(":")[1])
        acc = partner_account(db, org_id)
        post_entry(db, "PARTNER_SHARE", transit, acc, amt, payment_id=pay.id,
                   station_id=station_id,
                   purpose=f"Доля участника по спецификации станции {station.external_code}")
        acc.balance_pending += amt
        acc.balance_available -= amt  # начисление сначала ожидает (pending)

    rounding_note = {k: v for k, v in alloc.items()}
    audit(db, actor, "PAYMENT_PROCESSED", "payment", pay.id,
          new_value={"amount": amount, "allocation": rounding_note},
          actor_type="system")
    db.commit()
    return pay


def release_pending_for_station_period(db: Session, station_id: int):
    """Модель сверки: pending -> available (MVP: выгружается вручную финансистом)."""
    txs = db.query(LedgerTransaction).filter_by(station_id=station_id,
                                                operation_type="PARTNER_SHARE").all()
    accs: dict[int, LedgerAccount] = {}
    for tx in txs:
        acc = db.get(LedgerAccount, tx.credit_account_id)
        if acc.account_type == "partner" and acc.id not in accs:
            accs[acc.id] = acc
    # в MVP released считаются все shares старше выплат — упрощаем: освобождаем все
    for acc in accs.values():
        if acc.balance_pending > 0:
            acc.balance_available += acc.balance_pending
            acc.balance_pending = 0
    return len(accs)


# --------------------------------------------------------------------------
# FR-807 / FR-808: возвраты и чарджбеки (сторно)
# --------------------------------------------------------------------------

def _reverse_allocation(db: Session, payment: Payment, op: str, actor: User | None, reason: str):
    """Сторно исходного распределения пропорционально (FR-807). Балансы меняются
    симметрично проводкам постатей post_entry, поэтому отдельная корректировка не нужна.
    Если у участника не хватает средств — баланс уходит в минус (долг),
    который гасится будущими начислениями."""
    shares = db.query(LedgerTransaction).filter_by(payment_id=payment.id). \
        filter(LedgerTransaction.operation_type.in_(["PLATFORM_FEE", "PARTNER_SHARE"])).all()
    for tx in shares:
        debit = db.get(LedgerAccount, tx.credit_account_id)   # обратная проводка
        credit = db.get(LedgerAccount, tx.debit_account_id)
        post_entry(db, op, debit, credit, tx.amount,
                   payment_id=payment.id, station_id=payment.station_id,
                   purpose=f"Сторно «{tx.purpose}». Причина: {reason}",
                   reverses_id=tx.id)
    # деньги возвращаются конечному пользователю с транзитного счета
    transit = get_account(db, TRANSIT, None, "transit")
    external = get_account(db, "EXTERNAL_ACQUIRER", None, "external")
    post_entry(db, op, transit, external, payment.amount,
               payment_id=payment.id, station_id=payment.station_id,
               purpose=f"Возврат пользователю {money_format(payment.amount)}. {reason}")
    audit(db, actor, op, "payment", payment.id, old_value=payment.status,
          new_value="REFUNDED" if op == "REFUND" else "CHARGEBACK", reason=reason)


def refund_payment(db: Session, payment_id: int, reason: str, actor: User | None,
                   chargeback: bool = False):
    pay = db.get(Payment, payment_id)
    if not pay:
        raise HTTPException(404, "Платеж не найден")
    if pay.status != "RECEIVED":
        raise HTTPException(409, "Сторнировать можно только полученный платеж")
    _reverse_allocation(db, pay, "CHARGEBACK" if chargeback else "REFUND", actor, reason)
    pay.status = "CHARGEBACK" if chargeback else "REFUNDED"
    db.commit()
    return pay


# --------------------------------------------------------------------------
# FR-809: выплаты (реестр, четыре глаза)
# --------------------------------------------------------------------------

def create_payout(db: Session, org_id: int, period: str, actor: User | None) -> Payout:
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "Организация не найдена")
    if org.sb_status != "APPROVED" or org.blocked:
        raise HTTPException(409, "Выплата возможна только активному непроблокированному участнику")
    if not org.bank_details or not org.bank_details_verified:
        raise HTTPException(409, "Выплата невозможна: банковские реквизиты не подтверждены")
    acc = partner_account(db, org_id)
    amount = acc.balance_available
    if amount <= 0:
        raise HTTPException(409, "Нет доступных средств для выплаты")
    po = Payout(org_id=org_id, period=period, amount=amount, status="PENDING_APPROVAL",
                created_by=actor.id if actor else None,
                purpose=f"Выплата доли за {period}, ИНН {org.inn}")
    db.add(po)
    acc.balance_blocked += amount
    acc.balance_available -= amount
    audit(db, actor, "PAYOUT_CREATED", "payout", po.id, new_value={"amount": amount})
    db.commit()
    return po


def approve_payout(db: Session, payout_id: int, actor: User | None) -> Payout:
    po = db.get(Payout, payout_id)
    if not po:
        raise HTTPException(404, "Выплата не найдена")
    if po.status != "PENDING_APPROVAL":
        raise HTTPException(409, "Выплата не ожидает утверждения")
    if po.created_by == actor.id:
        raise HTTPException(403, "Правило «четыре глаза»: утверждать должен другой сотрудник")
    po.status = "APPROVED"
    po.approved_by = actor.id
    audit(db, actor, "PAYOUT_APPROVED", "payout", po.id)
    db.commit()
    return po


def mark_payout_sent(db: Session, payout_id: int, bank_ref: str, actor: User | None) -> Payout:
    po = db.get(Payout, payout_id)
    if not po or po.status != "APPROVED":
        raise HTTPException(409, "Выплату нельзя отправить в банк")
    po.status = "SENT_TO_BANK"
    po.bank_ref = bank_ref
    from datetime import datetime, timezone
    po.sent_at = datetime.now(timezone.utc)
    audit(db, actor, "PAYOUT_SENT", "payout", po.id, new_value={"bank_ref": bank_ref})
    db.commit()
    return po


def mark_payout_paid(db: Session, payout_id: int, actor: User | None) -> Payout:
    from datetime import datetime, timezone
    po = db.get(Payout, payout_id)
    if not po or po.status != "SENT_TO_BANK":
        raise HTTPException(409, "Выплата не отправлена в банк")
    acc = partner_account(db, po.org_id)
    acc.balance_blocked -= po.amount
    transit = get_account(db, TRANSIT, None, "transit")
    post_entry(db, "PAYOUT", acc, transit, po.amount, payout_id=po.id,
               purpose=f"Выплачено {po.purpose}")
    po.status = "PAID"
    po.paid_at = datetime.now(timezone.utc)
    audit(db, actor, "PAYOUT_PAID", "payout", po.id)
    notify_org(db, po.org_id, "PAYOUT_PAID",
               f"Выплата {money_format(po.amount)} за период {po.period} исполнена.",
               link=f"/#/payouts/{po.id}")
    db.commit()
    return po


def manual_adjustment(db: Session, org_id: int, amount: int, reason: str,
                      actor: User | None) -> LedgerTransaction:
    """Ручная корректировка финансиста — отдельная проводка ADJUSTMENT (тест-кейс Финансы-8)."""
    if not reason:
        raise HTTPException(400, "Корректировка требует причины")
    transit = get_account(db, TRANSIT, None, "transit")
    acc = partner_account(db, org_id)
    if amount > 0:
        tx = post_entry(db, "ADJUSTMENT", transit, acc, amount,
                        purpose=f"Ручная корректировка. {reason}")
        acc.balance_available += 0  # уже учтено проводкой
    else:
        tx = post_entry(db, "ADJUSTMENT", acc, transit, -amount,
                        purpose=f"Ручное удержание. {reason}")
    audit(db, actor, "MANUAL_ADJUSTMENT", "ledger", tx.id,
          new_value={"org_id": org_id, "amount": amount}, reason=reason)
    db.commit()
    return tx


# --------------------------------------------------------------------------
# FR-810: агентский отчет
# --------------------------------------------------------------------------

def build_agent_report_data(db: Session, org_id: int, period: str) -> dict:
    """Период — префикс даты 'YYYY-MM'. Собирает PARTNER_SHARE по ledger."""
    acc = partner_account(db, org_id)
    txs = db.query(LedgerTransaction).filter_by(credit_account_id=acc.id,
                                                operation_type="PARTNER_SHARE").all()
    reverse_txs = db.query(LedgerTransaction).filter_by(debit_account_id=acc.id). \
        filter(LedgerTransaction.operation_type.in_(["REFUND", "CHARGEBACK", "PAYOUT", "ADJUSTMENT"])).all()
    stations: dict[int, dict] = {}
    accrued = 0
    for tx in txs:
        if tx.created_at.strftime("%Y-%m") != period:
            continue
        accrued += tx.amount
        st = stations.setdefault(tx.station_id, {"station_id": tx.station_id, "gmv": 0, "share": 0})
        st["share"] += tx.amount
    payouts = db.query(Payout).filter_by(org_id=org_id, period=period).all()
    paid = sum(p.amount for p in payouts if p.status == "PAID")
    adjustments = sum(t.amount if t.credit_account_id == acc.id else -t.amount for t in reverse_txs
                      if t.created_at.strftime("%Y-%m") == period and t.operation_type == "ADJUSTMENT")
    refunds = sum(t.amount for t in reverse_txs
                  if t.created_at.strftime("%Y-%m") == period and t.operation_type in ("REFUND", "CHARGEBACK"))
    for sid in stations:
        from ..models import Payment as P
        gmv = db.query(P).filter(P.station_id == sid, P.status.in_(["RECEIVED"]),
                                 P.created_at.like(f"{period}%")). \
            with_entities(__import__("sqlalchemy").func.coalesce(__import__("sqlalchemy").func.sum(P.amount), 0)).scalar()
        stations[sid]["gmv"] = gmv or 0
    return {
        "org_id": org_id,
        "period": period,
        "stations": list(stations.values()),
        "accrued": accrued,
        "refunds_and_reversals": refunds,
        "adjustments": adjustments,
        "paid": paid,
        "balance_available": acc.balance_available,
        "balance_debt": min(acc.balance_available, 0),
        "to_pay": max(accrued - paid + adjustments - refunds, 0),
    }
