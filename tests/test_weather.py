"""Environmental context preserves the boundary between a forecast and WBGT."""

import httpx
import pytest

from heatshield import weather


def test_weather_adapter_returns_screening_context_without_admitting_work(monkeypatch):
    original = httpx.Client

    def provider(request):
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["timezone"] == "UTC"
        return httpx.Response(
            200,
            json={
                "hourly_units": {"temperature_2m": "°C"},
                "hourly": {
                    "time": ["2026-11-10T03:00"],
                    "temperature_2m": [31.0],
                    "relative_humidity_2m": [65],
                    "wind_speed_10m": [5.0],
                    "shortwave_radiation": [600.0],
                },
            },
        )

    monkeypatch.setattr(
        weather.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(provider), **kw),
    )
    result = weather.fetch_context(24.45, 54.37)
    assert result["classification"] == "screening_context"
    assert result["wbgt_c"] is None
    assert result["can_admit_heat_work"] is False
    assert result["hourly"][0]["time"].endswith("Z")
    assert result["hourly"][0]["temperature_2m"] == 31.0


def test_weather_outage_does_not_fabricate_context(monkeypatch):
    original = httpx.Client

    def unavailable(request):
        raise httpx.ConnectError("provider offline", request=request)

    monkeypatch.setattr(
        weather.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(unavailable), **kw),
    )
    with pytest.raises(httpx.ConnectError):
        weather.fetch_context(24.45, 54.37)
