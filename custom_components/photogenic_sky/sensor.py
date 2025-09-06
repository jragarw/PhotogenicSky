"""Platform for sensor integration."""
import logging
from datetime import timedelta
import asyncio
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)

# Maps the state of the moon.moon sensor to an approximate illumination percentage
MOON_PHASE_ILLUMINATION = {
    "new_moon": 0,
    "waxing_crescent": 15,
    "first_quarter": 50,
    "waxing_gibbous": 85,
    "full_moon": 100,
    "waning_gibbous": 85,
    "last_quarter": 50,
    "waning_crescent": 15,
}

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform from a config entry."""
    if "latitude" not in config_entry.data or "longitude" not in config_entry.data:
        _LOGGER.warning("Legacy config entry for '%s' missing coordinates. Please re-add it.", config_entry.title)
        return
        
    latitude = config_entry.data["latitude"]
    longitude = config_entry.data["longitude"]
    location_name = config_entry.data.get("location_name", config_entry.title)
    
    async_add_entities([PhotogenicSkySensor(hass, latitude, longitude, location_name, config_entry.entry_id)], True)

class PhotogenicSkySensor(SensorEntity):
    """Representation of a Photogenic Sky Sensor."""

    def __init__(self, hass: HomeAssistant, latitude: float, longitude: float, location_name: str, entry_id: str):
        self.hass = hass
        self._latitude = latitude
        self._longitude = longitude
        self._location_name = location_name
        self._attr_name = f"Photogenic Sky {location_name}"
        self._attr_unique_id = entry_id
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:camera-iris"
        self._photogenic_score = 0
        self._api_data = {}

    @property
    def native_value(self):
        return self._photogenic_score

    @property
    def extra_state_attributes(self):
        return self._api_data

    async def _api_call(self, session, base_url, params):
        """Make a single API call and return the JSON response."""
        async with session.get(base_url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def async_update(self):
        """Fetch new state data for the sensor using two separate API calls."""
        
        base_url = "https://api.open-meteo.com/v1/forecast"
        
        current_params = {
            "latitude": self._latitude, "longitude": self._longitude,
            "current": "temperature_2m,relativehumidity_2m,apparent_temperature,precipitation,weathercode,cloudcover,cloudcover_low,cloudcover_mid,cloudcover_high,windspeed_10m",
        }
        daily_params = {
            "latitude": self._latitude, "longitude": self._longitude,
            "daily": "sunrise,sunset,uv_index_max",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                current_task = self._api_call(session, base_url, current_params)
                daily_task = self._api_call(session, base_url, daily_params)
                results = await asyncio.gather(current_task, daily_task, return_exceptions=True)
                
                current_data, daily_data = results
                
                if isinstance(current_data, Exception): raise current_data
                if isinstance(daily_data, Exception): raise daily_data

        except Exception as err:
            _LOGGER.error("Error communicating with Open-Meteo API for '%s': %s", self._location_name, err)
            self._photogenic_score = 0
            self._api_data["photogenic_summary"] = "Could not retrieve data."
            return

        current = current_data.get("current", {})
        daily = daily_data.get("daily", {})
        if not current or not daily:
            _LOGGER.error("API response for '%s' missing data sections.", self._location_name)
            return

        # --- GET DATA FROM HOME ASSISTANT'S OWN SENSORS ---
        sun_state = self.hass.states.get('sun.sun')
        sun_elevation = sun_state.attributes.get('elevation', 0) if sun_state else 0
        
        moon_state = self.hass.states.get('moon.moon')
        moon_phase_str = moon_state.state if moon_state else "unknown"
        moon_illumination = MOON_PHASE_ILLUMINATION.get(moon_phase_str, 50) # Default to 50 if unknown
        
        total_clouds = current.get("cloudcover", 0)
        precip = current.get("precipitation", 0)
        
        # Astro score now uses reliable, internal moon data
        astro_score = 100
        astro_score -= moon_illumination * 0.5
        astro_score -= total_clouds * 0.5
        if precip > 0: astro_score = 0
        
        astro_summary = ""
        if astro_score > 85:
            astro_summary = "Excellent conditions: clear skies and a dark moon."
        elif astro_score > 60:
            astro_summary = "Good conditions, some moonlight or thin clouds will be visible."
        else:
            astro_summary = "Not suitable for astrophotography."

        score, summary, lighting_condition = self._calculate_main_score(sun_elevation, current)
        
        self._photogenic_score = score
        self._api_data = {
            "photogenic_summary": summary, "lighting_condition": lighting_condition,
            "sun_elevation": round(sun_elevation, 2), "location_name": self._location_name,
            "astrophotography_score": max(0, int(astro_score)), "astro_summary": astro_summary,
            "moon_phase": moon_phase_str.replace("_", " ").title(), # Get phase name from HA
            "cloud_cover_low": f"{current.get('cloudcover_low', 0)}%",
            "cloud_cover_mid": f"{current.get('cloudcover_mid', 0)}%",
            "cloud_cover_high": f"{current.get('cloudcover_high', 0)}%",
            "daily_max_uv_index": daily.get('uv_index_max', [0])[0],
            "precipitation_mm": precip,
            "wind_kph": round(current.get("windspeed_10m", 0), 1),
            "humidity": f"{current.get('relativehumidity_2m', 0)}%",
            "feels_like_c": f"{current.get('apparent_temperature', 0)}°C",
            "sunrise": daily.get("sunrise", [""])[0], "sunset": daily.get("sunset", [""])[0],
            "last_updated": current.get("time"),
        }

    def _calculate_main_score(self, sun_elevation, current):
        cloud_low = current.get("cloudcover_low", 0)
        cloud_mid = current.get("cloudcover_mid", 0)
        cloud_high = current.get("cloudcover_high", 0)
        
        score, summary, lighting_condition = 0, "", ""

        if sun_elevation < -6:
            lighting_condition, score, summary = "Night", 50, "Night time. See Astro card for details."
        elif -4 <= sun_elevation < 6:
            lighting_condition = "Golden Hour"
            summary = "Golden Hour: "
            score = 50 + (cloud_high * 0.5) - (cloud_low * 0.8)
            if cloud_high > 20 and cloud_low < 30: summary += "Stunning sunset potential!"
            elif cloud_low > 50: summary += "Poor. Low clouds are blocking the sun."
            else: summary += "Decent conditions, but clouds may not be ideal."
        else:
            lighting_condition = "Daytime"
            if -6 <= sun_elevation < -4: lighting_condition = "Blue Hour"
            summary = f"{lighting_condition}: "
            score = 100 - (cloud_low * 0.7)
            if cloud_mid > 75: score -= 25
            if cloud_low > 60: summary += "Dull, overcast conditions."
            elif cloud_mid > 20: summary += "Good potential for dramatic skies."
            else: summary += "Clear conditions, may have harsh light."
        
        return max(0, min(100, int(score))), summary, lighting_condition

