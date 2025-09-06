"""Platform for sensor integration."""
import logging
from datetime import timedelta
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)

MOON_PHASE_ILLUMINATION = {
    "new_moon": 0, "waxing_crescent": 15, "first_quarter": 50, "waxing_gibbous": 85,
    "full_moon": 100, "waning_gibbous": 85, "last_quarter": 50, "waning_crescent": 15,
}

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform from a config entry."""
    if "latitude" not in config_entry.data or "longitude" not in config_entry.data:
        _LOGGER.warning(
            "Legacy config entry found for '%s' with no coordinate data. "
            "Please remove this location and re-add it to fix.",
            config_entry.title
        )
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

    async def async_update(self):
        """Fetch new state data for the sensor using Open-Meteo."""
        
        # --- PERMANENT URL FIX: Using a params dictionary ---
        # This is the robust, industry-standard way to build a request.
        # The library handles all the special characters and joining.
        base_url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "current": "temperature_2m,relativehumidity_2m,apparent_temperature,precipitation,weathercode,cloudcover,cloudcover_low,cloudcover_mid,cloudcover_high,windspeed_10m,winddirection_10m,uv_index",
            "daily": "sunrise,sunset,moonrise,moonset,moon_phase",
            "timezone": "auto"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                # The library will correctly build the full URL from the base and params
                async with session.get(base_url, params=params) as response:
                    response.raise_for_status()
                    data = await response.json()
        except Exception as err:
            _LOGGER.error("Error communicating with Open-Meteo API for '%s': %s", self._location_name, err)
            self._photogenic_score = 0
            self._api_data["photogenic_summary"] = "Could not retrieve data from Open-Meteo."
            return

        # (The rest of the file is identical to the previous version)
        current = data.get("current", {})
        daily = data.get("daily", {})
        if not current or not daily:
            _LOGGER.error("API response for '%s' missing 'current' or 'daily' data sections.", self._location_name)
            return

        sun_state = self.hass.states.get('sun.sun')
        sun_elevation = sun_state.attributes.get('elevation', 0) if sun_state else 0
        
        total_clouds = current.get("cloudcover", 0)
        precip = current.get("precipitation", 0)
        
        moon_phase_list = daily.get("moon_phase", [])
        moon_phase_str = moon_phase_list[0].lower().replace(" ", "_") if moon_phase_list else "full_moon"
        moon_illumination = MOON_PHASE_ILLUMINATION.get(moon_phase_str, 100)
        
        astro_score = 100
        astro_score -= moon_illumination * 0.5
        astro_score -= total_clouds * 0.5
        if precip > 0: astro_score = 0
        
        astro_summary = ""
        if astro_score > 85: astro_summary = "Excellent conditions: clear skies and a dark moon."
        elif astro_score > 60: astro_summary = "Good conditions, some moonlight or thin clouds."
        else: astro_summary = "Not suitable for astrophotography."

        score, summary, lighting_condition = self._calculate_main_score(sun_elevation, current)
        
        self._photogenic_score = score
        self._api_data = {
            "photogenic_summary": summary, "lighting_condition": lighting_condition,
            "sun_elevation": round(sun_elevation, 2), "location_name": self._location_name,
            "astrophotography_score": max(0, int(astro_score)), "astro_summary": astro_summary,
            "moon_phase": moon_phase_str.replace("_", " ").title(),
            "cloud_cover_low": f"{current.get('cloudcover_low', 0)}%",
            "cloud_cover_mid": f"{current.get('cloudcover_mid', 0)}%",
            "cloud_cover_high": f"{current.get('cloudcover_high', 0)}%",
            "uv_index": current.get('uv_index'), "precipitation_mm": precip,
            "wind_kph": round(current.get("windspeed_10m", 0), 1),
            "humidity": f"{current.get('relativehumidity_2m', 0)}%",
            "feels_like_c": f"{current.get('apparent_temperature', 0)}°C",
            "sunrise": daily.get("sunrise", [""])[0], "sunset": daily.get("sunset", [""])[0],
            "moonrise": daily.get("moonrise", [""])[0], "moonset": daily.get("moonset", [""])[0],
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

