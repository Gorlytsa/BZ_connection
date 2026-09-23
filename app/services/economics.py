"""Экономика: справочник типов станций, прогноз GMV, рекомендации (FR-305..FR-309, FR-901..FR-904)."""
from ..config import settings
from ..models import StationType

DISCLAIMER = ("Прогноз является оценкой на основе доступных данных и не является гарантией дохода. "
              "Фактический оборот зависит от трафика, поведения пользователей, исправности станции "
              "и качества сервиса.")

TRAFFIC_FACTOR = {"low": 0.6, "medium": 1.0, "high": 1.4}
CATEGORY_FACTOR = {
    "coffee": 1.2, "restaurant": 1.1, "mall": 1.35, "store": 0.9,
    "bar": 1.15, "hotel": 1.0, "office": 0.85, "other": 1.0,
}


def forecast_for_type(st: StationType, traffic: str, category: str) -> dict:
    """Линейная MVP-модель: середина диапазона GMV типа × трафик × категория."""
    mid = (st.min_forecast_gmv + st.max_forecast_gmv) // 2
    value = int(mid * TRAFFIC_FACTOR.get(traffic, 1.0) * CATEGORY_FACTOR.get(category, 1.0))
    return {
        "expected": value,
        "min": int(value * 0.7),
        "max": int(value * 1.3),
        "confidence": "medium",
    }


def recommend_station_type(db_types: list[StationType], traffic: str, category: str) -> dict:
    """Рекомендация платформы (FR-306): тип с наибольшей ожидаемой маржой при загрузке ~70%."""
    best, best_score = None, -1
    for st in db_types:
        if not st.is_active:
            continue
        f = forecast_for_type(st, traffic, category)["expected"]
        # ограничиваем прогноз емкостью слотов (1 слот ≈ 3500 ₽/мес потенциала)
        capacity = st.slots * 350000
        capped = min(f, capacity)
        score = capped - st.capex_cost // 24  # штраф за капекс
        if score > best_score:
            best, best_score = st, score
    reasons = {
        "high": "высокий трафик, прогнозируется спрос выше среднего",
        "medium": "умеренный трафик, оптимален баланс цены и загрузки",
        "low": "низкий трафик, рекомендуется компактная станция для быстрой окупаемости",
    }
    return {
        "station_type_id": best.id if best else None,
        "slots": best.slots if best else None,
        "reason": f"Обоснование: {reasons.get(traffic, reasons['medium'])}, рядом мало конкурентов.",
    }


def type_warnings(chosen: StationType, db_types: list[StationType],
                  traffic: str, category: str) -> list[str]:
    """Предупреждения о нерациональном выборе (FR-307)."""
    warns = []
    expected = forecast_for_type(chosen, traffic, category)["expected"]
    capacity = chosen.slots * 350000
    if expected > capacity:
        warns.append("При выбранном типе станции возможен дефицит пауэрбанков "
                     "и потеря до 40% потенциального оборота.")
    utilization = capacity and expected / capacity
    if utilization and utilization < 0.35:
        warns.append("Загрузка может быть низкой, окупаемость увеличится.")
    return warns


def economics(db_types: list[StationType], chosen_id: int, traffic: str, category: str) -> dict:
    """Шаг 5 мастера — прогноз экономики (FR-308). Все суммы в копейках."""
    chosen = next((t for t in db_types if t.id == chosen_id), None)
    if not chosen:
        return {"error": "Тип станции не найден"}
    f = forecast_for_type(chosen, traffic, category)
    gmv = f["expected"]
    monthly_capex_income = gmv * settings.share_capex // 100
    payback_months = round(chosen.capex_cost / monthly_capex_income, 1) if monthly_capex_income else None
    return {
        "station_type": {"id": chosen.id, "name": chosen.name, "slots": chosen.slots,
                         "capex_cost": chosen.capex_cost},
        "forecast_gmv": gmv,
        "gmv_min": f["min"], "gmv_max": f["max"], "confidence": f["confidence"],
        "shares": {
            "platform": gmv * settings.share_platform // 100,
            "location": gmv * settings.share_location // 100,
            "capex": gmv * settings.share_capex // 100,
            "service": gmv * settings.share_service // 100,
        },
        "investment": chosen.capex_cost,
        "payback_months": payback_months,
        "warnings": type_warnings(chosen, db_types, traffic, category),
        "recommendation": recommend_station_type(db_types, traffic, category),
        "disclaimer": DISCLAIMER,
    }
