import asyncio
from datetime import date, timedelta

import httpx
import pytest

from tools.weather_tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    MockWeatherProvider,
    OpenMeteoWeatherProvider,
    WeatherAPIError,
    WeatherDateUnavailableError,
    WeatherLocationNotFoundError,
    get_weather,
)


@pytest.fixture
def weather_location():
    """Stable test input that is intentionally independent from main.py."""

    return "Madison, WI"


@pytest.fixture
def mock_date():
    return date(2026, 8, 7)


@pytest.fixture
def forecast_date():
    return date.today().isoformat()


@pytest.fixture
def resolved_place():
    return {
        "name": "Madison",
        "admin1": "Wisconsin",
        "country": "United States",
        "latitude": 43.0731,
        "longitude": -89.4012,
        "timezone": "America/Chicago",
    }


def get_mock_weather(location, target_date):
    return asyncio.run(
        get_weather(
            location,
            target_date,
            provider=MockWeatherProvider(),
        )
    )


def test_weather_returns_deterministic_mock_forecast(
    weather_location,
    mock_date,
):
    first = get_mock_weather(weather_location, mock_date)
    second = get_mock_weather(weather_location, mock_date)

    assert first == second
    assert first["source"] == "mock"
    assert first["location"] == weather_location
    assert first["date"] == mock_date.isoformat()


def test_weather_schema_and_ranges_are_valid(weather_location, mock_date):
    forecast = get_mock_weather(weather_location, mock_date)

    assert set(forecast) == {
        "location",
        "date",
        "condition",
        "temperature_low_c",
        "temperature_high_c",
        "precipitation_probability",
        "wind_kph",
        "planning_advice",
        "source",
    }
    assert forecast["temperature_low_c"] < forecast["temperature_high_c"]
    assert 0 <= forecast["precipitation_probability"] <= 100
    assert forecast["wind_kph"] >= 0


def test_weather_result_is_a_defensive_copy(weather_location, mock_date):
    first = get_mock_weather(weather_location, mock_date)
    first["condition"] = "Changed locally"

    second = get_mock_weather(weather_location, mock_date)
    assert second["condition"] != "Changed locally"


@pytest.mark.parametrize("invalid_location", ["", "   ", None])
def test_weather_rejects_blank_locations(invalid_location):
    with pytest.raises(ValueError):
        get_mock_weather(invalid_location, "2026-08-07")


@pytest.mark.parametrize("invalid_date", ["", "08/07/2026", "tomorrow"])
def test_weather_rejects_invalid_dates(invalid_date, weather_location):
    with pytest.raises(ValueError):
        get_mock_weather(weather_location, invalid_date)


def test_open_meteo_fetches_geocoding_and_daily_forecast(
    weather_location,
    forecast_date,
    resolved_place,
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [resolved_place]
                },
            )
        return httpx.Response(
            200,
            json={
                "daily": {
                    "time": [forecast_date],
                    "weather_code": [3],
                    "temperature_2m_max": [31.1],
                    "temperature_2m_min": [20.4],
                    "precipitation_probability_max": [6],
                    "wind_speed_10m_max": [15.0],
                }
            },
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenMeteoWeatherProvider(http_client=client)
        try:
            return await get_weather(
                weather_location,
                forecast_date,
                provider=provider,
            )
        finally:
            await client.aclose()

    forecast = asyncio.run(scenario())

    assert forecast == {
        "location": "Madison, Wisconsin, United States",
        "date": forecast_date,
        "condition": "Overcast",
        "temperature_low_c": 20.4,
        "temperature_high_c": 31.1,
        "precipitation_probability": 6,
        "wind_kph": 15.0,
        "planning_advice": (
            "Conditions are suitable for normal plans; keep a small time buffer."
        ),
        "source": "open-meteo",
    }
    assert len(requests) == 2
    assert requests[0].url.params["name"] == weather_location
    assert requests[1].url.params["timezone"] == "America/Chicago"
    assert requests[1].url.params["start_date"] == forecast_date
    assert requests[1].url.params["end_date"] == forecast_date
    assert requests[1].url.params["temperature_unit"] == "celsius"
    assert requests[1].url.params["wind_speed_unit"] == "kmh"


def test_open_meteo_reports_unknown_location(forecast_date):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenMeteoWeatherProvider(http_client=client)
        try:
            with pytest.raises(WeatherLocationNotFoundError, match="could not resolve"):
                await get_weather(
                    "Unknown place",
                    forecast_date,
                    provider=provider,
                )
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_open_meteo_surfaces_provider_error_reason(
    weather_location,
    forecast_date,
):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": True, "reason": "Invalid request parameters"},
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenMeteoWeatherProvider(http_client=client)
        try:
            with pytest.raises(WeatherAPIError, match="Invalid request parameters"):
                await get_weather(
                    weather_location,
                    forecast_date,
                    provider=provider,
                )
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_open_meteo_rejects_dates_outside_forecast_window(weather_location):
    unavailable_date = date.today() + timedelta(days=16)
    provider = OpenMeteoWeatherProvider()

    with pytest.raises(WeatherDateUnavailableError, match="forecast dates"):
        asyncio.run(
            get_weather(
                weather_location,
                unavailable_date,
                provider=provider,
            )
        )


def test_weather_tool_metadata_is_present():
    assert TOOL_NAME == "get_weather"
    assert TOOL_DESCRIPTION.strip()
