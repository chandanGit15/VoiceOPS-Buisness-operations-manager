"""Weather tool backed by the Open-Meteo API (real HTTP calls, no key needed)."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolError

GEOCODE_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


def _client() -> httpx.AsyncClient:
    """HTTP client factory (monkeypatched in tests with MockTransport)."""
    return httpx.AsyncClient(timeout=15.0)

WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers",
    81: "heavy rain showers", 82: "violent rain showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "heavy thunderstorm with hail",
}


class GetWeatherArgs(BaseModel):
    city: str = Field(min_length=1, max_length=100)
    days: int = Field(default=1, ge=1, le=7)


class WeatherTool(Tool):
    name = "get_weather"
    description = "Get the current weather forecast for a city."
    agent = "weather"

    class Args(GetWeatherArgs):
        pass

    async def _run(self, args: Args) -> dict[str, Any]:
        try:
            async with _client() as client:
                geo = await client.get(GEOCODE_ENDPOINT, params={
                    "name": args.city, "count": 1, "language": "en", "format": "json",
                })
                geo.raise_for_status()
                results = geo.json().get("results") or []
                if not results:
                    raise ToolError(f"unknown city: {args.city!r}")
                place = results[0]

                forecast = await client.get(FORECAST_ENDPOINT, params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": args.days,
                })
                forecast.raise_for_status()
        except ToolError:
            raise
        except (httpx.HTTPError, KeyError) as exc:
            raise ToolError(f"weather service unavailable: {type(exc).__name__}", retryable=True) from exc

        data = forecast.json().get("daily", {})
        days = []
        for i, code in enumerate(data.get("weather_code", [])[: args.days]):
            days.append({
                "date": data.get("time", [""] * len(data.get("weather_code", [])))[i],
                "condition": WEATHER_CODES.get(code, f"code {code}"),
                "temp_max_c": data.get("temperature_2m_max", [None])[i],
                "temp_min_c": data.get("temperature_2m_min", [None])[i],
                "precipitation_probability_pct": data.get("precipitation_probability_max", [None])[i],
            })

        if not days:
            raise ToolError("weather service returned no forecast data", retryable=True)

        return {
            "city": place.get("name", args.city),
            "country": place.get("country", ""),
            "days": days,
        }
