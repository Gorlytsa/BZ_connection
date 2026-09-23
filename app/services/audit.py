"""Аудит (4.1.12) и уведомления (раздел 28)."""
import json

from sqlalchemy.orm import Session

from ..models import AuditLog, Notification, User


def audit(db: Session, actor: User | None, action: str, entity_type: str, entity_id,
          old_value=None, new_value=None, reason: str = "", actor_type: str = "user"):
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_type=actor_type,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        old_value=json.dumps(old_value, ensure_ascii=False, default=str) if old_value is not None else "",
        new_value=json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else "",
        reason=reason,
    ))


def notify_org(db: Session, org_id: int, template_code: str, message: str, link: str = ""):
    """In-app уведомление всем пользователям организации (28.3: со ссылкой на объект)."""
    users = db.query(User).filter(User.org_id == org_id).all()
    for u in users:
        db.add(Notification(user_id=u.id, org_id=org_id, channel="in-app",
                            template_code=template_code, message=message, link=link))
