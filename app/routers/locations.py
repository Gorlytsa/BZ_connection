"""API локаций: поиск, создание с проверкой дублей, права (FR-201..FR-205)."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Location, LocationDocument, Station, User
from ..services.audit import audit
from ..services.security import get_current_user

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])


def normalize_address(a: str) -> str:
    return re.sub(r"\s+", " ", a.strip().lower().replace("г. ", "").replace("город ", ""))


class LocationIn(BaseModel):
    address: str
    city: str = ""
    permalink: str = ""
    place_name: str = ""
    category: str = "other"
    geo_lat: float | None = None
    geo_lng: float | None = None


@router.post("/search")
def search(q: str = "", db: Session = Depends(get_db)):
    """Поиск по справочнику локаций (в MVP — вместо внешнего Гео-API)."""
    query = db.query(Location)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter((Location.address.ilike(like)) | (Location.permalink.ilike(like)) |
                             (Location.place_name.ilike(like)))
    locs = query.limit(50).all()
    return [{"id": l.id, "address": l.address, "city": l.city, "permalink": l.permalink,
             "place_name": l.place_name, "category": l.category,
             "verification_status": l.verification_status} for l in locs]


@router.get("/{location_id}")
def get_location(location_id: int, db: Session = Depends(get_db)):
    l = db.get(Location, location_id)
    if not l:
        raise HTTPException(404, "Локация не найдена")
    stations = db.query(Station).filter_by(location_id=l.id). \
        filter(Station.status.notin_(["ARCHIVED", "DECOMMISSIONED"])).count()
    return {"id": l.id, "address": l.address, "city": l.city, "permalink": l.permalink,
            "place_name": l.place_name, "category": l.category,
            "verification_status": l.verification_status, "rights_status": l.rights_status,
            "stations_count": stations, "photos": l.photos_json}


@router.post("")
def create_location(data: LocationIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not data.address.strip():
        raise HTTPException(400, "Укажите адрес")
    norm = normalize_address(data.address)
    # Проверка дублей по адресу и пермалинку (FR-202)
    dup_addr = [l for l in db.query(Location).all() if normalize_address(l.address) == norm]
    if data.permalink:
        dup_link = db.query(Location).filter(Location.permalink == data.permalink).first()
        if dup_link:
            raise HTTPException(409, {
                "detail": "Локация с таким пермалинком уже существует",
                "existing_location_id": dup_link.id})
    if dup_addr:
        raise HTTPException(409, {
            "detail": "Найден потенциальный дубль адреса. Продолжить нельзя без модерации.",
            "existing_location_id": dup_addr[0].id})
    loc = Location(address=data.address.strip(), city=data.city,
                   permalink=data.permalink or f"auto/{abs(hash(norm)) % 10**8}",
                   place_name=data.place_name, category=data.category,
                   verification_status="MODERATION",  # новая точка → модерация (FR-203)
                   owner_org_id=user.org_id, geo_lat=data.geo_lat, geo_lng=data.geo_lng)
    db.add(loc)
    audit(db, user, "LOCATION_CREATED", "location", -1, new_value={"address": loc.address,
          "status": "MODERATION"})
    db.commit()
    return {"id": loc.id, "verification_status": loc.verification_status,
            "hint": "Локация отправлена на модерацию. Публикация заявок ограничена до проверки (FR-203)"}


class RightsIn(BaseModel):
    rights_status: str  # DECLARATION / PRELIMINARY_DOC / MANUAL_CHECK
    doc_type: str = "lease"
    file_url: str = ""


@router.post("/{location_id}/rights")
def declare_rights(location_id: int, data: RightsIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Декларация прав на площадь (FR-204)."""
    loc = db.get(Location, location_id)
    if not loc:
        raise HTTPException(404, "Локация не найдена")
    if data.rights_status not in ("DECLARATION", "PRELIMINARY_DOC", "MANUAL_CHECK"):
        raise HTTPException(400, "Недопустимый статус прав")
    loc.rights_status = data.rights_status
    db.add(LocationDocument(location_id=loc.id, doc_type=data.doc_type,
                            file_url=data.file_url, uploaded_by=user.id))
    audit(db, user, "RIGHTS_DECLARED", "location", loc.id, new_value=data.rights_status)
    db.commit()
    return {"ok": True, "rights_status": loc.rights_status}
