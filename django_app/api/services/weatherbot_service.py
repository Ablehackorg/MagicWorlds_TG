from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import requests


@dataclass
class DayForecast:
    day: str
    t_min: float
    t_max: float
    precip: float
    code: int


def geocode_city(city: str) -> Optional[Dict]:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    r = requests.get(url, params={"name": city, "count": 1, "language": "ru", "format": "json"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    return results[0] if results else None


def fetch_4days(lat: float, lon: float) -> List[DayForecast]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
        "forecast_days": 4,
    }
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    d = r.json()["daily"]

    out: List[DayForecast] = []
    for i in range(4):
        out.append(
            DayForecast(
                day=d["time"][i],
                t_min=float(d["temperature_2m_min"][i]),
                t_max=float(d["temperature_2m_max"][i]),
                precip=float(d["precipitation_sum"][i]),
                code=int(d["weathercode"][i]),
            )
        )
    return out


def wmo_text(code: int) -> str:
    if code == 0:
        return "Ясно ☀️"
    if code in (1, 2, 3):
        return "Облачно ⛅"
    if code in (45, 48):
        return "Туман 🌫"
    if code in (51, 53, 55, 56, 57):
        return "Морось 🌦"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "Дождь 🌧"
    if code in (71, 73, 75, 77):
        return "Снег ❄️"
    if code in (95, 96, 99):
        return "Гроза ⛈"
    return "Погода"


def pick_kind(code: int) -> str:
    if code == 0:
        return "sunny"
    if code in (1, 2, 3, 45, 48):
        return "cloudy"
    return "precip"


def build_message(country: str, city: str, days: List[DayForecast]) -> str:
    t = days[0]
    lines = [
        f"🌍 <b>{country}, {city}</b>",
        "",
        f"📌 <b>Сегодня</b>: {wmo_text(t.code)}",
        f"🌡 {t.t_min:.0f}…{t.t_max:.0f}°C   💧 осадки: {t.precip:.0f} мм",
        "",
        "🗓 <b>Прогноз на 3 дня</b>:",
    ]
    for d in days[1:]:
        lines.append(f"• {d.day}: {wmo_text(d.code)}  {d.t_min:.0f}…{d.t_max:.0f}°C  💧{d.precip:.0f}мм")
    return "\n".join(lines)
