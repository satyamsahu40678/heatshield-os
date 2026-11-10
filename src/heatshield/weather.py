"""Optional remote environmental context; never a WBGT measurement substitute."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import require_roles
from .database import utcnow

router = APIRouter(prefix="/api/v1/weather", tags=["Environmental context"])


def fetch_context(latitude: float, longitude: float) -> dict:
    with httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
        response = client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": (
                    "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation"
                ),
                "timezone": "UTC",
                "forecast_days": 2,
            },
        )
        response.raise_for_status()
        if len(response.content) > 1_048_576:
            raise ValueError("Weather response exceeded the context limit")
        payload = response.json()
    hourly = payload["hourly"]
    times = hourly["time"]
    if not isinstance(times, list) or len(times) > 72:
        raise ValueError("Unexpected weather horizon")
    names = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m", "shortwave_radiation")
    rows = []
    for index, instant in enumerate(times):
        rows.append({"time": instant + "Z", **{name: hourly[name][index] for name in names}})
    return {
        "source": "Open-Meteo",
        "classification": "screening_context",
        "attribution_url": "https://open-meteo.com/",
        "fetched_at": utcnow().isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "UTC",
        "wbgt_c": None,
        "can_admit_heat_work": False,
        "note": "Forecast context cannot establish on-site WBGT or approve a work assignment.",
        "units": payload.get("hourly_units", {}),
        "hourly": rows,
    }


@router.get("/context")
def context(
    _user: Annotated[object, Depends(require_roles("admin", "safety_officer", "supervisor"))],
    latitude: Annotated[float, Query(ge=-90, le=90)] = 24.4539,
    longitude: Annotated[float, Query(ge=-180, le=180)] = 54.3773,
):
    try:
        return fetch_context(latitude, longitude)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(
            502, "Weather context is unavailable; planning evidence is unchanged"
        ) from None
