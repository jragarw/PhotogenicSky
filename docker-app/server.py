#!/usr/bin/env python3
"""Self-hosted Photogenic Sky planner with a compact JSON API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import asin, atan2, cos, degrees, log1p, pi, radians, sin
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import base64
import hmac
import json
import logging
import os
import re
import uuid

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
LOCATIONS_FILE = DATA_DIR / "locations.json"
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "14400"))
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "1.0.0"
PORT = int(os.getenv("PORT", "8080"))
LOG = logging.getLogger("photogenic_sky")

DAILY = ",".join((
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "precipitation_probability_max", "precipitation_sum", "wind_speed_10m_max",
    "cloud_cover_mean", "sunrise", "sunset", "uv_index_max", "moonrise",
    "moonset", "moon_phase", "daylight_duration",
))
HOURLY = ",".join((
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "precipitation_probability", "precipitation", "wind_speed_10m",
))
WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog",
    48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    56: "Freezing drizzle", 57: "Dense freezing drizzle", 61: "Light rain", 63: "Rain",
    65: "Heavy rain", 66: "Freezing rain", 67: "Heavy freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
    81: "Rain showers", 82: "Heavy rain showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}
DEFAULT_PREFERENCES = {"photo_light": 45, "photo_sky": 30, "photo_weather": 25,
                       "bird_evidence": 50, "bird_light": 25, "bird_weather": 25,
                       "astro_darkness": 55, "astro_sky": 35, "astro_moon": 10,
                       "sky_style": "dramatic"}


def normalize_preferences(value: dict | None) -> dict:
    prefs = {**DEFAULT_PREFERENCES, **(value or {})}
    for field in DEFAULT_PREFERENCES.keys() - {"sky_style"}:
        try:
            prefs[field] = max(0, min(100, int(prefs.get(field, DEFAULT_PREFERENCES[field]))))
        except (ValueError, TypeError):
            prefs[field] = DEFAULT_PREFERENCES[field]
    if not any(prefs[field] for field in ("photo_light", "photo_sky", "photo_weather")):
        prefs["photo_light"] = 1
    if not any(prefs[field] for field in ("bird_evidence", "bird_light", "bird_weather")):
        prefs["bird_evidence"] = 1
    if not any(prefs[field] for field in ("astro_darkness", "astro_sky", "astro_moon")):
        prefs["astro_darkness"] = 1
        prefs[fields[0]] += 100 - sum(prefs[field] for field in fields)
    if prefs.get("sky_style") not in {"clear", "dramatic"}:
        prefs["sky_style"] = "dramatic"
    return prefs


def weighted_score(parts: dict[str, float], preferences: dict, fields: dict[str, str]) -> tuple[int, dict[str, float]]:
    """Apply saved 0–100 importance settings as relative weights."""
    weights = {part: preferences[field] for part, field in fields.items()}
    total = sum(weights.values()) or 1
    contributions = {part: round(weights[part] / total * value, 1) for part, value in parts.items()}
    return round(sum(contributions.values())), contributions


def http_json(url: str, params: dict | None = None, timeout: int = 18):
    """Fetch a public JSON API with an identifying user agent and bounded timeout."""
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    request = Request(url, headers={"User-Agent": "PhotogenicSky/3.0 (self-hosted photo planning app)"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise ProviderError(f"Provider returned HTTP {err.code}: {detail}") from err
    except (URLError, TimeoutError, json.JSONDecodeError) as err:
        raise ProviderError(str(err)) from err


class ProviderError(Exception):
    pass


def location_timezone_name(lat: float, lon: float) -> str:
    """Open-Meteo supplies the timezone; use UTC until its first response."""
    return "UTC"


def geocode(query: str) -> list[dict]:
    if not query.strip():
        return []
    data = http_json("https://geocoding-api.open-meteo.com/v1/search", {
        "name": query.strip(), "count": 6, "language": "en", "format": "json",
    })
    return [{
        "name": item.get("name"), "admin1": item.get("admin1"),
        "country": item.get("country"), "latitude": item["latitude"],
        "longitude": item["longitude"], "timezone": item.get("timezone"),
    } for item in data.get("results", [])]


def fetch_weather(location: dict) -> dict:
    payload = http_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": location["latitude"], "longitude": location["longitude"],
        "daily": DAILY, "hourly": HOURLY,
        "current": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
        "timezone": "auto", "forecast_days": 14,
    })
    daily, hourly = payload.get("daily", {}), payload.get("hourly", {})
    if not daily.get("time"):
        raise ProviderError("Open-Meteo returned no forecast days")
    days = []
    for i, day in enumerate(daily["time"]):
        days.append({
            "date": day,
            "condition": WEATHER_CODES.get(at(daily, "weather_code", i), "Unknown"),
            "weather_code": at(daily, "weather_code", i),
            "temperature_max_c": at(daily, "temperature_2m_max", i),
            "temperature_min_c": at(daily, "temperature_2m_min", i),
            "precipitation_probability_max": at(daily, "precipitation_probability_max", i),
            "precipitation_mm": at(daily, "precipitation_sum", i),
            "wind_speed_kmh": at(daily, "wind_speed_10m_max", i),
            "cloud_cover_mean": at(daily, "cloud_cover_mean", i),
            "uv_index_max": at(daily, "uv_index_max", i),
            "sunrise": at(daily, "sunrise", i), "sunset": at(daily, "sunset", i),
            "moonrise": at(daily, "moonrise", i), "moonset": at(daily, "moonset", i),
            "moon_phase": at(daily, "moon_phase", i),
            "daylight_duration_seconds": at(daily, "daylight_duration", i),
        })
    return {
        "status": "available", "source": "Open-Meteo",
        "timezone": payload.get("timezone", "UTC"), "days": days,
        "hourly": hourly, "current": payload.get("current", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def at(data: dict, key: str, index: int):
    values = data.get(key) or []
    return values[index] if index < len(values) else None


def fetch_birds(location: dict, timezone_name: str) -> dict:
    """Collect matching-date historical GBIF bird evidence for the next 7/14 days."""
    today = datetime.now(ZoneInfo(timezone_name)).date()
    days = location.get("lookahead_days", 7)
    target_end = today + timedelta(days=days - 1)
    polygon = radius_polygon(location["latitude"], location["longitude"], location.get("radius_km", 25))

    def query_year(offset: int):
        start = shift_year(today, today.year - offset)
        end = shift_year(target_end, target_end.year - offset)
        return http_json("https://api.gbif.org/v1/occurrence/search", {
            "taxonKey": 212, "geometry": polygon,
            "eventDate": f"{start.isoformat()},{end.isoformat()}",
            "facet": "speciesKey", "facetLimit": 500, "limit": 0,
        })

    with ThreadPoolExecutor(max_workers=5) as pool:
        payloads = list(pool.map(query_year, range(1, 6)))
    records: dict[str, dict] = {}
    years_with_data = 0
    for payload in payloads:
        facets = payload.get("facets", [])
        counts = facets[0].get("counts", []) if facets else []
        years_with_data += bool(counts)
        for species in counts:
            key = species.get("name")
            if key is None:
                continue
            rec = records.setdefault(key, {"taxon_key": key, "records": 0, "years_reported": 0})
            rec["records"] += species.get("count", 0)
            rec["years_reported"] += 1
    ranked = sorted(records.values(), key=lambda item: (-item["years_reported"], -item["records"]))
    shown = ranked[:12]
    with ThreadPoolExecutor(max_workers=8) as pool:
        details = list(pool.map(lambda species: http_json(
            f"https://api.gbif.org/v1/species/{species['taxon_key']}"), shown))
    for species, detail in zip(shown, details):
        species["scientific_name"] = detail.get("scientificName")
        species["common_name"] = detail.get("vernacularName")
    return {
        "status": "seasonal_records" if shown else "no_records",
        "source": "GBIF historical occurrence records", "window_days": days,
        "radius_km": location.get("radius_km", 25), "species_count": len(records),
        "species_count_may_be_capped": len(records) >= 500,
        "recurring_species_count": sum(item["years_reported"] >= 3 for item in records.values()),
        "total_historical_records": sum(item["records"] for item in records.values()),
        "years_searched": 5, "years_with_records": years_with_data,
        "species": shown,
        "note": "Historical observations for matching calendar dates in the preceding five years; this is evidence, not a forecast or live count.",
    }


def shift_year(value: date, year: int) -> date:
    try:
        return value.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def radius_polygon(latitude: float, longitude: float, radius_km: float) -> str:
    centre_lat, centre_lon = radians(latitude), radians(longitude)
    angular = radius_km / 6371
    points = []
    for bearing_deg in range(0, 360, 15):
        bearing = radians(bearing_deg)
        target_lat = asin(sin(centre_lat) * cos(angular) + cos(centre_lat) * sin(angular) * cos(bearing))
        target_lon = centre_lon + atan2(sin(bearing) * sin(angular) * cos(centre_lat), cos(angular) - sin(centre_lat) * sin(target_lat))
        points.append(f"{degrees(target_lon):.5f} {degrees(target_lat):.5f}")
    points.append(points[0])
    return f"POLYGON(({','.join(points)}))"


def distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    dlat, dlon = radians(b_lat - a_lat), radians(b_lon - a_lon)
    arc = sin(dlat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(min(1, arc ** .5))


def fetch_tides(location: dict, timezone_name: str) -> dict:
    """Get 30 days of harmonic tide extremes, falling back to NOAA where covered."""
    local_zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(datetime.now(local_zone).date(), time.min, local_zone)
    start = local_start.astimezone(timezone.utc)
    end = start + timedelta(days=30)
    try:
        payload = http_json("https://api.openwaters.io/tides/extremes", {
            "latitude": location["latitude"], "longitude": location["longitude"],
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"), "units": "meters",
        })
        station = payload.get("station", {})
        distance = payload.get("distance")
        events = [{
            "time": item["time"], "height_m": item.get("level"),
            "type": "high" if item.get("high") else "low",
        } for item in payload.get("extremes", []) if item.get("time")]
        if events and (distance is None or float(distance) <= 100):
            return {
                "status": "available", "source": "Open Waters (TICON-4)",
                "station": station.get("name"), "station_id": station.get("id"),
                "distance_km": distance, "datum": payload.get("datum"),
                "units": payload.get("units", "meters"),
                "timezone": station.get("timezone", timezone_name), "events": events,
                "attribution": "Open Waters tide predictions, CC BY 4.0",
                "note": station.get("disclaimers") or "Harmonic predictions; verify local datum and suitability. Not for navigation.",
            }
        openwaters_error = (f"Nearest Open Waters station is {float(distance):.1f} km away" if distance is not None
                            else "No Open Waters prediction events for this location")
    except (ProviderError, ValueError, TypeError) as err:
        openwaters_error = str(err) or "Open Waters returned unusable tide data"
    return fetch_noaa_tides(location, timezone_name, openwaters_error)


def fetch_noaa_tides(location: dict, timezone_name: str, previous_error: str) -> dict:
    try:
        stations = http_json("https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json", {"type": "tidepredictions"})
        candidates = [item for item in stations.get("stations", []) if item.get("type") in {"S", "R"}]
        if not candidates:
            raise ProviderError("No NOAA prediction stations returned")
        station = min(candidates, key=lambda item: distance_km(
            location["latitude"], location["longitude"], item["lat"], item["lng"]))
        distance = distance_km(location["latitude"], location["longitude"], station["lat"], station["lng"])
        if distance > 100:
            return {"status": "unavailable", "source": "Open Waters / NOAA", "error": previous_error,
                    "detail": f"No NOAA station within 100 km (nearest is {distance:.1f} km)."}
        today = datetime.now(ZoneInfo(timezone_name)).date()
        predictions = http_json("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter", {
            "product": "predictions", "application": "PhotogenicSky",
            "begin_date": today.strftime("%Y%m%d"),
            "end_date": (today + timedelta(days=29)).strftime("%Y%m%d"),
            "datum": "MLLW", "station": station["id"], "time_zone": "gmt",
            "interval": "hilo", "units": "metric", "format": "json",
        })
        events = [{"time": item["t"], "height_m": float(item["v"]),
                   "type": "high" if item["type"] == "H" else "low"}
                  for item in predictions.get("predictions", [])]
        if not events:
            raise ProviderError("NOAA returned no tide events")
        return {"status": "available", "source": "NOAA Tides & Currents",
                "station": station["name"], "station_id": station["id"],
                "distance_km": round(distance, 1), "datum": "MLLW", "units": "meters",
                "timezone": timezone_name, "events": events,
                "note": f"Open Waters unavailable ({previous_error}). NOAA coverage is limited to supported US stations."}
    except ProviderError as err:
        return {"status": "unavailable", "source": "Open Waters / NOAA",
                "error": f"{previous_error}; NOAA fallback: {err}", "events": []}


def solar_elevation(local_time: datetime, latitude: float, longitude: float) -> float:
    """Approximate solar elevation from NOAA fractional-year equations."""
    day = local_time.timetuple().tm_yday
    hour = local_time.hour + local_time.minute / 60 + local_time.second / 3600
    gamma = 2 * pi / 365 * (day - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * cos(gamma) - 0.032077 * sin(gamma)
                       - 0.014615 * cos(2*gamma) - 0.040849 * sin(2*gamma))
    decl = (0.006918 - 0.399912*cos(gamma) + 0.070257*sin(gamma)
            - 0.006758*cos(2*gamma) + 0.000907*sin(2*gamma)
            - 0.002697*cos(3*gamma) + 0.00148*sin(3*gamma))
    offset = local_time.utcoffset()
    offset_minutes = offset.total_seconds() / 60 if offset else 0
    true_solar_minutes = hour * 60 + eqtime + 4 * longitude - offset_minutes
    hour_angle = radians(true_solar_minutes / 4 - 180)
    lat = radians(latitude)
    return degrees(asin(sin(lat) * sin(decl) + cos(lat) * cos(decl) * cos(hour_angle)))


def solar_phase(elevation: float) -> str:
    if elevation > 6:
        return "daylight"
    if elevation > -0.833:
        return "golden hour"
    if elevation > -6:
        return "blue hour"
    if elevation > -12:
        return "nautical twilight"
    if elevation > -18:
        return "astronomical twilight"
    return "astronomical night"


def moon_illumination(phase: float | None) -> float | None:
    if phase is None:
        return None
    return round((1 - abs(2 * phase - 1)) * 100)


def moon_is_above_horizon(day: dict, moment: datetime | None, timezone_name: str) -> bool | None:
    """Estimate whether the Moon is up at an opportunity using local rise/set times."""
    if moment is None:
        return None
    try:
        zone = ZoneInfo(timezone_name)
        when = moment if moment.tzinfo else moment.replace(tzinfo=zone)
        local_when = when.astimezone(zone)
        day_date = date.fromisoformat(day["date"])
        rise = datetime.fromisoformat(day["moonrise"]) if day.get("moonrise") else None
        setting = datetime.fromisoformat(day["moonset"]) if day.get("moonset") else None
        if rise and rise.tzinfo is None:
            rise = rise.replace(tzinfo=zone)
        if setting and setting.tzinfo is None:
            setting = setting.replace(tzinfo=zone)
        # If both events are reported, compare to their actual instants.
        if rise and setting:
            rise_local, set_local = rise.astimezone(zone), setting.astimezone(zone)
            if rise_local <= set_local:
                return rise_local <= local_when <= set_local
            return local_when >= rise_local or local_when <= set_local
        # A missing event can mean the Moon remains up/down for this whole local date.
        if rise and rise.astimezone(zone).date() == day_date:
            return local_when >= rise.astimezone(zone)
        if setting and setting.astimezone(zone).date() == day_date:
            return local_when <= setting.astimezone(zone)
    except (ValueError, KeyError, TypeError):
        return None
    return None


def nearest_hour(hourly: dict, target: str | datetime) -> dict:
    target_dt = datetime.fromisoformat(target) if isinstance(target, str) else target
    times = hourly.get("time") or []
    target_compare = target_dt.replace(tzinfo=None) if target_dt.tzinfo else target_dt
    same_day = []
    for i, value in enumerate(times):
        try:
            moment = datetime.fromisoformat(value)
            moment_compare = moment.replace(tzinfo=None) if moment.tzinfo else moment
            if moment_compare.date() == target_compare.date():
                same_day.append((abs((moment_compare - target_compare).total_seconds()), i))
        except ValueError:
            continue
    if not same_day:
        return {}
    index = min(same_day)[1]
    return {key: at(hourly, source, index) for key, source in {
        "time": "time", "cloud_cover": "cloud_cover", "cloud_cover_low": "cloud_cover_low",
        "cloud_cover_mid": "cloud_cover_mid", "cloud_cover_high": "cloud_cover_high",
        "precipitation_probability": "precipitation_probability", "precipitation_mm": "precipitation",
        "wind_speed_kmh": "wind_speed_10m",
    }.items()}


def light_breakdown(day: dict, window: str, conditions: dict) -> dict:
    cloud = conditions.get("cloud_cover")
    if cloud is None:
        cloud = day.get("cloud_cover_mean") if day.get("cloud_cover_mean") is not None else 50
    low = conditions.get("cloud_cover_low") or 0
    high = conditions.get("cloud_cover_high") or 0
    rain = conditions.get("precipitation_probability")
    if rain is None:
        rain = day.get("precipitation_probability_max")
    if rain is None:
        rain = 20
    base = 90 if window in {"sunrise", "sunset"} else 25 if window == "midday" else 0
    wind = conditions.get("wind_speed_kmh")
    if wind is None:
        wind = day.get("wind_speed_kmh") or 0
    if window == "midday":
        cloud_adjustment = min(cloud, 100) * .25
    elif window == "night":
        cloud_adjustment = 0
    else:
        cloud_adjustment = min(high, 100) * .12 - min(low, 100) * .35
    rain_penalty = round(rain * .25, 1)
    wind_penalty = round(min(wind, 80) * .15, 1)
    score = max(0, min(100, round(base + cloud_adjustment - rain_penalty - wind_penalty)))
    return {
        "score": score, "window": window, "base": base,
        "cloud_adjustment": round(cloud_adjustment, 1),
        "low_cloud_percent": low, "high_cloud_percent": high,
        "rain_probability_percent": rain, "rain_penalty": rain_penalty,
        "wind_speed_kmh": wind, "wind_penalty": wind_penalty,
        "formula": "window base + cloud adjustment - 0.25 × rain probability - 0.15 × capped wind speed",
    }


def weather_breakdown(day: dict, conditions: dict) -> dict:
    rain = conditions.get("precipitation_probability")
    if rain is None:
        rain = day.get("precipitation_probability_max")
    rain = 25 if rain is None else rain
    wind = conditions.get("wind_speed_kmh")
    if wind is None:
        wind = day.get("wind_speed_kmh") or 0
    cloud = conditions.get("cloud_cover")
    if cloud is None:
        cloud = day.get("cloud_cover_mean")
    cloud = 50 if cloud is None else cloud
    parts = {"rain": round(.45 * max(0, 100-rain), 1),
             "wind": round(.30 * max(0, 100-min(wind, 100)), 1),
             "cloud": round(.25 * max(0, 100-cloud), 1)}
    return {"score": round(sum(parts.values())), "rain_probability_percent": rain,
            "wind_speed_kmh": wind, "cloud_cover_percent": cloud,
            "contributions": parts,
            "formula": "45% low rain probability + 30% low wind + 25% low cloud cover"}


def photo_breakdown(day: dict, light: dict, conditions: dict, night: bool, preferences: dict | None = None) -> dict:
    cloud = conditions.get("cloud_cover")
    if cloud is None:
        cloud = day.get("cloud_cover_mean")
    cloud = 50 if cloud is None else cloud
    rain = conditions.get("precipitation_probability")
    if rain is None:
        rain = day.get("precipitation_probability_max")
    rain = 20 if rain is None else rain
    prefs = normalize_preferences(preferences)
    sky_score = (100 - cloud) if prefs["sky_style"] == "clear" else min(100, round(100 - abs(cloud - 55) * 1.8))
    score, values = weighted_score({"light": light["score"], "sky": sky_score, "weather": 100-rain}, prefs,
                                   {"light": "photo_light", "sky": "photo_sky", "weather": "photo_weather"})
    score = 0 if night else score
    return {"score": score, "formula": f"{prefs['photo_light']}% light + {prefs['photo_sky']}% {prefs['sky_style']} sky + {prefs['photo_weather']}% dry weather",
            "cloud_cover_percent": cloud, "rain_probability_percent": rain,
            "sky_score": sky_score, "preferences": prefs, "contributions": values,
            "night_gate": "Sun is below -6°; daylight photography is inactive." if night else None}


def birding_score(birds: dict | None, light: dict, weather: dict, preferences: dict | None = None) -> dict:
    if not birds or not birds.get("species"):
        return {"score": None, "reason": "No historical seasonal species evidence is available for this region and date window."}
    count = birds.get("species_count", len(birds["species"]))
    years = birds.get("years_with_records", 0)
    richness = min(100, round(100 * log1p(count) / log1p(500)))
    coverage = min(100, round(years / 5 * 100))
    seasonal = round(.7 * richness + .3 * coverage)
    prefs = normalize_preferences(preferences)
    score, parts = weighted_score({"seasonal_species_evidence": seasonal,
                                   "light_quality": light["score"],
                                   "weather_suitability": weather["score"]}, prefs,
                                  {"seasonal_species_evidence": "bird_evidence", "light_quality": "bird_light",
                                   "weather_suitability": "bird_weather"})
    return {"score": score, "seasonal_score": seasonal,
            "richness_score": richness, "historical_coverage_score": coverage,
            "contributions": parts,
            "preferences": prefs, "formula": f"{prefs['bird_evidence']}% regional seasonal evidence + {prefs['bird_light']}% light + {prefs['bird_weather']}% weather"}


def astro_score(elevation: float, day: dict, conditions: dict, preferences: dict | None = None,
                moment: datetime | None = None, timezone_name: str = "UTC") -> dict:
    cloud = conditions.get("cloud_cover")
    if cloud is None:
        cloud = day.get("cloud_cover_mean")
    cloud = 50 if cloud is None else cloud
    darkness = max(0, min(100, round((-elevation - 6) / 12 * 100)))
    illumination = moon_illumination(day.get("moon_phase"))
    moon_visible = moon_is_above_horizon(day, moment, timezone_name)
    moon_penalty = (illumination if illumination is not None else 50) if moon_visible is not False else 0
    moon_dark = 100 - moon_penalty
    prefs = normalize_preferences(preferences)
    score, contributions = weighted_score({"darkness": darkness, "clear_sky": 100-cloud,
                                            "low_moon_illumination": moon_dark}, prefs,
                                           {"darkness": "astro_darkness", "clear_sky": "astro_sky",
                                            "low_moon_illumination": "astro_moon"})
    return {"score": score, "solar_elevation_degrees": round(elevation, 1),
            "darkness_score": darkness, "cloud_cover_percent": cloud,
            "moon_illumination_percent": illumination, "moon_above_horizon": moon_visible,
            "moon_penalty_percent": moon_penalty,
            "contributions": contributions, "preferences": prefs,
            "formula": f"{prefs['astro_darkness']}% darkness + {prefs['astro_sky']}% clear sky + {prefs['astro_moon']}% low moonlight"}


def build_opportunities(weather: dict, birds: dict | None, location: dict) -> list[dict]:
    tz = ZoneInfo(weather["timezone"])
    opportunities = []
    for day in weather["days"]:
        windows: list[tuple[str, str]] = [(name, day[name]) for name in ("sunrise", "sunset") if day.get(name)]
        if day.get("sunrise") and day.get("sunset"):
            rise, setting = datetime.fromisoformat(day["sunrise"]), datetime.fromisoformat(day["sunset"])
            windows.append(("midday", (rise + (setting-rise)/2).isoformat()))
            for offset in (1, 2, 3, 4):
                windows.append(("night", (setting + timedelta(hours=offset)).isoformat()))
        elif not day.get("sunrise") and not day.get("sunset"):
            # Polar night can still have astro opportunities even when the API
            # has no sunrise/sunset event for that local date.
            night_time = datetime.combine(date.fromisoformat(day["date"]), time(0, 0)).isoformat()
            windows.append(("night", night_time))
        for name, local_time in windows:
            moment = datetime.fromisoformat(local_time)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=tz)
            if moment < datetime.now(tz):
                continue
            elevation = solar_elevation(moment, location["latitude"], location["longitude"])
            night = elevation < -6
            data_window = "night" if name == "night" else name
            conditions = nearest_hour(weather["hourly"], moment)
            light = light_breakdown(day, data_window, conditions)
            suitable = weather_breakdown(day, conditions)
            preferences = location.get("preferences")
            photo = photo_breakdown(day, light, conditions, night, preferences)
            bird = birding_score(birds, light, suitable, preferences) if not night else {
                "score": None, "reason": "Birding score is inactive after blue hour."}
            astro = astro_score(elevation, day, conditions, preferences, moment, weather["timezone"]) if name == "night" and night else None
            opportunities.append({
                "date": day["date"], "window": name, "time": local_time,
                "solar_elevation_degrees": round(elevation, 1), "condition": day["condition"],
                "temperature_max_c": day["temperature_max_c"],
                "cloud_cover_percent": conditions.get("cloud_cover", day.get("cloud_cover_mean")),
                "low_cloud_percent": conditions.get("cloud_cover_low"),
                "mid_cloud_percent": conditions.get("cloud_cover_mid"),
                "high_cloud_percent": conditions.get("cloud_cover_high"),
                "rain_probability_percent": conditions.get("precipitation_probability", day.get("precipitation_probability_max")),
                "wind_speed_kmh": conditions.get("wind_speed_kmh", day.get("wind_speed_kmh")),
                "light": light, "weather_suitability": suitable, "photogenic": photo,
                "birding": bird, "astro": astro,
                "summary": explain_opportunity(name, photo, bird, astro, light, conditions),
            })
    return opportunities


def explain_opportunity(name: str, photo: dict, bird: dict, astro: dict | None,
                        light: dict, conditions: dict) -> str:
    if astro:
        return (f"Astro score {astro['score']}/100 at {name}; sun is {astro['solar_elevation_degrees']}° below the horizon, "
                f"cloud cover is {astro['cloud_cover_percent']}%, and moon illumination is {astro['moon_illumination_percent']}%.")
    if photo["score"] == 0 and photo["night_gate"]:
        return "Sun is below the end of blue hour: daylight photo score is 0 and astro is the applicable score."
    cloud = conditions.get("cloud_cover")
    rain = conditions.get("precipitation_probability")
    return (f"Photogenic score {photo['score']}/100: light {light['score']}/100, "
            f"cloud {cloud if cloud is not None else 'forecast mean'}%, "
            f"rain chance {rain if rain is not None else 'daily forecast'}%. "
            f"Birding {bird['score'] if bird['score'] is not None else 'unavailable'}.")


def current_lighting(weather: dict, location: dict) -> dict:
    now = datetime.now(ZoneInfo(weather["timezone"]))
    elevation = solar_elevation(now, location["latitude"], location["longitude"])
    current = weather.get("current", {})
    return {
        "phase": solar_phase(elevation), "solar_elevation_degrees": round(elevation, 1),
        "observed_at": now.isoformat(), "cloud_cover_percent": current.get("cloud_cover"),
        "low_cloud_percent": current.get("cloud_cover_low"),
        "mid_cloud_percent": current.get("cloud_cover_mid"),
        "high_cloud_percent": current.get("cloud_cover_high"),
    }


def tide_calendar(tides: dict, timezone_name: str) -> list[dict]:
    zone = ZoneInfo(timezone_name)
    grouped: dict[str, list[dict]] = {}
    for event in tides.get("events", []):
        moment = parse_tide_datetime(event["time"]).astimezone(zone)
        grouped.setdefault(moment.date().isoformat(), []).append({
            "time": moment.isoformat(), "type": event.get("type"), "height_m": event.get("height_m"),
        })
    return [{"date": day, "events": events} for day, events in sorted(grouped.items())]


def tide_context(value: str | None, tides: dict, timezone_name: str) -> dict | None:
    if not value or not tides.get("events"):
        return None
    target = datetime.fromisoformat(value)
    if target.tzinfo is None:
        target = target.replace(tzinfo=ZoneInfo(timezone_name))
    events = []
    for item in tides["events"]:
        moment = parse_tide_datetime(item["time"])
        events.append((abs((moment-target.astimezone(timezone.utc)).total_seconds()), item, moment))
    distance, event, moment = min(events, key=lambda x: x[0])
    if distance > 3*60*60:
        return None
    return {"type": event["type"], "time": moment.astimezone(ZoneInfo(timezone_name)).isoformat(),
            "height_m": event.get("height_m"), "minutes_from_sun_time": round((moment-target.astimezone(timezone.utc)).total_seconds()/60)}


def parse_tide_datetime(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # NOAA predictions are requested in GMT and may omit the trailing Z.
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def solar_noon_time(sunrise: str | None, sunset: str | None) -> str | None:
    if not sunrise or not sunset:
        return None
    rise, setting = datetime.fromisoformat(sunrise), datetime.fromisoformat(sunset)
    return (rise + (setting-rise)/2).isoformat()


class LocationStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, data: list[dict]) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def all(self) -> list[dict]:
        with self.lock:
            locations = self._read()
            for item in locations:
                item["preferences"] = normalize_preferences(item.get("preferences"))
                item["plan"] = [entry for entry in item.get("plan", []) if isinstance(entry, dict)] if isinstance(item.get("plan"), list) else []
            return locations

    def get(self, location_id: str) -> dict | None:
        return next((x for x in self.all() if x["id"] == location_id), None)

    def save(self, payload: dict, location_id: str | None = None) -> dict:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("Give this location a name.")
        if payload.get("place"):
            matches = geocode(str(payload["place"]))
            if not matches:
                raise ValueError("That place was not found. Try a town, postcode, or coordinates.")
            chosen = matches[0]
            lat, lon = float(chosen["latitude"]), float(chosen["longitude"])
            place_name = ", ".join(x for x in (chosen.get("name"), chosen.get("admin1"), chosen.get("country")) if x)
        else:
            if payload.get("latitude") in (None, "") or payload.get("longitude") in (None, ""):
                raise ValueError("Choose a search result or enter both latitude and longitude.")
            lat, lon = float(payload.get("latitude")), float(payload.get("longitude"))
            place_name = str(payload.get("place_name", ""))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Latitude or longitude is outside the valid range.")
        days = int(payload.get("lookahead_days", 7))
        radius = int(payload.get("radius_km", 25))
        if days not in (7, 14):
            raise ValueError("Bird outlook must be 7 or 14 days.")
        if radius not in (10, 25, 50):
            raise ValueError("Bird search radius must be 10, 25, or 50 km.")
        with self.lock:
            locations = self._read()
            previous = next((item for item in locations if item["id"] == location_id), None)
            saved_preferences = (previous or {}).get("preferences")
            preferences = normalize_preferences(payload.get("preferences", saved_preferences))
            # Keep preferences when an older client submits an update without them.
            if "preferences" not in payload and saved_preferences:
                preferences = normalize_preferences(saved_preferences)
            location = {
                "id": location_id or uuid.uuid4().hex[:12], "name": name,
                "place_name": place_name, "latitude": lat, "longitude": lon,
                "lookahead_days": days, "radius_km": radius,
                "shoreline": bool(payload.get("shoreline", False)),
                "preferences": preferences,
                "plan": (previous or {}).get("plan", []) if isinstance((previous or {}).get("plan", []), list) else [],
            }
            existing = next((i for i, item in enumerate(locations) if item["id"] == location["id"]), None)
            if existing is None:
                locations.append(location)
            else:
                locations[existing] = location
            self._write(locations)
        return location

    def add_plan_item(self, location_id: str, payload: dict) -> dict | None:
        item_type = str(payload.get("type", ""))
        if item_type not in {"tide", "alert", "opportunity"}:
            raise ValueError("Plan item type must be tide, alert, or opportunity.")
        title = str(payload.get("title", "")).strip()[:120]
        key = str(payload.get("key", "")).strip()[:180]
        if not title or not key:
            raise ValueError("Plan items need a title and a stable key.")
        item = {"id": uuid.uuid4().hex[:12], "key": key, "type": item_type,
                "title": title, "date": str(payload.get("date", ""))[:20],
                "time": str(payload.get("time", ""))[:40],
                "details": str(payload.get("details", ""))[:300]}
        with self.lock:
            locations = self._read()
            location = next((row for row in locations if row["id"] == location_id), None)
            if not location:
                return None
            plan = [entry for entry in location.get("plan", []) if isinstance(entry, dict)] if isinstance(location.get("plan"), list) else []
            existing = next((entry for entry in plan if entry.get("key") == key), None)
            if existing:
                return location
            location["plan"] = [*plan, item]
            self._write(locations)
            return location

    def remove_plan_item(self, location_id: str, item_id: str) -> dict | None:
        with self.lock:
            locations = self._read()
            location = next((row for row in locations if row["id"] == location_id), None)
            if not location:
                return None
            plan = [entry for entry in location.get("plan", []) if isinstance(entry, dict)] if isinstance(location.get("plan"), list) else []
            location["plan"] = [entry for entry in plan if entry.get("id") != item_id]
            self._write(locations)
            return location

    def delete(self, location_id: str) -> bool:
        with self.lock:
            locations = self._read()
            filtered = [x for x in locations if x["id"] != location_id]
            if len(filtered) == len(locations):
                return False
            self._write(filtered)
            return True


class ReportService:
    def __init__(self, store: LocationStore, cache_seconds: int = CACHE_SECONDS):
        self.store, self.cache_seconds = store, cache_seconds
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = Lock()

    def report(self, location_id: str, refresh: bool = False) -> dict | None:
        location = self.store.get(location_id)
        if not location:
            return None
        import time as monotonic_time
        now_tick = monotonic_time.monotonic()
        with self.lock:
            cached = self.cache.get(location_id)
            if cached and not refresh and now_tick - cached[0] < self.cache_seconds:
                cached[1]["location"] = location
                return cached[1]
        report = self._build(location)
        with self.lock:
            self.cache[location_id] = (now_tick, report)
        return report

    def invalidate(self, location_id: str) -> None:
        with self.lock:
            self.cache.pop(location_id, None)

    def _build(self, location: dict) -> dict:
        providers: dict[str, dict] = {}
        # Independent sources refresh concurrently and expose their own errors.
        try:
            weather = fetch_weather(location)
        except Exception as err:
            weather = {"status": "unavailable", "source": "Open-Meteo", "error": str(err)}
        timezone_name = weather.get("timezone", "UTC")
        with ThreadPoolExecutor(max_workers=2) as pool:
            bf = pool.submit(fetch_birds, location, timezone_name)
            tf = pool.submit(fetch_tides, location, timezone_name) if location.get("shoreline") else None
            try:
                birds = bf.result(timeout=60)
            except Exception as err:
                birds = {"status": "unavailable", "source": "GBIF", "error": str(err), "species": []}
            if tf:
                try:
                    tides = tf.result(timeout=40)
                except Exception as err:
                    tides = {"status": "unavailable", "source": "Open Waters / NOAA", "error": str(err), "events": []}
            else:
                tides = {"status": "disabled", "source": "Open Waters", "events": []}
        providers["weather"], providers["birds"], providers["tides"] = weather, birds, tides
        if weather["status"] != "available":
            return {"location": location, "providers": providers, "bird_outlook": birds, "tides": tides,
                    "timezone": timezone_name, "updated_at": datetime.now(timezone.utc).isoformat(),
                    "error": weather.get("error", "Weather data is unavailable"), "opportunities": [],
                    "tide_calendar": [], "daily_summary": []}
        lighting = current_lighting(weather, location)
        opportunities = build_opportunities(weather, birds, location)
        daytime = [x for x in opportunities if x["photogenic"]["score"] > 0]
        best_photo = max(daytime, key=lambda x: x["photogenic"]["score"], default=None)
        bird_candidates = [x for x in opportunities if x["birding"]["score"] is not None]
        best_bird = max(bird_candidates, key=lambda x: x["birding"]["score"], default=None)
        astro_candidates = [x for x in opportunities if x["astro"]]
        best_astro = max(astro_candidates, key=lambda x: x["astro"]["score"], default=None)
        calendar = tide_calendar(tides, tides.get("timezone", timezone_name))
        tide_heights = [float(event["height_m"]) for event in tides.get("events", [])
                        if event.get("height_m") is not None]
        tides_by_date = {item["date"]: item["events"] for item in calendar}
        opportunities_by_date: dict[str, list[dict]] = {}
        for item in opportunities:
            opportunities_by_date.setdefault(item["date"], []).append(item)
        daily_summary = []
        for day in weather["days"]:
            items = opportunities_by_date.get(day["date"], [])
            photo = max((x for x in items if x["photogenic"]["score"] > 0), key=lambda x: x["photogenic"]["score"], default=None)
            bird = max((x for x in items if x["birding"]["score"] is not None), key=lambda x: x["birding"]["score"], default=None)
            astro = max((x for x in items if x["astro"]), key=lambda x: x["astro"]["score"], default=None)
            daily_summary.append({"date": day["date"], "condition": day["condition"],
                "temperature_max_c": day["temperature_max_c"], "temperature_min_c": day["temperature_min_c"],
                "precipitation_probability_max": day["precipitation_probability_max"], "wind_speed_kmh": day["wind_speed_kmh"],
                "sunrise": day["sunrise"], "sunset": day["sunset"], "solar_noon": solar_noon_time(day["sunrise"], day["sunset"]),
                "moonrise": day["moonrise"], "moonset": day["moonset"], "moon_phase": day["moon_phase"],
                "photo": photo, "birding": bird, "astro": astro, "tides": tides_by_date.get(day["date"], []),
                "bird_evidence": {"species_count": birds.get("species_count", 0), "recurring_species_count": birds.get("recurring_species_count", 0), "status": birds.get("status")}})
        today = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        day_today = next((x for x in weather["days"] if x["date"] == today), weather["days"][0])
        sunrise_tide = tide_context(day_today.get("sunrise"), tides, timezone_name)
        sunset_tide = tide_context(day_today.get("sunset"), tides, timezone_name)
        return {
            "location": location, "providers": providers, "timezone": timezone_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lighting": lighting, "weather_days": weather["days"],
            "current_clouds": weather.get("current", {}),
            "bird_outlook": birds, "tides": tides, "tide_calendar": calendar,
            "tide_range": {"minimum_m": min(tide_heights) if tide_heights else None,
                           "maximum_m": max(tide_heights) if tide_heights else None,
                           "window_days": 30},
            "tide_context_today": {"sunrise": sunrise_tide, "sunset": sunset_tide},
            "opportunities": opportunities,
            "daily_summary": daily_summary,
            "best_photo": best_photo, "best_birding": best_bird, "best_astro": best_astro,
            "scores": {"photogenic": best_photo["photogenic"] if best_photo else None,
                       "birding": best_bird["birding"] if best_bird else None,
                       "astro": best_astro["astro"] if best_astro else None},
        }




STORE = LocationStore(LOCATIONS_FILE)
SERVICE = ReportService(STORE)


def location_tide_alerts(report: dict) -> list[dict]:
    """Find high tides at sunrise or within two hours, and sunset or within three."""
    if not report.get("location", {}).get("shoreline") or report.get("tides", {}).get("status") != "available":
        return []
    timezone_name = report.get("timezone", "UTC")
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)
    daily = report.get("daily_summary", [])[:14]
    high_tides = [event for event in report.get("tides", {}).get("events", []) if event.get("type") == "high"]
    alerts = []
    for day in daily:
        targets = [("sunrise", day.get("sunrise")), ("sunset", day.get("sunset"))]
        for event in high_tides:
            if not event.get("time"):
                continue
            tide_time = parse_tide_datetime(event["time"])
            if tide_time.astimezone(zone) < now:
                continue
            for label, raw_target in targets:
                if not raw_target:
                    continue
                target = datetime.fromisoformat(raw_target)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=zone)
                if target.astimezone(zone) < now:
                    continue
                offset_minutes = (tide_time.astimezone(timezone.utc) - target.astimezone(timezone.utc)).total_seconds() / 60
                window_minutes = 120 if label == "sunrise" else 180
                if abs(offset_minutes) <= window_minutes:
                    relation = "at" if abs(offset_minutes) < 1 else f"{abs(round(offset_minutes))} min {'after' if offset_minutes > 0 else 'before'}"
                    height = f" · {float(event['height_m']):.1f} m" if event.get("height_m") is not None else ""
                    alerts.append({"type": "high_tide_near_sun", "date": day["date"], "time": tide_time.astimezone(zone).isoformat(),
                                   "sun_event": label, "minutes_from_sun_event": round(offset_minutes),
                                   "height_m": event.get("height_m"),
                                   "message": f"High tide {relation} {label}{height}"})
    return alerts


class Handler(BaseHTTPRequestHandler):
    server_version = f"PhotogenicSky/{APP_VERSION}"

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value):
        self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 100_000)
        if length <= 0:
            return {}
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _require_auth(self) -> bool:
        if urlparse(self.path).path == "/api/health":
            return True
        expected_password = os.getenv("APP_PASSWORD", "")
        if not expected_password:
            return True
        expected_user = os.getenv("APP_USERNAME", "photogenic")
        header = self.headers.get("Authorization", "")
        try:
            scheme, encoded = header.split(" ", 1)
            user, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError):
            scheme, user, password = "", "", ""
        if scheme.lower() == "basic" and hmac.compare_digest(user.encode(), expected_user.encode()) and hmac.compare_digest(password.encode(), expected_password.encode()):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Photogenic Sky Planner"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            return self._json(200, {"status": "ok", "version": APP_VERSION})
        if path == "/api/locations":
            return self._json(200, STORE.all())
        plan_match = re.fullmatch(r"/api/locations/([a-zA-Z0-9_-]+)/plan", path)
        if plan_match:
            location = STORE.get(plan_match.group(1))
            return self._json(200 if location else 404,
                              {"plan": location.get("plan", [])} if location else {"error": "Location not found"})
        if path == "/api/overview":
            locations = STORE.all()
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(locations)))) as pool:
                reports = list(pool.map(lambda loc: SERVICE.report(loc["id"]), locations))
            rows, alerts = [], []
            for location, report in zip(locations, reports):
                if not report or report.get("error"):
                    rows.append({"location": location, "status": "unavailable", "alerts": []})
                    continue
                row_alerts = location_tide_alerts(report)
                rows.append({"location": location, "status": "available", "alerts": row_alerts,
                             "scores": report.get("scores", {}), "lighting": report.get("lighting", {}),
                             "updated_at": report.get("updated_at"),
                             "tide_range": report.get("tide_range")})
                for alert in row_alerts:
                    alerts.append({**alert, "timezone": report.get("timezone", "UTC"),
                                   "location_id": location["id"], "location_name": location["name"]})
            return self._json(200, {"locations": rows, "alerts": alerts})
        if path.startswith("/api/report/"):
            location_id = path.rsplit("/", 1)[-1]
            query = parse_qs(parsed.query)
            report = SERVICE.report(location_id, refresh=query.get("refresh") == ["1"])
            return self._json(200 if report else 404, report or {"error": "Location not found"})
        if path.startswith("/api/glance/"):
            location_id = path.rsplit("/", 1)[-1]
            report = SERVICE.report(location_id)
            if not report:
                return self._json(404, {"error": "Location not found"})
            photo = report.get("scores", {}).get("photogenic") or {}
            bird = report.get("scores", {}).get("birding") or {}
            astro = report.get("scores", {}).get("astro") or {}
            return self._json(200, {
                "location": report["location"]["name"],
                "photogenic_score": photo.get("score"), "birding_score": bird.get("score"),
                "astro_score": astro.get("score"),
                "photogenic_score_display": f"{photo['score']}/100" if photo.get("score") is not None else "Unavailable",
                "birding_score_display": f"{bird['score']}/100" if bird.get("score") is not None else "Unavailable",
                "astro_score_display": f"{astro['score']}/100" if astro.get("score") is not None else "No dark-sky window",
                "lighting": report.get("lighting", {}).get("phase"),
                "solar_elevation_degrees": report.get("lighting", {}).get("solar_elevation_degrees"),
                "cloud_cover_percent": report.get("lighting", {}).get("cloud_cover_percent"),
                "low_cloud_percent": report.get("lighting", {}).get("low_cloud_percent"),
                "best_photo": report.get("best_photo"), "best_astro": report.get("best_astro"),
                "birding_evidence": report.get("bird_outlook", {}).get("species_count"),
                "next_tide": next((e for d in report.get("tide_calendar", []) for e in d["events"]
                                   if datetime.fromisoformat(e["time"]).astimezone(timezone.utc) >= datetime.now(timezone.utc)), None),
                "summary": glance_summary(report), "updated_at": report.get("updated_at"),
            })
        if path.startswith("/api/geocode"):
            query = parse_qs(parsed.query).get("q", [""])[0]
            try:
                return self._json(200, {"results": geocode(query)})
            except ProviderError as err:
                return self._json(502, {"error": str(err)})
        if path == "/" or path in {"/app.js", "/styles.css"}:
            target = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
            try:
                content = target.read_bytes()
                mime = "text/html; charset=utf-8" if target.suffix == ".html" else "text/javascript; charset=utf-8" if target.suffix == ".js" else "text/css; charset=utf-8"
                return self._send(200, content, mime)
            except OSError:
                return self._json(404, {"error": "Not found"})
        self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        try:
            payload = self._body()
            if path == "/api/locations":
                item = STORE.save(payload)
                return self._json(201, item)
            plan_match = re.fullmatch(r"/api/locations/([a-zA-Z0-9_-]+)/plan", path)
            if plan_match:
                location = STORE.add_plan_item(plan_match.group(1), payload)
                return self._json(201 if location else 404,
                                  location if location else {"error": "Location not found"})
            if path == "/api/locations/geocode":
                return self._json(200, {"results": geocode(payload.get("query", ""))})
            return self._json(404, {"error": "Not found"})
        except (ValueError, TypeError, json.JSONDecodeError, ProviderError) as err:
            return self._json(400, {"error": str(err)})

    def do_PUT(self):
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/locations/([a-zA-Z0-9_-]+)", path)
        if not match:
            return self._json(404, {"error": "Not found"})
        if not STORE.get(match.group(1)):
            return self._json(404, {"error": "Location not found"})
        try:
            item = STORE.save(self._body(), match.group(1))
            SERVICE.invalidate(match.group(1))
            return self._json(200, item)
        except (ValueError, TypeError, json.JSONDecodeError, ProviderError) as err:
            return self._json(400, {"error": str(err)})

    def do_DELETE(self):
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        plan_match = re.fullmatch(r"/api/locations/([a-zA-Z0-9_-]+)/plan/([a-zA-Z0-9_-]+)", path)
        if plan_match:
            location = STORE.remove_plan_item(plan_match.group(1), plan_match.group(2))
            return self._json(200 if location else 404,
                              location if location else {"error": "Location not found"})
        match = re.fullmatch(r"/api/locations/([a-zA-Z0-9_-]+)", path)
        if not match:
            return self._json(404, {"error": "Not found"})
        SERVICE.invalidate(match.group(1))
        deleted = STORE.delete(match.group(1))
        return self._json(200 if deleted else 404,
                          {"deleted": True} if deleted else {"error": "Location not found"})


def glance_summary(report: dict) -> str:
    lighting = report.get("lighting", {})
    if lighting.get("phase") in {"nautical twilight", "astronomical twilight", "astronomical night"}:
        score = report.get("scores", {}).get("astro") or {}
        return f"{lighting.get('phase').title()} · Astro {score.get('score', '—')}/100"
    photo = report.get("scores", {}).get("photogenic") or {}
    bird = report.get("scores", {}).get("birding") or {}
    return f"{lighting.get('phase', 'Light unavailable').title()} · Photo {photo.get('score', '—')}/100 · Birding {bird.get('score', '—')}/100"


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    LOG.info("Photogenic Sky Planner listening on port %s with data in %s", PORT, DATA_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
