"""Модель данных MVP (раздел 19 DESCRIPTION.md).

Ключевые принципы:
- деньги хранятся в копейках (minor units), float запрещен (FR-803);
- ledger неизменяем, исправления — сторно (FR-805);
- все действия пишутся в AuditLog (4.1.12).
"""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, JSON,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# 19.1 Пользователи и организации
# --------------------------------------------------------------------------

class User(Base):
    """Пользователь платформы. system_role — роль доступа (RBAC),
    roles — партнерские роли (location/capex/service)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    system_role: Mapped[str] = mapped_column(String(32), default="partner")
    # partner | admin | moderator | security | finance | ops | support | auditor
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Organization(Base):
    """Карточка организации (FR-101, FR-104)."""
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str] = mapped_column(String(255), default="")
    inn: Mapped[str] = mapped_column(String(12), index=True)
    kpp: Mapped[str] = mapped_column(String(9), default="")
    ogrn: Mapped[str] = mapped_column(String(15), default="")
    legal_form: Mapped[str] = mapped_column(String(16), default="OOO")  # OOO/IP/SAM/ZAO...
    tax_regime: Mapped[str] = mapped_column(String(32), default="")
    legal_address: Mapped[str] = mapped_column(String(512), default="")
    bank_details: Mapped[str] = mapped_column(String(512), default="")
    bank_details_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    edo_id: Mapped[str] = mapped_column(String(128), default="")
    signer_name: Mapped[str] = mapped_column(String(255), default="")
    signer_authority: Mapped[str] = mapped_column(String(255), default="")
    # Статус проверки СБ: PENDING / APPROVED / REJECTED / NEED_DOCS / SUSPENDED
    sb_status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    sb_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sb_comments: Mapped[str] = mapped_column(Text, default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[float] = mapped_column(Float, default=5.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    roles: Mapped[list["OrganizationRole"]] = relationship(back_populates="org")


class OrganizationRole(Base):
    """Разрешенные роли организации (FR-105)."""
    __tablename__ = "organization_roles"
    __table_args__ = (UniqueConstraint("org_id", "role_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    role_type: Mapped[str] = mapped_column(String(16))  # location / capex / service
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    regions: Mapped[str] = mapped_column(String(512), default="")
    max_stations: Mapped[int] = mapped_column(Integer, default=100)
    notes: Mapped[str] = mapped_column(Text, default="")

    org: Mapped[Organization] = relationship(back_populates="roles")


class Consent(Base):
    """Согласия при регистрации (FR-103)."""
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    doc_code: Mapped[str] = mapped_column(String(64))  # PD_PROCESSING / USER_AGREEMENT / ...
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")


class SecurityCheck(Base):
    """История проверок СБ (FR-102)."""
    __tablename__ = "security_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[str] = mapped_column(Text, default="")
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    checked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------------------------------
# 19.2 Локации
# --------------------------------------------------------------------------

class Location(Base):
    """Локация — отдельный объект, не равный станции (FR-201)."""
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(512), index=True)
    city: Mapped[str] = mapped_column(String(128), default="", index=True)
    geo_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    permalink: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    place_name: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(64), default="")  # тип заведения
    # VERIFIED / MODERATION / NEW_DECLARED
    verification_status: Mapped[str] = mapped_column(String(16), default="NEW_DECLARED")
    # NONE / DECLARATION / PRELIMINARY_DOC / MANUAL_CHECK
    rights_status: Mapped[str] = mapped_column(String(24), default="NONE")
    owner_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    allow_multiple_stations: Mapped[bool] = mapped_column(Boolean, default=False)
    photos_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LocationDocument(Base):
    __tablename__ = "location_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(64))  # lease / ownership / photo / letter
    file_url: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="UPLOADED")
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------------------------------
# 19.3 Заявки, роли заявки, отклики
# --------------------------------------------------------------------------

class StationType(Base):
    """Справочник типов станций (FR-901)."""
    __tablename__ = "station_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    slots: Mapped[int] = mapped_column(Integer)
    capex_cost: Mapped[int] = mapped_column(Integer)  # копейки
    model: Mapped[str] = mapped_column(String(64), default="")
    power_requirements: Mapped[str] = mapped_column(String(128), default="220V, 150W")
    area_requirements: Mapped[str] = mapped_column(String(128), default="")
    min_forecast_gmv: Mapped[int] = mapped_column(Integer, default=0)  # копейки/мес
    max_forecast_gmv: Mapped[int] = mapped_column(Integer, default=0)
    supply_days: Mapped[int] = mapped_column(Integer, default=14)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Application(Base):
    """Заявка на биржу (FR-301..FR-310)."""
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    created_by_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    station_type_id: Mapped[int] = mapped_column(ForeignKey("station_types.id"))
    forecast_gmv: Mapped[int] = mapped_column(Integer, default=0)  # копейки/мес
    recommendation: Mapped[str] = mapped_column(Text, default="")
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)  # история предупреждений FR-307
    # Параметры точки (FR-303)
    venue_category: Mapped[str] = mapped_column(String(64), default="")
    traffic: Mapped[str] = mapped_column(String(32), default="medium")  # low/medium/high
    working_hours: Mapped[str] = mapped_column(String(128), default="")
    has_socket: Mapped[bool] = mapped_column(Boolean, default=True)
    has_internet: Mapped[bool] = mapped_column(Boolean, default=True)
    mount_possible: Mapped[bool] = mapped_column(Boolean, default=True)
    comments: Mapped[str] = mapped_column(Text, default="")
    author_note: Mapped[str] = mapped_column(Text, default="")  # комментарий платформе FR-405
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    roles: Mapped[list["ApplicationRole"]] = relationship(back_populates="application")


APP_STATUS_FLOW = {
    # Переходы статусов заявки (13.2)
    "DRAFT": {"UNDER_REVIEW"},
    "UNDER_REVIEW": {"ON_EXCHANGE", "REJECTED"},
    "ON_EXCHANGE": {"ASSEMBLY", "EXPIRED"},
    "ASSEMBLY": {"SIGNING", "REASSEMBLY"},
    "SIGNING": {"DOCS_COMPLETED", "REASSEMBLY"},
    "DOCS_COMPLETED": {"EQUIPMENT_ORDER"},
    "EQUIPMENT_ORDER": {"DELIVERY"},
    "DELIVERY": {"INSTALLATION"},
    "INSTALLATION": {"ACTIVATION"},
    "ACTIVATION": {"ACTIVE"},
    "ACTIVE": {"REPLACEMENT_SEARCH", "SUSPENDED", "ARCHIVED"},
    "REASSEMBLY": {"ASSEMBLY"},
    "REPLACEMENT_SEARCH": {"ACTIVE", "ARCHIVED"},
}


class ApplicationRole(Base):
    """Роль в заявке: занята или свободна (FR-304)."""
    __tablename__ = "application_roles"
    __table_args__ = (UniqueConstraint("application_id", "role_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    role_type: Mapped[str] = mapped_column(String(16))
    is_filled: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    assigned_by: Mapped[str] = mapped_column(String(32), default="")  # self / platform
    status: Mapped[str] = mapped_column(String(24), default="OPEN")  # OPEN/FILLED/SEARCHING

    application: Mapped[Application] = relationship(back_populates="roles")


class Proposal(Base):
    """Отклик на роль (FR-403, FR-404)."""
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("application_id", "org_id", "role_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    role_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="SUBMITTED")
    # DRAFT/SUBMITTED/UNDER_REVIEW/APPROVED/REJECTED/WITHDRAWN/EXPIRED/RESERVED
    commercial_offer: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    ready_to_start: Mapped[str] = mapped_column(String(64), default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # скоринг FR-502
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------
# 19.4 Станции
# --------------------------------------------------------------------------

class Station(Base):
    """Центральный системный объект (FR-601)."""
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # ST-7745
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    station_type_id: Mapped[int] = mapped_column(ForeignKey("station_types.id"))
    # PLANNED/DOCS_PENDING/EQUIPMENT_ORDER/DELIVERY/INSTALLATION/COMMISSIONING/
    # ACTIVE/MAINTENANCE/OFFLINE/SUSPENDED/MODERNIZATION/REPLACEMENT/DECOMMISSIONED/ARCHIVED
    status: Mapped[str] = mapped_column(String(24), default="PLANNED", index=True)
    qr_payload: Mapped[str] = mapped_column(String(255), default="")
    serial_number: Mapped[str] = mapped_column(String(64), default="")
    telemetry_id: Mapped[str] = mapped_column(String(64), default="")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    participants: Mapped[list["StationParticipant"]] = relationship(back_populates="station")


class StationParticipant(Base):
    """Участник цепочки станции с долей (FR-802). roles — список ролей юрлица."""
    __tablename__ = "station_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    roles_json: Mapped[list] = mapped_column(JSON, default=list)  # ["location","capex"]
    share_percent: Mapped[int] = mapped_column(Integer, default=0)  # суммарная доля в %
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE/EXITING/REPLACED
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    station: Mapped[Station] = relationship(back_populates="participants")


# --------------------------------------------------------------------------
# 19.5 Документы
# --------------------------------------------------------------------------

class DocumentTemplate(Base):
    """Утвержденные шаблоны (FR-701, FR-702)."""
    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("code", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True)  # A / B / C / SPEC / ACT / ADDENDUM
    version: Mapped[str] = mapped_column(String(16), default="1.0")
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)  # Jinja2-шаблон
    legal_status: Mapped[str] = mapped_column(String(16), default="APPROVED")
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=now)
    approved_by: Mapped[str] = mapped_column(String(128), default="legal")


class Specification(Base):
    """Сквозная спецификация станции (FR-707)."""
    __tablename__ = "specifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64), unique=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT/SENT/SIGNED/CANCELLED/SUPERSEDED
    shares_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {org_id: percent} + platform
    station_type_id: Mapped[int] = mapped_column(ForeignKey("station_types.id"))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class DocumentPackage(Base):
    """Пакет документов станции (FR-710, FR-713)."""
    __tablename__ = "document_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    spec_id: Mapped[int | None] = mapped_column(ForeignKey("specifications.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="GENERATED")
    # GENERATED/SENT/PARTIALLY_SIGNED/FULLY_SIGNED/EXPIRED/REJECTED/CANCELLED/SUPERSEDED
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fully_signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


DOC_STATUSES = ("GENERATED", "SENT", "VIEWED", "PARTIALLY_SIGNED", "FULLY_SIGNED",
                "REJECTED", "EXPIRED", "CANCELLED", "SUPERSEDED")


class Document(Base):
    """Договор/акт/спецификация (FR-704..FR-706)."""
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("document_packages.id"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("document_templates.id"))
    doc_type: Mapped[str] = mapped_column(String(32))  # agency_A / placement_B / service_C / SPEC
    parties_json: Mapped[list] = mapped_column(JSON, default=list)  # org_ids; 0 = платформа
    sign_required_org_ids: Mapped[list] = mapped_column(JSON, default=list)
    signed_by_org_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="GENERATED")
    edo_id: Mapped[str] = mapped_column(String(128), default="")
    file_url: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")  # сгенерированный текст
    version: Mapped[int] = mapped_column(Integer, default=1)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------------------------------
# 19.6 Финансы (все суммы — копейки, int)
# --------------------------------------------------------------------------

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    end_user_ref: Mapped[str] = mapped_column(String(64), default="")  # пользователь приложения
    amount: Mapped[int] = mapped_column(Integer)  # копейки
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(16), default="RECEIVED")  # RECEIVED/REFUNDED/CHARGEBACK
    acquiring_ref: Mapped[str] = mapped_column(String(128), default="")
    fiscal_ref: Mapped[str] = mapped_column(String(128), default="")
    tariff: Mapped[str] = mapped_column(String(64), default="")
    rental_id: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(32), default="acquiring")  # acquiring/sbp/wallet
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LedgerAccount(Base):
    """Счета: PLATFORM (агентский транзит), FEE, PARTICIPANT:<org_id> (FR-806)."""
    __tablename__ = "ledger_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    account_type: Mapped[str] = mapped_column(String(24))  # platform/transit/partner/fee
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    balance_available: Mapped[int] = mapped_column(Integer, default=0)
    balance_pending: Mapped[int] = mapped_column(Integer, default=0)
    balance_blocked: Mapped[int] = mapped_column(Integer, default=0)
    balance_debt: Mapped[int] = mapped_column(Integer, default=0)


LEDGER_OPS = ("PAYMENT_RECEIVED", "PLATFORM_FEE", "PARTNER_SHARE", "REFUND", "CHARGEBACK",
              "PAYOUT", "PAYOUT_FEE", "ADJUSTMENT", "PENALTY", "WITHHOLD", "RELEASE_HOLD")


class LedgerTransaction(Base):
    """Неизменяемый журнал (FR-805). Двойная запись: debit -> credit."""
    __tablename__ = "ledger_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(24), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"), nullable=True)
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("payouts.id"), nullable=True)
    debit_account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"))
    credit_account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"))
    amount: Mapped[int] = mapped_column(Integer)  # копейки, всегда > 0
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    purpose: Mapped[str] = mapped_column(String(512), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_transactions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Payout(Base):
    """Выплата по реестру (FR-809). Четыре глаза: created_by != approved_by."""
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    period: Mapped[str] = mapped_column(String(16))  # 2026-08
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    # DRAFT/PENDING_APPROVAL/APPROVED/SENT_TO_BANK/PAID/FAILED/REVERSED/ON_HOLD
    bank_ref: Mapped[str] = mapped_column(String(128), default="")
    purpose: Mapped[str] = mapped_column(String(512), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentReport(Base):
    """Агентский отчет (FR-810)."""
    __tablename__ = "agent_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    period: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT/SENT/SIGNED
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------
# 19.7 Оборудование и операции
# --------------------------------------------------------------------------

class EquipmentOrder(Base):
    __tablename__ = "equipment_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    supplier: Mapped[str] = mapped_column(String(128), default="")
    # CREATED/PREPARING/SHIPPED/IN_TRANSIT/DELIVERED/ACCEPTED
    status: Mapped[str] = mapped_column(String(16), default="CREATED")
    cost: Mapped[int] = mapped_column(Integer, default=0)
    expected_delivery_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InstallationTask(Base):
    """Монтаж с чек-листом (14.3)."""
    __tablename__ = "installation_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN/DONE
    assigned_to: Mapped[str] = mapped_column(String(128), default="")
    checklist_json: Mapped[dict] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MaintenanceTask(Base):
    __tablename__ = "maintenance_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32), default="incident")
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PowerbankAsset(Base):
    __tablename__ = "powerbank_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(64), unique=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="IN_STATION")  # IN_STATION/RENTED/LOST
    charge_level: Mapped[int] = mapped_column(Integer, default=100)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExitRequest(Base):
    """Запрос выхода участника из цепочки (17.1)."""
    __tablename__ = "exit_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    role_type: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    desired_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="REQUESTED")
    # REQUESTED / REPLACEMENT_SEARCH / HANDOVER / COMPLETED / CANCELLED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ModernizationRequest(Base):
    """Модернизация после активации (FR-908)."""
    __tablename__ = "modernization_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    initiator_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    new_station_type_id: Mapped[int] = mapped_column(ForeignKey("station_types.id"))
    justification: Mapped[str] = mapped_column(Text, default="")
    cost: Mapped[int] = mapped_column(Integer, default=0)
    investor_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    approvals_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {org_id: true/false}
    status: Mapped[str] = mapped_column(String(16), default="COLLECTING_CONSENTS")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------------------------------
# 19.8 Аудит и уведомления
# --------------------------------------------------------------------------

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="in-app")
    template_code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="SENT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    """Редактируемые справочники администратора (раздел 27)."""
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512))


class OfferAcceptance(Base):
    """Акцепт оферты конечным пользователем (18.3)."""
    __tablename__ = "offer_acceptances"

    id: Mapped[int] = mapped_column(primary_key=True)
    end_user_ref: Mapped[str] = mapped_column(String(64), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    offer_version: Mapped[str] = mapped_column(String(16), default="1.0")
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    ip: Mapped[str] = mapped_column(String(64), default="")
    device: Mapped[str] = mapped_column(String(255), default="")
