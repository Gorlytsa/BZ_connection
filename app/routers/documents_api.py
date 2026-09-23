"""API документов: список, просмотр, подписание/отказ через «ЭДО» (FR-710..FR-715)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, DocumentPackage, Organization, Station, User
from ..services import documents as docsrv
from ..services.security import get_current_user

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.get("")
def my_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org_id = user.org_id
    result = []
    q = db.query(Document)
    if user.system_role not in ("admin", "moderator"):
        if not org_id:
            return []
        q = q.filter(Document.sign_required_org_ids.any(org_id))
    for d in q.order_by(Document.id.desc()).limit(200).all():
        pkg = db.get(DocumentPackage, d.package_id)
        station = db.get(Station, pkg.station_id)
        result.append({"id": d.id, "doc_type": d.doc_type, "status": d.status,
                       "station": station.external_code, "station_id": station.id,
                       "edo_id": d.edo_id, "version": d.version,
                       "sign_required": d.sign_required_org_ids,
                       "signed_by": d.signed_by_org_ids,
                       "deadline_at": pkg.deadline_at.isoformat() if pkg.deadline_at else None})
    return result


@router.get("/{doc_id}")
def get_document(doc_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Документ не найден")
    if user.system_role not in ("admin", "moderator", "auditor"):
        if not user.org_id or user.org_id not in d.parties_json + [0]:
            if user.org_id not in d.sign_required_org_ids:
                raise HTTPException(403, "Документ недоступен этой организации")
    return {"id": d.id, "doc_type": d.doc_type, "status": d.status, "body": d.body,
            "edo_id": d.edo_id, "version": d.version,
            "sign_required": d.sign_required_org_ids, "signed_by": d.signed_by_org_ids}


class SignIn(BaseModel):
    signature: str = "ЭП"  # в MVP — отметка; в проде — КЭП через ЭДО-провайдера


@router.post("/{doc_id}/sign")
def sign(doc_id: int, data: SignIn, user: User = Depends(get_current_user),
         db: Session = Depends(get_db)):
    if not user.org_id and user.system_role != "admin":
        raise HTTPException(400, "Нет организации для подписания")
    org_id = user.org_id if user.org_id else 0
    doc = docsrv.sign_document(db, doc_id, org_id, user)
    pkg = db.get(DocumentPackage, doc.package_id)
    return {"ok": True, "document_status": doc.status, "package_status": pkg.status}


class RejectDocIn(BaseModel):
    reason: str


@router.post("/{doc_id}/reject")
def reject(doc_id: int, data: RejectDocIn, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    if not user.org_id:
        raise HTTPException(400, "Нет организации")
    docsrv.reject_document(db, doc_id, user.org_id, data.reason, user)
    return {"ok": True, "hint": "Пакет отклонен. Платформа запускает досборку цепочки (FR-714)"}
