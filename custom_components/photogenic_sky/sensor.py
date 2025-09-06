"""Platform for sensor integration."""
import logging
from datetime import timedelta
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_LOCATION_NAME

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform from a config entry."""
    # Extract the stored location data from the config entry
    location_data = config_entry.data
    latitude = location_data["latitude"]
    longitude = location_data["longitude"]
    location_name = location_data[CONF_LOCATION_NAME]
    
    async_add_entities([PhotogenicSkySensor(hass, latitude, longitude, location_name, config_entry.entry_id)], True)

class PhotogenicSkySensor(SensorEntity):
    """Representation of a Photogenic Sky Sensor."""

    def __init__(self, hass: HomeAssistant, latitude: float, longitude: float, location_name: str, entry_id: str):
        """Initialize the sensor."""
        self.hass = hass
        self._latitude = latitude
        self._longitude = longitude
        self._location_name = location_name
        
        # Use the full display name for the sensor's friendly name
        self._attr_name = f"Photogenic Sky {location_name}"
        # Make unique ID from the config entry ID to ensure it's stable and unique
        self._attr_unique_id = entry_id
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:camera-iris"
        self._photogenic_score = 0
        self._api_data = {}

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._photogenic_score

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        return self._api_data

    async def async_update(self):
        """Fetch new state data for the sensor using Open-Meteo."""
        
        # Use the stored latitude and longitude for this specific sensor instance
        lat = self._latitude
        lon = self._longitude
        
        params = (
            "&current=temperature_2m,relativehumidity_2m,apparent_temperature,"
            "precipitation,weathercode,cloudcover,cloudcover_low,cloudcover_mid,"
            "cloudcover_high,windspeed_10m,winddirection_10m,uv_index"
            "&daily=sunrise,sunset&timezone=auto"
        )
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}{params}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
        except Exception as err:
            _LOGGER.error("Error communicating with Open-Meteo API: %s", err)
            return

        # --- V6 ENGINE: CLOUD-TYPE AWARE SCORING ---
        
        sun_state = self.hass.states.get('sun.sun')
        # Use Home Assistant's sun elevation which is location-aware
        sun_elevation = sun_state.attributes.get('elevation', 0) if sun_state else 0

        current = data.get("current", {})
        daily = data.get("daily", {})
        
        cloud_low = current.get("cloudcover_low", 100)
        cloud_mid = current.get("cloudcover_mid", 100)
        cloud_high = current.get("cloudcover_high", 100)
        precip = current.get("precipitation", 0)
        wind_kph = current.get("windspeed_10m", 0)
        
        score = 0
        summary = ""
        lighting_condition = ""

        # MODEL SELECTION BASED ON SUN ELEVATION & CLOUD TYPE
        if sun_elevation < -6:
            lighting_condition = "Night"
            score = 100
            summary = "Night: "
            score -= (cloud_low * 0.5) + (cloud_mid * 0.2) + (cloud_high * 0.1)
            if precip > 0: score -= 100
            if score > 85: summary += "Excellent clear sky for astrophotography."
            else: summary += "Clouds are present, poor for astrophotography."

        elif -4 <= sun_elevation < 6:
            lighting_condition = "Golden Hour"
            summary = "Golden Hour: "
            score = 50
            score += cloud_high * 0.5
            score -= cloud_low * 0.8
            if cloud_high > 20 and cloud_low < 30:
                summary += "Stunning sunset potential! High clouds are catching the light."
            elif cloud_low > 50:
                summary += "Poor. Low clouds are blocking the sun."
            else:
                summary += "Decent conditions, but the clouds may not be ideal."

        else:
            lighting_condition = "Daytime"
            if -6 <= sun_elevation < -4:
                lighting_condition = "Blue Hour"
            summary = f"{lighting_condition}: "
            score = 100
            score -= cloud_low * 0.7
            if cloud_mid > 75: score -= 25
            if cloud_low > 60:
                summary += "Dull, overcast conditions due to a thick low cloud layer."
            elif cloud_mid > 20:
                summary += "Good potential for dramatic skies with mid-level clouds."
            else:
                summary += "Clear conditions, may result in harsh light."

        self._photogenic_score = max(0, min(100, int(score)))
        self._api_data = {
            "photogenic_summary": summary,
            "lighting_condition": lighting_condition,
            "sun_elevation": round(sun_elevation, 2),
            "location_name": self._location_name,
            
            "cloud_cover_low": f"{cloud_low}%",
            "cloud_cover_mid": f"{cloud_mid}%",
            "cloud_cover_high": f"{cloud_high}%",
            
            "uv_index": current.get('uv_index'),
            "precipitation_mm": precip,
            "wind_kph": round(wind_kph, 1),
            "humidity": f"{current.get('relativehumidity_2m', 0)}%",
            "feels_like_c": f"{current.get('apparent_temperature', 0)}°C",

            "sunrise": daily.get("sunrise", [""])[0],
            "sunset": daily.get("sunset", [""])[0],

            "last_updated": current.get("time"),
        }

