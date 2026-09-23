"""Конфигурация MVP-платформы."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "PowerStation Exchange"
    secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24 * 7
    database_url: str = "sqlite:///./mvp.db"
    # Срок подписания пакета документов по умолчанию (FR-711)
    signing_deadline_hours: int = 72
    # Минимальный срок уведомления при плановом выходе (FR-1001)
    exit_notice_days: int = 30
    # Срок уведомления при замене сервиса (17.3)
    service_replacement_notice_days: int = 14
    # Доли по умолчанию, % (FR-802). Изменяется через справочник админки.
    share_platform: int = 35
    share_location: int = 20
    share_capex: int = 25
    share_service: int = 20


settings = Settings()
