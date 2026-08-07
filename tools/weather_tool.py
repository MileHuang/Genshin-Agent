"""Async real and mock weather providers for planning tools."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Protocol, TypedDict

import httpx

from config.settings import (
    OPEN_METEO_FORECAST_URL,
    OPEN_METEO_GEOCODING_URL,
    WEATHER_PROVIDER,
    WEATHER_TIMEOUT_SECONDS,
)


TOOL_NAME = "get_weather"
TOOL_DESCRIPTION = (
    "Resolve a location and return a daily weather forecast. Open-Meteo is the "
    "default live provider; a deterministic mock provider remains available "
    "for offline tests."
)


class WeatherForecast(TypedDict):
    location: str
    date: str
    condition: str
    temperature_low_c: float
    temperature_high_c: float
    precipitation_probability: int
    wind_kph: float
    planning_advice: str
    source: str


class WeatherProvider(Protocol):
    """Interface implemented by mock and live async providers."""

    async def get_forecast(
        self,
        location: str,
        target_date: str,
    ) -> WeatherForecast: ...


class WeatherAPIError(RuntimeError):
    """Base exception for live weather-provider failures."""


class WeatherLocationNotFoundError(WeatherAPIError):
    """Raised when the provider cannot resolve a requested location."""


class WeatherDateUnavailableError(WeatherAPIError):
    """Raised when a requested date is outside provider availability."""


class OpenMeteoWeatherProvider:
    """Fetch real daily forecasts from Open-Meteo without an API key."""

    _daily_fields = (
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_probability_max",
        "wind_speed_10m_max",
    )

    def __init__(
        self,
        *,
        geocoding_url: str = OPEN_METEO_GEOCODING_URL,
        forecast_url: str = OPEN_METEO_FORECAST_URL,
        timeout_seconds: float = WEATHER_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.geocoding_url = geocoding_url
        self.forecast_url = forecast_url
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def get_forecast(
        self,
        location: str,
        target_date: str,
    ) -> WeatherForecast:
        clean_location = _normalise_location(location)
        clean_date = _normalise_date(target_date)
        _validate_forecast_window(clean_date)

        if self._http_client is not None:
            return await self._fetch_forecast(
                self._http_client,
                clean_location,
                clean_date,
            )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds)
            ) as client:
                return await self._fetch_forecast(
                    client,
                    clean_location,
                    clean_date,
                )
        except httpx.HTTPError as exc:
            raise WeatherAPIError(f"Open-Meteo request failed: {exc}") from exc

    async def _fetch_forecast(
        self,
        client: httpx.AsyncClient,
        location: str,
        target_date: str,
    ) -> WeatherForecast:
        place = await self._resolve_location(client, location)
        latitude = _required_float(place, "latitude", "geocoding result")
        longitude = _required_float(place, "longitude", "geocoding result")
        timezone = str(place.get("timezone") or "auto")

        forecast_data = await self._get_json(
            client,
            self.forecast_url,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": ",".join(self._daily_fields),
                "timezone": timezone,
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "start_date": target_date,
                "end_date": target_date,
            },
        )
        daily = forecast_data.get("daily")
        if not isinstance(daily, dict):
            raise WeatherAPIError("Open-Meteo response is missing daily data")

        times = daily.get("time")
        if not isinstance(times, list) or target_date not in times:
            raise WeatherDateUnavailableError(
                f"No Open-Meteo forecast is available for {target_date}"
            )
        index = times.index(target_date)

        weather_code = int(_daily_value(daily, "weather_code", index))
        temperature_high = _daily_value(daily, "temperature_2m_max", index)
        temperature_low = _daily_value(daily, "temperature_2m_min", index)
        precipitation = int(
            round(
                _daily_value(
                    daily,
                    "precipitation_probability_max",
                    index,
                )
            )
        )
        wind = _daily_value(daily, "wind_speed_10m_max", index)
        resolved_location = _format_resolved_location(place, location)

        return {
            "location": resolved_location,
            "date": target_date,
            "condition": _weather_code_to_condition(weather_code),
            "temperature_low_c": round(temperature_low, 1),
            "temperature_high_c": round(temperature_high, 1),
            "precipitation_probability": max(0, min(100, precipitation)),
            "wind_kph": round(wind, 1),
            "planning_advice": _planning_advice(
                weather_code,
                precipitation,
                wind,
                temperature_high,
            ),
            "source": "open-meteo",
        }

    async def _resolve_location(
        self,
        client: httpx.AsyncClient,
        location: str,
    ) -> dict:
        data = await self._get_json(
            client,
            self.geocoding_url,
            params={
                "name": location,
                "count": 1,
                "language": "en",
                "format": "json",
            },
        )
        results = data.get("results")
        if not isinstance(results, list) or not results:
            raise WeatherLocationNotFoundError(
                f"Open-Meteo could not resolve location: {location}"
            )
        place = results[0]
        if not isinstance(place, dict):
            raise WeatherAPIError("Open-Meteo returned an invalid geocoding result")
        return place

    @staticmethod
    async def _get_json(
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, object],
    ) -> dict:
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise WeatherAPIError(f"Open-Meteo request failed: {exc}") from exc

        if response.is_error:
            detail = _open_meteo_error_detail(response)
            raise WeatherAPIError(
                f"Open-Meteo returned HTTP {response.status_code}: {detail}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeatherAPIError("Open-Meteo returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise WeatherAPIError("Open-Meteo returned an invalid response object")
        return payload


class MockWeatherProvider:
    """Generate stable mock forecasts without network access."""

    _conditions = (
        ("Clear", 10, "Good conditions for outdoor activities."),
        ("Partly cloudy", 25, "Outdoor plans are reasonable; bring a light layer."),
        ("Light rain", 65, "Keep a flexible indoor backup and allow travel buffer."),
        ("Windy", 20, "Allow extra travel time and secure loose outdoor items."),
    )

    async def get_forecast(
        self,
        location: str,
        target_date: str,
    ) -> WeatherForecast:
        clean_location = _normalise_location(location)
        clean_date = _normalise_date(target_date)
        seed = sum(ord(character) for character in f"{clean_location}:{clean_date}")
        condition, precipitation, advice = self._conditions[
            seed % len(self._conditions)
        ]
        low = float(8 + seed % 13)
        high = float(low + 6 + seed % 5)

        forecast: WeatherForecast = {
            "location": clean_location,
            "date": clean_date,
            "condition": condition,
            "temperature_low_c": low,
            "temperature_high_c": high,
            "precipitation_probability": precipitation,
            "wind_kph": float(8 + seed % 24),
            "planning_advice": advice,
            "source": "mock",
        }
        return deepcopy(forecast)


def _build_default_provider() -> WeatherProvider:
    if WEATHER_PROVIDER == "open-meteo":
        return OpenMeteoWeatherProvider()
    if WEATHER_PROVIDER == "mock":
        return MockWeatherProvider()
    raise ValueError(
        "WEATHER_PROVIDER must be either 'open-meteo' or 'mock'"
    )


_default_provider = _build_default_provider()


async def get_weather(
    location: str,
    target_date: str | date,
    *,
    provider: WeatherProvider | None = None,
) -> WeatherForecast:
    """Return weather data from the configured async or synchronous provider."""

    clean_location = _normalise_location(location)
    clean_date = _normalise_date(target_date)
    result = await (provider or _default_provider).get_forecast(
        clean_location,
        clean_date,
    )
    return deepcopy(result)


def _normalise_location(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("location must not be blank")
    return value.strip()


def _normalise_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_date must be a non-blank ISO date")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD format") from exc


def _validate_forecast_window(target_date: str) -> None:
    requested_date = date.fromisoformat(target_date)
    today = date.today()
    earliest_date = today - timedelta(days=92)
    latest_date = today + timedelta(days=15)
    if not earliest_date <= requested_date <= latest_date:
        raise WeatherDateUnavailableError(
            "Open-Meteo forecast dates must be between "
            f"{earliest_date.isoformat()} and {latest_date.isoformat()}"
        )


def _required_float(data: dict, key: str, context: str) -> float:
    try:
        return float(data[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherAPIError(f"Open-Meteo {context} is missing {key}") from exc


def _daily_value(daily: dict, field: str, index: int) -> float:
    values = daily.get(field)
    if not isinstance(values, list) or index >= len(values):
        raise WeatherAPIError(f"Open-Meteo daily data is missing {field}")
    try:
        return float(values[index])
    except (TypeError, ValueError) as exc:
        raise WeatherAPIError(
            f"Open-Meteo returned an invalid value for {field}"
        ) from exc


def _format_resolved_location(place: dict, fallback: str) -> str:
    parts: list[str] = []
    for field in ("name", "admin1", "country"):
        value = place.get(field)
        if isinstance(value, str) and value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or fallback


def _open_meteo_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("reason"), str):
            return payload["reason"]
    except ValueError:
        pass
    detail = response.text.strip()
    return detail[:300] if detail else "unknown error"


def _weather_code_to_condition(code: int) -> str:
    if code == 0:
        return "Clear sky"
    if code in {1, 2, 3}:
        return "Partly cloudy" if code < 3 else "Overcast"
    if code in {45, 48}:
        return "Fog"
    if code in {51, 53, 55, 56, 57}:
        return "Drizzle"
    if code in {61, 63, 65, 66, 67}:
        return "Rain"
    if code in {71, 73, 75, 77}:
        return "Snow"
    if code in {80, 81, 82}:
        return "Rain showers"
    if code in {85, 86}:
        return "Snow showers"
    if code in {95, 96, 99}:
        return "Thunderstorm"
    return f"Unknown weather code {code}"


def _planning_advice(
    weather_code: int,
    precipitation_probability: int,
    wind_kph: float,
    temperature_high_c: float,
) -> str:
    if weather_code in {95, 96, 99}:
        return "Move outdoor activities indoors and allow travel delays."
    if precipitation_probability >= 60 or weather_code in {
        61,
        63,
        65,
        66,
        67,
        80,
        81,
        82,
    }:
        return "Keep an indoor backup and allow extra travel time for rain."
    if wind_kph >= 35:
        return "Use caution with exposed outdoor activities because of wind."
    if temperature_high_c >= 32:
        return "Schedule strenuous outdoor activity early and plan hydration."
    if weather_code in {71, 73, 75, 77, 85, 86}:
        return "Allow extra travel time and prepare for snow."
    return "Conditions are suitable for normal plans; keep a small time buffer."
