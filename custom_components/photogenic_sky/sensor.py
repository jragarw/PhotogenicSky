"""Photo planning sensors for weather, birds, and shoreline tides."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
import asyncio
import logging
import math

from aiohttp import ClientError
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(hours=12)

DAILY_FIELDS = ",".join((
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "precipitation_probability_max", "precipitation_sum", "wind_speed_10m_max",
    "cloud_cover_mean",
    "sunrise", "sunset", "uv_index_max", "moonrise", "moonset", "moon_phase", "daylight_duration",
))
HOURLY_FIELDS = ",".join((
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "precipitation_probability", "precipitation", "wind_speed_10m",
))

CONDITIONS = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 56: "Freezing drizzle", 57: "Dense freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Heavy freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
    82: "Heavy rain showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


async def async_setup_entry(
    hass, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors for the configured planning location."""
    latitude = config_entry.data.get("latitude")
    longitude = config_entry.data.get("longitude")
    if latitude is None or longitude is None:
        _LOGGER.error("Location '%s' has no coordinates", config_entry.title)
        return

    location_name = config_entry.data.get("location_name", config_entry.title)
    options = config_entry.options
    lookahead_days = options.get("lookahead_days", 7)
    radius_km = options.get("radius_km", 25)
    shoreline = options.get("shoreline", False)
    session = async_get_clientsession(hass)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Photo Planner {location_name}",
        update_method=lambda: _async_update_data(
            session,
            latitude,
            longitude,
            str(hass.config.time_zone),
            lookahead_days,
            radius_km,
            shoreline,
        ),
        update_interval=UPDATE_INTERVAL,
    )
    await coordinator.async_config_entry_first_refresh()
    entities = [
        PhotogenicSkySensor(coordinator, location_name, config_entry.entry_id),
        LightingSensor(coordinator, location_name, config_entry.entry_id),
        AstroSensor(coordinator, location_name, config_entry.entry_id),
        BirdingSensor(coordinator, location_name, config_entry.entry_id),
        ScoreDecisionSensor(coordinator, location_name, config_entry.entry_id, "photogenic"),
        ScoreDecisionSensor(coordinator, location_name, config_entry.entry_id, "birding"),
        BirdOutlookSensor(coordinator, location_name, config_entry.entry_id),
    ]
    if shoreline:
        entities.append(TideSensor(coordinator, location_name, config_entry.entry_id))
    async_add_entities(entities)


async def _async_update_data(
    session, latitude, longitude, timezone, lookahead_days, radius_km, shoreline
):
    """Refresh data sources independently so one outage does not hide others."""
    weather, birds, tides = await _gather_sources(
        session, latitude, longitude, timezone, lookahead_days, radius_km, shoreline
    )
    location_timezone = weather["timezone"] if weather else timezone
    lighting = _lighting_state(weather, latitude, longitude) if weather else None
    opportunities = (
        _build_opportunities(weather["days"], location_timezone, birds, weather["hourly"], lighting)
        if weather else []
    )
    return {
        "weather": weather,
        "birds": birds,
        "tides": tides,
        "opportunities": opportunities,
        "lighting": lighting,
        "location": {"latitude": latitude, "longitude": longitude},
        "lookahead_days": lookahead_days,
        "updated_at": weather.get("updated_at") if weather else None,
    }


async def _gather_sources(session, latitude, longitude, timezone, lookahead_days, radius_km, shoreline):
    """Fetch weather and optional biological and tide context."""
    weather = birds = tides = None
    try:
        weather = await _fetch_weather(session, latitude, longitude, "auto", lookahead_days)
    except (ClientError, TimeoutError, ValueError, UpdateFailed) as err:
        _LOGGER.warning("Weather refresh failed: %s", err)
    try:
        location_timezone = weather["timezone"] if weather else timezone
        birds = await _fetch_birds(
            session, latitude, longitude, location_timezone, radius_km, lookahead_days
        )
    except (ClientError, TimeoutError, ValueError, UpdateFailed) as err:
        _LOGGER.warning("Bird outlook refresh failed: %s", err)
    if shoreline:
        try:
            tides = await _fetch_tides(
                session, latitude, longitude, weather["timezone"] if weather else timezone
            )
        except (ClientError, TimeoutError, ValueError, UpdateFailed) as err:
            _LOGGER.warning("Tide refresh failed: %s", err)
    return weather, birds, tides


async def _json_get(session, url, params=None):
    async with session.get(url, params=params) as response:
        response.raise_for_status()
        return await response.json()


async def _fetch_weather(session, latitude, longitude, timezone, forecast_days):
    payload = await _json_get(
        session,
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": DAILY_FIELDS,
            "hourly": HOURLY_FIELDS,
            "current": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
            "timezone": timezone,
            "forecast_days": forecast_days,
        },
    )
    daily = payload.get("daily", {})
    hourly = payload.get("hourly", {})
    if not daily.get("time"):
        raise UpdateFailed("Open-Meteo returned no forecast days")
    days = []
    for index, date in enumerate(daily["time"]):
        days.append({
            "date": date,
            "condition": CONDITIONS.get(_at(daily, "weather_code", index), "Unknown"),
            "weather_code": _at(daily, "weather_code", index),
            "temperature_max_c": _at(daily, "temperature_2m_max", index),
            "temperature_min_c": _at(daily, "temperature_2m_min", index),
            "precipitation_probability_max": _at(daily, "precipitation_probability_max", index),
            "precipitation_mm": _at(daily, "precipitation_sum", index),
            "wind_speed_kmh": _at(daily, "wind_speed_10m_max", index),
            "cloud_cover_mean": _at(daily, "cloud_cover_mean", index),
            "uv_index_max": _at(daily, "uv_index_max", index),
            "moonrise": _at(daily, "moonrise", index),
            "moonset": _at(daily, "moonset", index),
            "moon_phase": _at(daily, "moon_phase", index),
            "daylight_duration_seconds": _at(daily, "daylight_duration", index),
            "sunrise": _at(daily, "sunrise", index),
            "sunset": _at(daily, "sunset", index),
        })
    return {
        "days": days,
        "hourly": hourly,
        "current": payload.get("current", {}),
        "timezone": payload.get("timezone", timezone),
        "source": "Open-Meteo",
        "updated_at": datetime.now(dt_timezone.utc).isoformat(),
    }


async def _fetch_birds(session, latitude, longitude, timezone, radius_km, lookahead_days):
    """Summarize seasonal bird records from GBIF for the upcoming calendar window.

    GBIF's open occurrence data is historical evidence, not a species forecast.
    The same upcoming date range is queried in the previous five years.
    """
    from datetime import datetime, timedelta
    from urllib.parse import urlencode
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(timezone)).date()
    target_end = today + timedelta(days=lookahead_days - 1)
    polygon = _radius_polygon(latitude, longitude, radius_km)
    birds = {}
    years_with_data = 0
    queries = []
    for year_offset in range(1, 6):
        start = _shift_year(today, today.year - year_offset)
        end = _shift_year(target_end, target_end.year - year_offset)
        params = {
            "taxonKey": 212,
            "geometry": polygon,
            "eventDate": f"{start.isoformat()},{end.isoformat()}",
            "facet": "speciesKey",
            "facetLimit": 500,
            "limit": 0,
        }
        url = "https://api.gbif.org/v1/occurrence/search?" + urlencode(params)
        queries.append(_json_get(session, url))

    payloads = await asyncio.gather(*queries)
    for payload in payloads:
        facets = payload.get("facets", [])
        species_counts = facets[0].get("counts", []) if facets else []
        if species_counts:
            years_with_data += 1
        for species in species_counts:
            key = species.get("name")
            if key is None:
                continue
            record = birds.setdefault(key, {"gbif_taxon_key": key, "records": 0, "years_reported": 0})
            record["records"] += species.get("count", 0)
            record["years_reported"] += 1

    # Keep regional totals before limiting the display list. The old score was
    # calculated from only the 12 names shown in the outlook, so busy regions
    # all received the same capped bird-evidence score.
    species_count = len(birds)
    recurring_species_count = sum(
        1 for item in birds.values() if item["years_reported"] >= 3
    )
    total_records = sum(item["records"] for item in birds.values())
    species_list = sorted(
        birds.values(),
        key=lambda item: (-item["years_reported"], -item["records"]),
    )
    species_list = species_list[:12]
    details = await asyncio.gather(*(
        _json_get(session, f"https://api.gbif.org/v1/species/{species['gbif_taxon_key']}")
        for species in species_list
    ))
    for species, detail in zip(species_list, details):
        species["scientific_name"] = detail.get("scientificName")
        species["common_name"] = detail.get("vernacularName")
    return {
        "species": species_list,
        "species_count": species_count,
        "species_count_may_be_capped": species_count == 500,
        "recurring_species_count": recurring_species_count,
        "total_records": total_records,
        "source": "GBIF occurrence search",
        "evidence_years": years_with_data,
        "window_days": lookahead_days,
        "radius_km": radius_km,
        "status": "seasonal_records" if species_list else "no_records",
        "note": "Historical records from matching calendar dates in the previous five years; not a forecast or a guarantee of presence.",
    }


def _shift_year(value, target_year):
    from datetime import date
    try:
        return value.replace(year=target_year)
    except ValueError:
        return date(target_year, 2, 28)


def _radius_polygon(latitude, longitude, radius_km):
    center_lat = math.radians(latitude)
    center_lon = math.radians(longitude)
    angular_distance = radius_km / 6371.0
    coordinates = []
    for bearing_degrees in range(0, 360, 15):
        bearing = math.radians(bearing_degrees)
        target_lat = math.asin(
            math.sin(center_lat) * math.cos(angular_distance)
            + math.cos(center_lat) * math.sin(angular_distance) * math.cos(bearing)
        )
        target_lon = center_lon + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(center_lat),
            math.cos(angular_distance) - math.sin(center_lat) * math.sin(target_lat),
        )
        coordinates.append(f"{math.degrees(target_lon):.5f} {math.degrees(target_lat):.5f}")
    coordinates.append(coordinates[0])
    return f"POLYGON(({','.join(coordinates)}))"


async def _fetch_tides(session, latitude, longitude, timezone):
    """Fetch free 30-day tide events from Open Waters."""
    from urllib.parse import urlencode
    from zoneinfo import ZoneInfo

    start = datetime.now(ZoneInfo(timezone)).astimezone(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=30)
    url = "https://api.openwaters.io/tides/extremes?" + urlencode({
        "latitude": latitude, "longitude": longitude,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"), "units": "meters",
    })
    payload = await _json_get(session, url)
    station = payload.get("station", {})
    events = [{
        "time": item["time"], "height_m": item.get("level"),
        "type": "high" if item.get("high") else "low",
    } for item in payload.get("extremes", []) if item.get("time")]
    if events:
        return {
            "status": "available", "source": "Open Waters (TICON-4)",
            "station": station.get("name"), "station_id": station.get("id"),
            "station_distance_km": payload.get("distance"), "datum": payload.get("datum"),
            "units": payload.get("units", "meters"), "timezone": station.get("timezone", timezone), "events": events,
            "attribution": "Open Waters tide predictions, CC BY 4.0",
            "note": station.get("disclaimers") or "Harmonic predictions; verify local datum and station suitability. Not for navigation.",
        }
    # NOAA fallback retained for US stations.

    stations = await _json_get(
        session, "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions"
    )
    candidates = [station for station in stations.get("stations", []) if station.get("type") in {"S", "R"}]
    if not candidates:
        raise UpdateFailed("NOAA tide stations are unavailable")
    station = min(
        candidates,
        key=lambda item: _distance_km(latitude, longitude, item["lat"], item["lng"]),
    )
    distance = _distance_km(latitude, longitude, station["lat"], station["lng"])
    if distance > 100:
        return {"status": "no_nearby_station", "source": "NOAA Tides & Currents", "station_distance_km": round(distance)}

    from zoneinfo import ZoneInfo

    start_date = datetime.now(ZoneInfo(timezone)).date()
    predictions = await _json_get(
        session,
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
        {
            "product": "predictions", "application": "PhotogenicSky",
            "begin_date": start_date.strftime("%Y%m%d"),
            "end_date": (start_date + timedelta(days=29)).strftime("%Y%m%d"),
            "datum": "MLLW", "station": station["id"],
            "time_zone": "gmt", "interval": "hilo", "units": "metric", "format": "json",
        },
    )
    events = [
        {"time": item["t"], "height_m": float(item["v"]), "type": "high" if item["type"] == "H" else "low"}
        for item in predictions.get("predictions", [])
    ]
    if not events:
        return {"status": "unavailable", "source": "NOAA Tides & Currents", "station": station["name"]}
    return {
        "status": "available", "source": "NOAA Tides & Currents",
        "station": station["name"], "station_id": station["id"],
        "station_distance_km": round(distance, 1), "timezone": timezone, "events": events,
        "note": "NOAA coverage is limited to US stations. Verify local datum and station suitability.",
    }


def _distance_km(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def _lighting_state(weather, latitude, longitude):
    """Calculate current solar phase independently for each configured location."""
    from zoneinfo import ZoneInfo
    local_now = datetime.now(ZoneInfo(weather["timezone"]))
    elevation = _solar_elevation(local_now, latitude, longitude)
    phase = "daylight" if elevation > 6 else "golden_hour" if elevation > -0.833 else "blue_hour" if elevation > -6 else "night"
    current = weather.get("current", {})
    return {"phase": phase, "solar_elevation_degrees": round(elevation, 2), "latitude": latitude, "longitude": longitude, "observed_at": local_now.isoformat(),
            "cloud_cover_percent": current.get("cloud_cover"), "low_cloud_cover_percent": current.get("cloud_cover_low"),
            "mid_cloud_cover_percent": current.get("cloud_cover_mid"), "high_cloud_cover_percent": current.get("cloud_cover_high")}


def _solar_elevation(local_time, latitude, longitude):
    """Approximate solar elevation using NOAA fractional-year equations."""
    day = local_time.timetuple().tm_yday
    hour = local_time.hour + local_time.minute / 60 + local_time.second / 3600
    gamma = 2 * math.pi / 365 * (day - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2*gamma) - 0.040849 * math.sin(2*gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2*gamma) + 0.000907 * math.sin(2*gamma) - 0.002697 * math.cos(3*gamma) + 0.00148 * math.sin(3*gamma))
    utc_offset = local_time.utcoffset()
    offset_minutes = utc_offset.total_seconds() / 60 if utc_offset else 0
    minutes = hour * 60 + eqtime + 4 * longitude - offset_minutes
    ha = math.radians(minutes / 4 - 180)
    lat = math.radians(latitude)
    return math.degrees(math.asin(math.sin(lat)*math.sin(decl)+math.cos(lat)*math.cos(decl)*math.cos(ha)))


def _astro_score(lighting, day, conditions):
    elevation = lighting["solar_elevation_degrees"]
    darkness = max(0, min(100, round((-elevation - 6) / 12 * 100)))
    clouds = conditions.get("cloud_cover", day.get("cloud_cover_mean"))
    clouds = 50 if clouds is None else clouds
    moon = day.get("moon_phase")
    illumination = (1 - abs(2 * moon - 1)) * 100 if moon is not None else 50
    return {"score": round(.55 * darkness + .35 * (100-clouds) + .10 * (100-illumination)),
            "darkness_score": darkness, "cloud_cover_percent": clouds, "moon_phase_fraction": moon,
            "moon_illumination_estimate_percent": round(illumination),
            "formula": "55% darkness below -6° solar elevation + 35% clear sky + 10% low moon illumination",
            "darkness_note": "Full astro darkness begins near -18° solar elevation; -6° is the blue-hour boundary."}


def _build_opportunities(days, timezone, bird_data, hourly=None, lighting=None):
    """Build daily photography windows and nighttime astro windows."""
    opportunities = []
    species_factor = _species_score(bird_data)
    latitude = lighting["latitude"] if lighting else 0
    longitude = lighting["longitude"] if lighting else 0
    for day_index, day in enumerate(days):
        within_bird_window = bird_data is not None and day_index < bird_data["window_days"]
        windows = [(name, day[name]) for name in ("sunrise", "sunset") if day.get(name)]
        noon = _solar_noon(day)
        if noon:
            windows.append(("midday", noon))
        if day.get("sunset"):
            sunset = datetime.fromisoformat(day["sunset"])
            for offset in (1, 2, 3):
                windows.append(("night", (sunset + timedelta(hours=offset)).isoformat()))
        for window, time in windows:
            conditions = _nearest_hour(hourly or {}, time)
            elevation = _solar_elevation(datetime.fromisoformat(time), latitude, longitude)
            astro_active = window == "night" and elevation < -6
            light_detail = _light_score_breakdown(day, window if window != "night" else "sunset", conditions)
            light = light_detail["score"]
            sky = _sky_score(day, light, conditions)
            if elevation < -6:
                sky = 0
            weather_detail = _weather_suitability_breakdown(day, conditions)
            weather = weather_detail["score"]
            photo_detail = _photogenic_score_breakdown(day, light, conditions)
            birds_present = within_bird_window and bird_data and bird_data["species"]
            birding_detail = _birding_score_breakdown(bird_data, light, weather) if birds_present else None
            birding = round(.55*species_factor + .30*light + .15*weather) if birds_present else None
            astro = _astro_score({"solar_elevation_degrees": elevation}, day, conditions) if astro_active else None
            if elevation < -6:
                birding = None
                birding_detail = None
            opportunities.append({
                "date": day["date"], "window": window, "time": time,
                "solar_elevation_degrees": round(elevation, 2),
                "photogenic_sky_score": sky, "light_quality_score": light,
                "birding_score": birding, "astro_score": astro["score"] if astro else None,
                "astro_score_breakdown": astro,
                "seasonal_bird_score": species_factor if birds_present else None,
                "weather_suitability_score": weather,
                "light_quality_breakdown": light_detail,
                "weather_suitability_breakdown": weather_detail,
                "photogenic_score_breakdown": photo_detail | ({"calculated_score": 0, "night_gate": "sun below -6 degrees; astro-only"} if elevation < -6 else {}),
                "birding_score_breakdown": birding_detail,
                "condition": day["condition"],
                "conditions_at_window": conditions,
                "cloud_cover_mean": day["cloud_cover_mean"],
                "seasonal_bird_species_count": bird_data.get("species_count", len(bird_data["species"])) if bird_data else None,
            })
    opportunities.sort(key=lambda item: item["time"])
    return opportunities


def _species_score(bird_data):
    if not bird_data or not bird_data["species"]:
        return 0
    # Measure regional richness from every returned species, not the 12-name
    # display sample. Historical year coverage tempers locations with sparse data.
    species_count = bird_data.get("species_count", len(bird_data["species"]))
    evidence_years = bird_data.get("evidence_years", 0)
    richness = min(100, round(100 * math.log1p(species_count) / math.log1p(500)))
    seasonal_coverage = min(100, round(evidence_years / 5 * 100))
    return round(0.70 * richness + 0.30 * seasonal_coverage)


def _photogenic_score_breakdown(day, light, conditions=None):
    conditions = conditions or {}
    cloud = conditions.get("cloud_cover")
    cloud = cloud if cloud is not None else day["cloud_cover_mean"]
    cloud = cloud if cloud is not None else 50
    rain = conditions.get("precipitation_probability")
    rain = rain if rain is not None else day["precipitation_probability_max"]
    rain = rain if rain is not None else 20
    light_contribution = round(0.55 * light, 1)
    clear_sky_contribution = round(0.30 * (100 - cloud), 1)
    dry_weather_contribution = round(0.15 * (100 - rain), 1)
    return {
        "formula": "55% light quality + 30% clear sky + 15% low rain probability",
        "light_quality_score": light,
        "light_quality_contribution": light_contribution,
        "cloud_cover_percent": cloud,
        "clear_sky_contribution": clear_sky_contribution,
        "precipitation_probability_percent": rain,
        "dry_weather_contribution": dry_weather_contribution,
        "calculated_score": _sky_score(day, light, conditions),
    }


def _birding_score_breakdown(bird_data, light, weather):
    seasonal = _species_score(bird_data)
    return {
        "formula": "55% seasonal bird evidence + 30% light quality + 15% weather suitability",
        "seasonal_bird_score": seasonal,
        "seasonal_bird_contribution": round(0.55 * seasonal, 1),
        "light_quality_score": light,
        "light_quality_contribution": round(0.30 * light, 1),
        "weather_suitability_score": weather,
        "weather_suitability_contribution": round(0.15 * weather, 1),
        "calculated_score": round(0.55 * seasonal + 0.30 * light + 0.15 * weather),
        "bird_species_count_in_region": bird_data.get(
            "species_count", len(bird_data["species"])
        ),
        "recurring_species_count": bird_data.get("recurring_species_count"),
        "historical_evidence_years": bird_data.get("evidence_years"),
        "historical_window_days": bird_data.get("window_days"),
        "search_radius_km": bird_data.get("radius_km"),
    }


def _decision_text(product, opportunity):
    if not opportunity:
        return f"{product} score is unavailable: no matching forecast opportunity."
    score_key = "photogenic_sky_score" if product == "Photogenic sky" else "birding_score"
    score = opportunity.get(score_key)
    if score is None:
        return f"{product} score is unavailable: no seasonal bird evidence was found."
    quality = "excellent" if score >= 80 else "good" if score >= 65 else "mixed" if score >= 45 else "poor"
    window = opportunity["window"]
    date_value = opportunity["date"]
    if product == "Photogenic sky":
        detail = opportunity["photogenic_score_breakdown"]
        return (
            f"{score}/100, {quality}; best at {window} on {date_value}. "
            f"Light {detail['light_quality_score']}/100, "
            f"cloud {detail['cloud_cover_percent']}%, "
            f"rain chance {detail['precipitation_probability_percent']}%."
        )
    detail = opportunity["birding_score_breakdown"]
    return (
        f"{score}/100, {quality}; best at {window} on {date_value}. "
        f"Seasonal evidence {detail['seasonal_bird_score']}/100, "
        f"light {detail['light_quality_score']}/100, "
        f"weather {detail['weather_suitability_score']}/100."
    )


def _light_score_breakdown(day, window, conditions=None):
    """Return the light score and every adjustment applied to it."""
    conditions = conditions or {}
    cloud = conditions.get("cloud_cover")
    cloud = cloud if cloud is not None else day["cloud_cover_mean"]
    cloud = cloud if cloud is not None else 50
    low_cloud = conditions.get("cloud_cover_low", 0) or 0
    high_cloud = conditions.get("cloud_cover_high", 0) or 0
    precipitation = conditions.get("precipitation_probability")
    precipitation = precipitation if precipitation is not None else day["precipitation_probability_max"]
    precipitation = precipitation if precipitation is not None else 20
    base = 90 if window in {"sunrise", "sunset"} else 25
    wind = conditions.get("wind_speed_kmh", day["wind_speed_kmh"]) or 0
    wind_penalty = min(wind, 80) * 0.15
    if window == "midday":
        cloud_modifier = min(cloud, 100) * 0.25
        cloud_detail = {"total_cloud_cover_percent": cloud}
    else:
        cloud_modifier = min(high_cloud, 100) * 0.12 - min(low_cloud, 100) * 0.35
        cloud_detail = {
            "low_cloud_cover_percent": low_cloud,
            "high_cloud_cover_percent": high_cloud,
        }
    cloud_adjustment = round(cloud_modifier, 1)
    rain_penalty = round(precipitation * 0.25, 1)
    raw_score = base + cloud_modifier - rain_penalty - wind_penalty
    return {
        "score": max(0, min(100, round(raw_score))),
        "formula": "window base + cloud adjustment - 0.25 x rain probability - 0.15 x capped wind speed",
        "window": window,
        "window_base": base,
        **cloud_detail,
        "cloud_adjustment": cloud_adjustment,
        "precipitation_probability_percent": precipitation,
        "precipitation_penalty": rain_penalty,
        "wind_speed_kmh": wind,
        "wind_speed_used_for_penalty_kmh": min(wind, 80),
        "wind_penalty_cap_kmh": 80,
        "wind_penalty": round(wind_penalty, 1),
        "uncapped_score": round(raw_score, 1),
    }


def _light_score(day, window, conditions=None):
    """Dawn and dusk are favorable; harsh midday light lowers the shared light score."""
    return _light_score_breakdown(day, window, conditions)["score"]


def _sky_score(day, light, conditions=None):
    conditions = conditions or {}
    cloud = conditions.get("cloud_cover", day["cloud_cover_mean"])
    cloud = cloud if cloud is not None else 50
    rain = conditions.get("precipitation_probability", day["precipitation_probability_max"])
    rain = rain if rain is not None else 20
    return max(0, min(100, round(0.55 * light + 0.30 * (100 - cloud) + 0.15 * (100 - rain))))


def _weather_suitability_breakdown(day, conditions=None):
    conditions = conditions or {}
    cloud = conditions.get("cloud_cover", day["cloud_cover_mean"])
    cloud = cloud if cloud is not None else 50
    rain = conditions.get("precipitation_probability", day["precipitation_probability_max"])
    rain = rain if rain is not None else 25
    wind = conditions.get("wind_speed_kmh", day["wind_speed_kmh"]) or 0
    rain_score = max(0, 100 - rain)
    wind_score = max(0, 100 - min(wind, 100))
    cloud_score = max(0, 100 - cloud)
    contributions = {
        "rain_contribution": round(0.45 * rain_score, 1),
        "wind_contribution": round(0.30 * wind_score, 1),
        "cloud_contribution": round(0.25 * cloud_score, 1),
    }
    return {
        "formula": "45% low rain probability + 30% low wind + 25% low cloud cover",
        "precipitation_probability_percent": rain,
        "rain_score": rain_score,
        "wind_speed_kmh": wind,
        "wind_score": wind_score,
        "cloud_cover_percent": cloud,
        "cloud_score": cloud_score,
        **contributions,
        "score": max(0, min(100, round(
            0.45 * rain_score + 0.30 * wind_score + 0.25 * cloud_score
        ))),
    }


def _weather_suitability(day, conditions=None):
    return _weather_suitability_breakdown(day, conditions)["score"]


def _nearest_hour(hourly, local_time):
    from datetime import datetime

    target = datetime.fromisoformat(local_time)
    values = hourly.get("time", [])
    if not values:
        return {}
    candidates = []
    for index, value in enumerate(values):
        timestamp = datetime.fromisoformat(value)
        if timestamp.date() == target.date():
            candidates.append((abs((timestamp - target).total_seconds()), index))
    if not candidates:
        return {}
    index = min(candidates)[1]
    return {
        "time": values[index],
        "cloud_cover": _at(hourly, "cloud_cover", index),
        "cloud_cover_low": _at(hourly, "cloud_cover_low", index),
        "cloud_cover_mid": _at(hourly, "cloud_cover_mid", index),
        "cloud_cover_high": _at(hourly, "cloud_cover_high", index),
        "precipitation_probability": _at(hourly, "precipitation_probability", index),
        "precipitation_mm": _at(hourly, "precipitation", index),
        "wind_speed_kmh": _at(hourly, "wind_speed_10m", index),
    }


def _at(data, key, index):
    values = data.get(key, [])
    return values[index] if index < len(values) else None


def _solar_noon(day):
    from datetime import datetime

    if not day["sunrise"] or not day["sunset"]:
        return None
    sunrise = datetime.fromisoformat(day["sunrise"])
    sunset = datetime.fromisoformat(day["sunset"])
    return (sunrise + (sunset - sunrise) / 2).isoformat()


class PlanningSensor(SensorEntity):
    """Base for entities backed by the location coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, location_name, entry_id):
        self.coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": f"Photo Planner {location_name}",
            "manufacturer": "Photogenic Sky",
        }

    @property
    def available(self):
        return self.coordinator.last_update_success and self.coordinator.data is not None

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class PhotogenicSkySensor(PlanningSensor):
    """Photo opportunity with a score combining light, sky, and weather."""

    _attr_translation_key = "photogenic_sky"
    _attr_icon = "mdi:camera-iris"

    def __init__(self, coordinator, location_name, entry_id):
        super().__init__(coordinator, location_name, entry_id)
        self._attr_unique_id = entry_id

    @property
    def available(self):
        return super().available and self.coordinator.data["weather"] is not None

    @property
    def native_value(self):
        data = self.coordinator.data
        if data.get("lighting", {}).get("phase") == "night":
            return 0
        best = _best(data["opportunities"], "photogenic_sky_score")
        return best["photogenic_sky_score"] if best else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        best = _best(data["opportunities"], "photogenic_sky_score")
        current_night = data.get("lighting", {}).get("phase") == "night"
        return _common_attributes(data) | {
            "current_lighting": data.get("lighting"),
            "weather_forecast": data["weather"]["days"] if data["weather"] else [],
            "best_opportunity": best,
            "score_breakdown": ((best["photogenic_score_breakdown"] | {"current_state_score": 0, "status": "astro_only_after_blue_hour"}) if current_night and best else best["photogenic_score_breakdown"] if best else None),
            "decision": "0/100; sun below blue hour. Astro score is active." if current_night else _decision_text("Photogenic sky", best),
            "opportunities": data["opportunities"],
            "score_note": "Forecast guide from light quality, cloud cover, and rain probability; not a guarantee. Rounded scores can match across locations; compare coordinates and component inputs.",
        }


class LightingSensor(PlanningSensor):
    """Current local light phase and cloud layers."""
    _attr_translation_key = "lighting"
    _attr_icon = "mdi:weather-sunny"
    def __init__(self, coordinator, location_name, entry_id):
        super().__init__(coordinator, location_name, entry_id)
        self._attr_unique_id = f"{entry_id}_lighting"
    @property
    def native_value(self):
        return self.coordinator.data["lighting"]["phase"] if self.coordinator.data.get("lighting") else None
    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return (data.get("lighting") or {}) | {
            "daylight_duration_forecast_seconds": [day.get("daylight_duration_seconds") for day in data["weather"]["days"]],
            "moon_forecast": [{"date": day["date"], "phase_fraction": day.get("moon_phase"), "moonrise": day.get("moonrise"), "moonset": day.get("moonset")} for day in data["weather"]["days"]],
            "cloud_forecast_by_hour": [{"time": t, "cloud_cover": _at(data["weather"]["hourly"], "cloud_cover", i),
                "low_cloud_cover": _at(data["weather"]["hourly"], "cloud_cover_low", i),
                "mid_cloud_cover": _at(data["weather"]["hourly"], "cloud_cover_mid", i),
                "high_cloud_cover": _at(data["weather"]["hourly"], "cloud_cover_high", i)}
                for i, t in enumerate(data["weather"]["hourly"].get("time", []))],
        }


class AstroSensor(PlanningSensor):
    """Best astrophotography opportunity after blue hour."""
    _attr_translation_key = "astro_score"
    _attr_icon = "mdi:star-shooting"
    def __init__(self, coordinator, location_name, entry_id):
        super().__init__(coordinator, location_name, entry_id)
        self._attr_unique_id = f"{entry_id}_astro_score"
    @property
    def native_value(self):
        best = _best(self.coordinator.data["opportunities"], "astro_score")
        return best["astro_score"] if best else None
    @property
    def extra_state_attributes(self):
        best = _best(self.coordinator.data["opportunities"], "astro_score")
        return {"best_astro_opportunity": best, "score_breakdown": best.get("astro_score_breakdown") if best else None,
                "lighting": self.coordinator.data.get("lighting"),
                "note": "Astro score is only calculated when forecast solar elevation is below -6° (end of civil twilight)."}


class BirdingSensor(PlanningSensor):
    """Birding score includes the shared sunrise/sunset light-quality score."""

    _attr_translation_key = "birding_score"
    _attr_icon = "mdi:bird"

    def __init__(self, coordinator, location_name, entry_id):
        super().__init__(coordinator, location_name, entry_id)
        self._attr_unique_id = f"{entry_id}_birding_score"

    @property
    def available(self):
        if not super().available:
            return False
        birds = self.coordinator.data["birds"]
        return (
            self.coordinator.data["weather"] is not None
            and birds is not None
            and bool(birds["species"])
        )

    @property
    def native_value(self):
        best = _best(self.coordinator.data["opportunities"], "birding_score")
        return best["birding_score"] if best else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        best = _best(data["opportunities"], "birding_score")
        return _common_attributes(data) | {
            "best_birding_opportunity": best,
            "score_breakdown": best["birding_score_breakdown"] if best else None,
            "decision": _decision_text("Birding", best),
            "opportunities": data["opportunities"],
            "bird_species": data["birds"]["species"] if data["birds"] else [],
            "seasonal_bird_score": _species_score(data["birds"]),
            "seasonal_bird_evidence": _bird_evidence_attributes(data["birds"]),
            "lookahead_days": data["lookahead_days"],
            "score_formula": "55% seasonal regional species evidence, 30% light quality, 15% weather suitability",
            "score_note": "Seasonal occurrence history is not a prediction that birds will be present on a particular day. Rounded scores can match across locations; compare coordinates and evidence counts.",
        }


class ScoreDecisionSensor(PlanningSensor):
    """Readable score decision plus the inputs used for its best window."""

    _attr_icon = "mdi:text-box-check-outline"

    def __init__(self, coordinator, location_name, entry_id, product):
        super().__init__(coordinator, location_name, entry_id)
        self.product = product
        self._attr_translation_key = f"{product}_decision"
        self._attr_unique_id = f"{entry_id}_{product}_decision"

    @property
    def available(self):
        return super().available and self.coordinator.data["weather"] is not None

    @property
    def native_value(self):
        data = self.coordinator.data
        score_key = "photogenic_sky_score" if self.product == "photogenic" else "birding_score"
        best = _best(data["opportunities"], score_key)
        if self.product == "photogenic" and data.get("lighting", {}).get("phase") == "night":
            return "No sunlit photo score now; use the Astrophotography score after blue hour."
        if self.product == "birding" and data["birds"] is None:
            return "Birding score unavailable: regional bird data could not be retrieved."
        if self.product == "birding" and not data["birds"]["species"]:
            return "No historical seasonal bird records were found in this region."
        if self.product == "photogenic" and data.get("lighting", {}).get("phase") == "night":
            return "0/100; sun below blue hour. Daylight photo score is inactive; see Astrophotography score."
        return _decision_text("Photogenic sky" if self.product == "photogenic" else "Birding", best)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        score_key = "photogenic_sky_score" if self.product == "photogenic" else "birding_score"
        best = _best(data["opportunities"], score_key)
        breakdown_key = "photogenic_score_breakdown" if self.product == "photogenic" else "birding_score_breakdown"
        attributes = {
            **_common_attributes(data),
            "decision": self.native_value,
            "score": best.get(score_key) if best else None,
            "selected_opportunity": best,
            "score_breakdown": best.get(breakdown_key) if best else None,
            "formula": (
                "55% light quality + 30% clear sky + 15% low rain probability"
                if self.product == "photogenic"
                else "55% seasonal bird evidence + 30% light quality + 15% weather suitability"
            ),
        }
        if self.product == "birding":
            attributes["seasonal_bird_evidence"] = _bird_evidence_attributes(data["birds"])
        return attributes


class BirdOutlookSensor(PlanningSensor):
    """Regional seasonal bird records for the configured lookahead window."""

    _attr_translation_key = "bird_outlook"
    _attr_icon = "mdi:binoculars"

    def __init__(self, coordinator, location_name, entry_id):
        super().__init__(coordinator, location_name, entry_id)
        self._attr_unique_id = f"{entry_id}_bird_outlook"

    @property
    def available(self):
        return super().available and self.coordinator.data["birds"] is not None

    @property
    def native_value(self):
        birds = self.coordinator.data["birds"]
        return birds.get("species_count", len(birds["species"])) if birds else None

    @property
    def extra_state_attributes(self):
        return _common_attributes(self.coordinator.data) | {"bird_outlook": self.coordinator.data["birds"]}


class TideSensor(PlanningSensor):
    """Shoreline tide prediction events when a supported station is nearby."""

    _attr_translation_key = "tide_outlook"
    _attr_icon = "mdi:waves"

    def __init__(self, coordinator, location_name, entry_id):
        super().__init__(coordinator, location_name, entry_id)
        self._attr_unique_id = f"{entry_id}_tides"

    @property
    def available(self):
        return super().available and self.coordinator.data["tides"] is not None

    @property
    def native_value(self):
        tide_data = self.coordinator.data["tides"]
        if not tide_data:
            return None
        if tide_data.get("status") != "available":
            return tide_data["status"]
        events = tide_data.get("events", [])
        now = datetime.now(dt_timezone.utc)
        upcoming = [event for event in events if datetime.fromisoformat(event["time"].replace("Z", "+00:00")) >= now]
        if not upcoming:
            return "No upcoming tide event"
        event = upcoming[0]
        from zoneinfo import ZoneInfo
        local_time = datetime.fromisoformat(event["time"].replace("Z", "+00:00")).astimezone(ZoneInfo(tide_data.get("timezone", "UTC")))
        return f"{event['type'].title()} tide at {local_time.strftime('%Y-%m-%d %H:%M')}"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "tides": data["tides"],
            "upcoming_events": data["tides"].get("events", []) if data["tides"] else [],
            "calendar_timezone": data["tides"].get("timezone") if data["tides"] else None,
            "calendar_days": _group_tides_by_day(data["tides"]) if data["tides"] else [],
            "shoreline_enabled": data["tides"] is not None,
            "tide_match_windows": [
                {
                    "date": day["date"],
                    "sunrise": _tide_context(
                        day["sunrise"], data["tides"], data["weather"]["timezone"]
                    ),
                    "sunset": _tide_context(
                        day["sunset"], data["tides"], data["weather"]["timezone"]
                    ),
                }
                for day in (data["weather"]["days"] if data["weather"] else [])
            ] if data["tides"] and data["tides"].get("events") else [],
        }


def _group_tides_by_day(tide_data):
    """Group upcoming high and low events by the provider's local calendar date."""
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(tide_data.get("timezone", "UTC"))
    grouped = {}
    for event in tide_data.get("events", []):
        local = datetime.fromisoformat(event["time"].replace("Z", "+00:00")).astimezone(zone)
        grouped.setdefault(local.date().isoformat(), []).append({
            "time": local.isoformat(), "type": event.get("type"), "height_m": event.get("height_m")
        })
    return [{"date": day, "events": events} for day, events in sorted(grouped.items())]


def _common_attributes(data):
    return {
        "location": data["location"],
        "latitude": data["location"]["latitude"],
        "longitude": data["location"]["longitude"],
        "lookahead_days": data["lookahead_days"],
        "weather_source": data["weather"]["source"] if data["weather"] else "unavailable",
        "bird_data_source": data["birds"]["source"] if data["birds"] else "unavailable",
        "updated_at": data.get("updated_at"),
    }


def _bird_evidence_attributes(bird_data):
    if bird_data is None:
        return {"status": "unavailable"}
    species_count = bird_data.get("species_count", len(bird_data["species"]))
    evidence_years = bird_data.get("evidence_years", 0)
    richness_score = min(100, round(100 * math.log1p(species_count) / math.log1p(500)))
    seasonal_coverage_score = min(100, round(evidence_years / 5 * 100))
    return {
        "status": bird_data.get("status", "unknown"),
        "score_formula": "70% log-scaled regional species richness (0-500) + 30% historical year coverage",
        "score": _species_score(bird_data),
        "regional_species_count": species_count,
        "species_count_may_be_capped": bird_data.get("species_count_may_be_capped", False),
        "displayed_species_count": len(bird_data["species"]),
        "recurring_species_count": bird_data.get("recurring_species_count"),
        "total_historical_records": bird_data.get("total_records"),
        "historical_years_with_records": evidence_years,
        "years_searched": 5,
        "richness_score": richness_score,
        "richness_contribution": round(0.70 * richness_score, 1),
        "seasonal_coverage_score": seasonal_coverage_score,
        "seasonal_coverage_contribution": round(0.30 * seasonal_coverage_score, 1),
        "search_radius_km": bird_data.get("radius_km"),
        "window_days": bird_data.get("window_days"),
        "data_source": bird_data.get("source"),
        "note": "GBIF historical occurrences for matching calendar dates in the previous five years; not a live bird count or forecast.",
    }


def _best(opportunities, key):
    ranked = [item for item in opportunities if item.get(key) is not None]
    return max(ranked, key=lambda item: item[key]) if ranked else None


def _tide_context(value, tide_data, timezone_name):
    from datetime import datetime
    from datetime import timezone as utc_timezone
    from zoneinfo import ZoneInfo

    if not value:
        return None
    events = tide_data.get("events", [])
    target = datetime.fromisoformat(value)
    if target.tzinfo is None:
        target = target.replace(tzinfo=ZoneInfo(timezone_name))
    target = target.astimezone(utc_timezone.utc)

    timed_events = []
    for event in events:
        tide_time = datetime.fromisoformat(event["time"])
        if tide_time.tzinfo is None:
            tide_time = tide_time.replace(tzinfo=utc_timezone.utc)
        timed_events.append((tide_time.astimezone(utc_timezone.utc), event))
    timed_events.sort(key=lambda item: item[0])
    previous = next((item for item in reversed(timed_events) if item[0] <= target), None)
    following = next((item for item in timed_events if item[0] > target), None)
    if previous is None or following is None:
        return {"status": "outside_tide_forecast"}

    direction = "rising" if previous[1]["type"] == "low" else "falling"
    minutes_to_turn = round((following[0] - target).total_seconds() / 60)
    nearby = [
        event | {"minutes_from_light_window": round(abs((target - tide_time).total_seconds()) / 60)}
        for tide_time, event in timed_events
        if abs((target - tide_time).total_seconds()) <= 3 * 60 * 60
    ]
    return {
        "status": "available",
        "direction": direction,
        "previous_turn": previous[1],
        "next_turn": following[1],
        "minutes_to_next_turn": minutes_to_turn,
        "nearby_high_low_events": nearby,
    }
