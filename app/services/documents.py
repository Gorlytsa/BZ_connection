"""Генерация документов по сценариям (FR-701..FR-715).

Правила (раздел 10.3):
- Шаблон A: финансово-агентский договор Платформа ↔ каждый участник;
- Шаблон B: размещение Локация ↔ Капекс — только если это разные юрлица;
- Шаблон C: сервисный договор Капекс ↔ Сервис — только если это разные юрлица;
- Сквозная спецификация станции привязывается ко всем договорам.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from jinja2 import Template
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (Document, DocumentPackage, DocumentTemplate, Location,
                      Organization, Specification, Station, StationParticipant, User)
from .audit import audit, notify_org

PLATFORM_NAME = "ООО «ПауэрСтейшн Экслейдж» (Платформа)"

TEMPLATE_A = """ФИНАНСОВО-АГЕНТСКИЙ ДОГОВОР № {{ doc_number }} (шаблон A, ред. {{ tpl_version }})

г. Москва                                                     «{{ day }}» {{ monthname }} {{ year }} г.

{{ platform_name }}, именуемое далее «Агент», и {{ party_name }} (ИНН {{ party_inn }}),
именуемое далее «Принципал», заключили настоящий договор о нижеследующем.

1. ПРЕДМЕТ
1.1. Агент по поручению Принципала принимает денежные средства от конечных пользователей
за аренду пауэрбанков на станции {{ station_code }} (адрес: {{ address }}).
1.2. Агент удерживает агентское вознаграждение в размере {{ platform_share }}% от поступлений.
1.3. Оставшаяся доля Принципала в размере {{ party_share }}% перечисляется Принципалу
в порядке и сроки, установленные Спецификацией.
1.4. Роли Принципала по станции: {{ roles }}.
1.5. Принципал признает своей выручкой исключительно свою долю, а не весь оборот станции.

2. СПЕЦИФИКАЦИЯ
2.1. Существенным условием договора является Сквозная спецификация № {{ spec_number }}
к станции {{ station_code }}, являющаяся неотъемлемой частью договора.
2.2. Порядок выплат: реестром, не чаще одного раза в месяц, при подтвержденных реквизитах.
2.3. Отчетность: агентский отчет за календарный месяц через ЭДО.

3. СРОК И РАСТОРЖЕНИЕ
3.1. Договор вступает в силу с момента подписания ВСЕХ документов цепочки станции
(отлагательное условие, FR-712) и действует до расторжения.
3.2. Расторжение — по письменному уведомлению за {{ exit_notice_days }} дней.

ПОДПИСИ СТОРОН:
{{ platform_name }}: ________________ / {{ platform_signer }}
{{ party_name }}: ________________ / {{ party_signer }}"""

TEMPLATE_B = """ДОГОВОР ПРЕДОСТАВЛЕНИЯ ПЛОЩАДИ И ДОСТУПА № {{ doc_number }} (шаблон B, ред. {{ tpl_version }})

{{ location_name }} (ИНН {{ location_inn }}), «Локация», и
{{ capex_name }} (ИНН {{ capex_inn }}), «Капекс», заключили договор о нижеследующем.

1. Локация предоставляет Капексу место для размещения зарядной станции
{{ station_code }} по адресу: {{ address }} (пермалинк: {{ permalink }}).
2. Тип станции: {{ station_type }} ({{ slots }} слотов), требования к питанию: {{ power }}.
3. Доступ обеспечивается в часы работы точки: {{ working_hours }}.
4. Локация обеспечивает доступ авторизованных сервисных компаний для обслуживания.
5. Локация гарантирует наличие законных прав на распоряжение площадью и возмещает
убытки при нарушении третьих лиц на эту площадь.
6. Оплата между сторонами производится через Агента согласно Спецификации
№ {{ spec_number }} к станции {{ station_code }}.
7. Договор вступает в силу при подписании всех документов цепочки (FR-712).

ЛОКАЦИЯ: ________________ / {{ location_signer }}
КАПЕКС: ________________ / {{ capex_signer }}"""

TEMPLATE_C = """ДОГОВОР ТЕХНИЧЕСКОГО ОБСЛУЖИВАНИЯ № {{ doc_number }} (шаблон C, ред. {{ tpl_version }})

{{ capex_name }} (ИНН {{ capex_inn }}), «Заказчик», и
{{ service_name }} (ИНН {{ service_inn }}), «Сервис», заключили договор о нижеследующем.

1. Сервис обслуживает станцию {{ station_code }}: ротация пауэрбанков, диагностика,
устранение инцидентов, контроль загрузки.
2. SLA: реакция на инцидент — не более 24 часов, устранение — не более 72 часов.
3. Порядок передачи пауэрбанков фиксируется актами приема-передачи.
4. Ответственность за утрату пауэрбанка —Replacement cost, фиксируется в акте.
5. Вознаграждение Сервиса — {{ service_share }}% оборота станции, выплачивается
через Агента по Спецификации № {{ spec_number }} в рамках агентской схемы.
6. Договор вступает в силу при подписании всех документов цепочки (FR-712).

ЗАКАЗЧИК: ________________ / {{ capex_signer }}
СЕРВИС: ________________ / {{ service_signer }}"""

TEMPLATE_SPEC = """СКВОЗНАЯ СПЕЦИФИКАЦИЯ № {{ spec_number }} (версия {{ version }}) к станции {{ station_code }}

Station ID: {{ station_id }} | Внешний код: {{ station_code }}
Адрес: {{ address }} | Город: {{ city }} | Пермалинк: {{ permalink }}
Тип станции: {{ station_type }} ({{ slots }} слотов), модель: {{ model }}

СОСТАВ УЧАСТНИКОВ И ДОЛИ:
{% for p in participants %}- {{ p.name }} (ИНН {{ p.inn }}), роли: {{ p.roles }} — доля {{ p.share }}%
{% endfor %}- {{ platform_name }} — агентское вознаграждение {{ platform_share }}%
ИТОГО: 100%

ПОРЯДОК РАСЧЕТОВ: 100% выручки поступает Агенту; Агент удерживает вознаграждение
и перечисляет доли реестром раз в месяц по подтвержденным реквизитам.
Дата вступления в силу: с подписания всех документов цепочки.
Связанные договоры: {{ linked_docs }}
Спецификация влечет изменение долей/состава только новой версией (4.1.11)."""

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def ensure_templates(db: Session):
    """Загрузка юридически одобренных шаблонов (FR-701)."""
    defs = [
        ("A", "1.0", "Финансово-агентский договор", TEMPLATE_A),
        ("B", "1.0", "Договор предоставления площади и доступа", TEMPLATE_B),
        ("C", "1.0", "Договор технического обслуживания", TEMPLATE_C),
        ("SPEC", "1.0", "Сквозная спецификация станции", TEMPLATE_SPEC),
    ]
    for code, ver, title, content in defs:
        exists = db.query(DocumentTemplate).filter_by(code=code, version=ver).one_or_none()
        if not exists:
            db.add(DocumentTemplate(code=code, version=ver, title=title, content=content))
    db.commit()


def _org(db: Session, org_id: int) -> Organization:
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, f"Организация {org_id} не найдена")
    return org


def compute_shares(role_map: dict[str, int]) -> dict[int, dict]:
    """role_map: {'location': org_id, 'capex': org_id, 'service': org_id}.
    Возвращает {org_id: {"roles": [...], "share": percent}} с объединением ролей."""
    base = {"location": settings.share_location, "capex": settings.share_capex,
            "service": settings.share_service}
    result: dict[int, dict] = {}
    for role, org_id in role_map.items():
        entry = result.setdefault(org_id, {"roles": [], "share": 0})
        entry["roles"].append(role)
        entry["share"] += base[role]
    total = sum(e["share"] for e in result.values()) + settings.share_platform
    assert total == 100, f"Доли в сумме {total}% != 100%"
    return result


def scenario_document_plan(role_map: dict[str, int]) -> list[dict]:
    """Определяет набор документов по сценарию (FR-709). Чистая функция — тестируется."""
    plan = []
    shares = compute_shares(role_map)
    # 1. Агентские договоры (Шаблон A) — по одному на юрлицо
    for org_id, info in shares.items():
        plan.append({"doc_type": "agency_A", "template": "A",
                     "parties": [0, org_id], "signers": [org_id],
                     "title": f"Агентский договор: Платформа ↔ {info['roles']}"})
    loc, cap, srv = role_map["location"], role_map["capex"], role_map["service"]
    # 2. Размещение (Шаблон B) — если локация ≠ капекс
    if loc != cap:
        plan.append({"doc_type": "placement_B", "template": "B",
                     "parties": [loc, cap], "signers": [loc, cap],
                     "title": "Договор предоставления площади и доступа"})
    # 3. Сервис (Шаблон C) — если капекс ≠ сервис
    if cap != srv:
        plan.append({"doc_type": "service_C", "template": "C",
                     "parties": [cap, srv], "signers": [cap, srv],
                     "title": "Договор технического обслуживания"})
    # 4. Спецификация — подписывают все участники
    plan.append({"doc_type": "SPEC", "template": "SPEC",
                 "parties": sorted(shares.keys()), "signers": sorted(shares.keys()),
                 "title": "Сквозная спецификация станции"})
    return plan


def build_context(db: Session, station: Station) -> dict:
    loc = db.get(Location, station.location_id)
    stype = station.station_type_id
    from ..models import StationType
    t = db.get(StationType, stype)
    app = None
    from ..models import Application
    app = db.get(Application, station.application_id)
    now = datetime.now(timezone.utc)
    ctx = {
        "platform_name": PLATFORM_NAME,
        "platform_signer": "Директор Иванов А.А.",
        "station_id": station.id,
        "station_code": station.external_code,
        "address": loc.address, "city": loc.city, "permalink": loc.permalink,
        "station_type": t.name, "slots": t.slots, "model": t.model,
        "power": t.power_requirements,
        "working_hours": app.working_hours if app else "",
        "day": now.day, "monthname": MONTHS[now.month - 1], "year": now.year,
        "tpl_version": "1.0",
        "platform_share": settings.share_platform,
        "exit_notice_days": settings.exit_notice_days,
    }
    return ctx


def generate_package(db: Session, station: Station, actor: User | None = None) -> DocumentPackage:
    """Формирование пакета после сборки цепочки (FR-710)."""
    parts = db.query(StationParticipant).filter_by(station_id=station.id, status="ACTIVE").all()
    role_map: dict[str, int] = {}
    for p in parts:
        for r in p.roles_json:
            role_map[r] = p.org_id
    for need in ("location", "capex", "service"):
        if need not in role_map:
            raise HTTPException(409, f"Цепочка не собрана: роль {need} не назначена")

    plan = scenario_document_plan(role_map)
    shares = compute_shares(role_map)

    # Спецификация станции (версионирование, FR-707)
    prev_max = db.query(Specification).filter_by(station_id=station.id). \
        order_by(Specification.version.desc()).first()
    spec_version = (prev_max.version + 1) if prev_max else 1
    spec = Specification(
        station_id=station.id, version=spec_version, status="GENERATED",
        shares_json={**{str(oid): i["share"] for oid, i in shares.items()},
                     "platform": settings.share_platform},
        station_type_id=station.station_type_id,
    )
    db.add(spec)
    db.flush()
    spec.number = f"SPEC-{station.external_code}-v{spec_version}"

    pkg = DocumentPackage(station_id=station.id, spec_id=spec.id, status="GENERATED",
                          deadline_at=datetime.now(timezone.utc) +
                          timedelta(hours=settings.signing_deadline_hours))
    db.add(pkg)
    db.flush()

    ctx = build_context(db, station)
    ctx["spec_number"] = spec.number
    ctx["version"] = spec_version
    ctx["participants"] = [{
        "name": _org(db, oid).legal_name, "inn": _org(db, oid).inn,
        "roles": ", ".join(info["roles"]), "share": info["share"],
    } for oid, info in shares.items()]
    ctx["linked_docs"] = "; ".join(p["title"] for p in plan if p["doc_type"] != "SPEC")

    templates = {t.code: t for t in db.query(DocumentTemplate).all()}
    doc_seq = 1000 + pkg.id
    for item in plan:
        tpl = templates[item["template"]]
        dctx = dict(ctx)
        dctx["doc_number"] = doc_seq
        doc_seq += 1
        if item["doc_type"] == "agency_A":
            oid = item["parties"][1]
            org = _org(db, oid)
            dctx.update({"party_name": org.legal_name, "party_inn": org.inn,
                         "party_signer": org.signer_name or "____________",
                         "roles": ", ".join(shares[oid]["roles"]),
                         "party_share": shares[oid]["share"]})
        elif item["doc_type"] == "placement_B":
            l, c = _org(db, role_map["location"]), _org(db, role_map["capex"])
            dctx.update({"location_name": l.legal_name, "location_inn": l.inn,
                         "location_signer": l.signer_name or "____",
                         "capex_name": c.legal_name, "capex_inn": c.inn,
                         "capex_signer": c.signer_name or "____"})
        elif item["doc_type"] == "service_C":
            c, s = _org(db, role_map["capex"]), _org(db, role_map["service"])
            dctx.update({"capex_name": c.legal_name, "capex_inn": c.inn,
                         "capex_signer": c.signer_name or "____",
                         "service_name": s.legal_name, "service_inn": s.inn,
                         "service_signer": s.signer_name or "____",
                         "service_share": settings.share_service})
        body = Template(tpl.content).render(**dctx)
        db.add(Document(package_id=pkg.id, template_id=tpl.id, doc_type=item["doc_type"],
                        parties_json=item["parties"], sign_required_org_ids=item["signers"],
                        signed_by_org_ids=[], status="GENERATED", body=body,
                        edo_id=f"EDO-{pkg.id}-{item['doc_type'][:4]}-{db.query(Document).count()+1}"))
    audit(db, actor, "DOCUMENT_PACKAGE_GENERATED", "package", pkg.id,
          new_value={"station": station.external_code, "docs": len(plan)})
    db.commit()
    return pkg


def send_package(db: Session, pkg: DocumentPackage, actor: User | None = None):
    """Отправка в ЭДО (MVP — имитация) и уведомления участникам (FR-710)."""
    docs = db.query(Document).filter_by(package_id=pkg.id).all()
    for d in docs:
        d.status = "SENT"
    pkg.status = "SENT"
    pkg.sent_at = datetime.now(timezone.utc)
    station = db.get(Station, pkg.station_id)
    for oid in {x for d in docs for x in d.sign_required_org_ids}:
        notify_org(db, oid, "DOCS_FOR_SIGNING",
                   f"Станция {station.external_code}: документы отправлены на подпись. "
                   f"Срок — {settings.signing_deadline_hours} ч.",
                   link="/#/documents")
    audit(db, actor, "DOCUMENT_PACKAGE_SENT", "package", pkg.id, actor_type="system")
    db.commit()


def sign_document(db: Session, doc_id: int, org_id: int, actor: User | None):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    if org_id not in doc.sign_required_org_ids:
        raise HTTPException(403, "Эта организация не является подписантом документа")
    if org_id in doc.signed_by_org_ids:
        raise HTTPException(409, "Документ уже подписан этой организацией")
    pkg = db.get(DocumentPackage, doc.package_id)
    if pkg.status in ("EXPIRED", "CANCELLED", "SUPERSEDED"):
        raise HTTPException(409, "Пакет аннулирован, подписание невозможно")
    doc.signed_by_org_ids = doc.signed_by_org_ids + [org_id]
    if set(doc.signed_by_org_ids) >= set(doc.sign_required_org_ids):
        doc.status = "FULLY_SIGNED"
        doc.signed_at = datetime.now(timezone.utc)
    else:
        doc.status = "PARTIALLY_SIGNED"
    # статус пакета (FR-713) и переход к активации (FR-712 отлагательное условие)
    docs = db.query(Document).filter_by(package_id=pkg.id).all()
    if all(d.status == "FULLY_SIGNED" for d in docs):
        pkg.status = "FULLY_SIGNED"
        pkg.fully_signed_at = datetime.now(timezone.utc)
        spec = db.get(Specification, pkg.spec_id)
        if spec:
            spec.status = "SIGNED"
            spec.effective_from = datetime.now(timezone.utc)
        for p in db.query(StationParticipant).filter_by(station_id=pkg.station_id):
            p.effective_from = datetime.now(timezone.utc)
        station = db.get(Station, pkg.station_id)
        if station.status in ("PLANNED", "DOCS_PENDING"):
            station.status = "EQUIPMENT_ORDER"
        for oid in {x for d in docs for x in d.sign_required_org_ids}:
            notify_org(db, oid, "CHAIN_SIGNED",
                       f"Все документы по станции {station.external_code} подписаны. "
                       "Заказ оборудования оформлен.", link=f"/#/stations/{station.id}")
    else:
        pkg.status = "PARTIALLY_SIGNED"
    audit(db, actor, "DOCUMENT_SIGNED", "document", doc.id, new_value={"by_org": org_id})
    db.commit()
    return doc


def reject_document(db: Session, doc_id: int, org_id: int, reason: str, actor: User | None):
    """Отказ участника → пакет REJECTED → досборка (FR-714)."""
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    if org_id not in doc.sign_required_org_ids:
        raise HTTPException(403, "Эта организация не является подписантом документа")
    pkg = db.get(DocumentPackage, doc.package_id)
    doc.status = "REJECTED"
    pkg.status = "REJECTED"
    station = db.get(Station, pkg.station_id)
    # освобождаем роли отказавшего участника
    parts = db.query(StationParticipant).filter_by(station_id=station.id, org_id=org_id).all()
    for p in parts:
        p.status = "REPLACED"
    from ..models import Application, ApplicationRole
    app = db.get(Application, station.application_id)
    for p in parts:
        for r in p.roles_json:
            ar = db.query(ApplicationRole).filter_by(application_id=app.id, role_type=r).first()
            if ar:
                ar.is_filled = False
                ar.assigned_org_id = None
                ar.status = "SEARCHING"
    app.status = "REASSEMBLY"
    spec = db.get(Specification, pkg.spec_id)
    if spec:
        spec.status = "CANCELLED"
    other_orgs = {x for d in db.query(Document).filter_by(package_id=pkg.id).all()
                  for x in d.sign_required_org_ids} - {org_id}
    for oid in other_orgs:
        notify_org(db, oid, "PARTICIPANT_REJECTED",
                   f"Участник отказался подписывать документы по станции "
                   f"{station.external_code}. Запущена досборка.", link=f"/#/stations/{station.id}")
    audit(db, actor, "DOCUMENT_REJECTED", "document", doc.id, reason=reason,
          new_value={"by_org": org_id})
    db.commit()


def expire_packages(db: Session):
    """Просрочка срока подписания → EXPIRED + досборка (FR-714, тест Документы-8)."""
    now = datetime.now(timezone.utc)
    pkgs = db.query(DocumentPackage).filter(
        DocumentPackage.status.in_(["SENT", "PARTIALLY_SIGNED"]),
        DocumentPackage.deadline_at < now).all()
    for pkg in pkgs:
        pkg.status = "EXPIRED"
        for d in db.query(Document).filter_by(package_id=pkg.id).all():
            if d.status != "FULLY_SIGNED":
                d.status = "EXPIRED"
        station = db.get(Station, pkg.station_id)
        from ..models import Application, ApplicationRole
        app = db.get(Application, station.application_id)
        if app and app.status == "SIGNING":
            app.status = "REASSEMBLY"
            parts = db.query(StationParticipant).filter_by(station_id=station.id).all()
            for p in parts:
                if p.status == "ACTIVE":
                    p.status = "REPLACED"
                for r in p.roles_json:
                    ar = db.query(ApplicationRole).filter_by(application_id=app.id,
                                                             role_type=r).first()
                    if ar:
                        ar.is_filled = False
                        ar.assigned_org_id = None
                        ar.status = "SEARCHING"
        audit(db, None, "PACKAGE_EXPIRED", "package", pkg.id, actor_type="system")
    if pkgs:
        db.commit()
    return len(pkgs)
