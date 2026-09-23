"""Начальные данные MVP: справочники, шаблоны, демо-пользователи и сценарии.

Запуск: python -m app.seed  (или автоматически при старте приложения)
"""
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext  # noqa: F401  (в прод-контуре — bcrypt)

from .config import settings
from .database import Base, SessionLocal, engine
from .models import *  # noqa
from .services.documents import ensure_templates
from .services.security import hash_password


def seed():
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        Base.metadata.create_all(bind=engine)

        # Справочник типов станций (FR-901), суммы в копейках
        types = [
            StationType(name="Компакт 8", slots=8, capex_cost=5_900_000, model="PB-Compact8",
                        min_forecast_gmv=6_000_000, max_forecast_gmv=12_000_000, supply_days=10),
            StationType(name="Стандарт 16", slots=16, capex_cost=9_900_000, model="PB-Std16",
                        min_forecast_gmv=12_000_000, max_forecast_gmv=25_000_000, supply_days=14),
            StationType(name="Профи 32", slots=32, capex_cost=17_900_000, model="PB-Pro32",
                        min_forecast_gmv=25_000_000, max_forecast_gmv=45_000_000, supply_days=21),
        ]
        db.add_all(types)

        # Настройки по умолчанию (раздел 27.2)
        for k in ("share_platform", "share_location", "share_capex", "share_service"):
            db.add(Setting(key=k, value=str(getattr(settings, k))))

        ensure_templates(db)

        # Пользователи платформы (RBAC, раздел 2)
        staff_defs = [
            ("admin@ps-ex.app", "Администратор Платформы", "admin"),
            ("sb@ps-ex.app", "Служба Безопасности", "security"),
            ("moderator@ps-ex.app", "Менеджер Модерации", "moderator"),
            ("finance@ps-ex.app", "Финансовый Отдел", "finance"),
            ("ops@ps-ex.app", "Операционный Отдел", "ops"),
            ("auditor@ps-ex.app", "Аудитор", "auditor"),
        ]
        pwd = hash_password("DemoPass123!")
        for email, name, role in staff_defs:
            db.add(User(email=email, full_name=name, password_hash=pwd, system_role=role))

        # Демо-организации: три роли у разных юрлиц (сценарий S1) + одна «полная» (S4)
        orgs_data = [
            ("ООО «Кофейня Угол»", "7701234567", ["location"], "Москва", 4.8),
            ("ООО «ИнвестКапитал»", "7702345678", ["capex", "service"], "Москва, СПб", 4.6),
            ("ИП СервисМастеров И.И.", "771234567890", ["service"], "Москва", 4.9),
        ]
        org_ids = []
        for legal, inn, roles, regions, rating in orgs_data:
            o = Organization(legal_name=legal, short_name=legal, inn=inn,
                             legal_form="OOO" if not legal.startswith("ИП") else "IP",
                             sb_status="APPROVED", rating=rating,
                             bank_details=f"р/с 40702810{inn}, Банк ГФОТ02",
                             bank_details_verified=True,
                             signer_name="Директор Петров В.В.", signer_authority="Устав")
            db.add(o)
            db.flush()
            org_ids.append(o.id)
            for r in roles:
                db.add(OrganizationRole(org_id=o.id, role_type=r, regions=regions))
            db.add(User(email=f"demo{o.id}@partner.ru", full_name=f"Представитель {legal}",
                        phone="+7 900 000-00-0" + str(o.id), password_hash=pwd,
                        system_role="partner", org_id=o.id))

        # Локации (VERIFIED — чтобы не ждать модерацию на демо)
        loc = Location(address="Москва, ул. Тверская, 12, Кофейня «Угол»", city="Москва",
                       permalink="yandex.ru/maps/?um=constructor%3Ademo1",
                       place_name="Кофейня «Угол»", category="coffee",
                       verification_status="VERIFIED", rights_status="DECLARATION",
                       owner_org_id=org_ids[0])
        db.add(loc)
        db.commit()

        # Заявка автора-локации: нужны капекс+сервис → на бирже (ON_EXCHANGE)
        app = Application(location_id=loc.id, created_by_org_id=org_ids[0], status="ON_EXCHANGE",
                          station_type_id=types[1].id, forecast_gmv=18_000_000,
                          venue_category="coffee", traffic="high", working_hours="08:00–23:00",
                          author_note="Проходимая точка у метро, высокий трафик",
                          published_at=datetime.now(timezone.utc),
                          expires_at=datetime.now(timezone.utc) + timedelta(days=14))
        db.add(app)
        db.flush()
        db.add(ApplicationRole(application_id=app.id, role_type="location", is_filled=True,
                               assigned_org_id=org_ids[0], assigned_by="self", status="FILLED"))
        db.add(ApplicationRole(application_id=app.id, role_type="capex", status="OPEN"))
        db.add(ApplicationRole(application_id=app.id, role_type="service", status="OPEN"))
        db.commit()
        print("Seed выполнен. Демо-заявка #%d на бирже." % app.id)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("OK")
