"""Platform for sensor integration."""
import logging
from datetime import timedelta
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_LOCATION

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform from a config entry."""
    config = config_entry.data
    api_key = config[CONF_API_KEY]
    location = config[CONF_LOCATION]
    async_add_entities([PhotogenicSkySensor(api_key, location, config_entry.entry_id)], True)

class PhotogenicSkySensor(SensorEntity):
    """Representation of a Photogenic Sky Sensor."""

    def __init__(self, api_key, location, entry_id):
        """Initialize the sensor."""
        self._api_key = api_key
        self._location = location
        self._attr_name = f"Photogenic Sky {location}"
        self._attr_unique_id = f"{entry_id}_{location.lower().replace(' ', '_')}"
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:camera"
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
        """Fetch new state data for the sensor."""
        url = f"http://api.weatherapi.com/v1/forecast.json?key={self._api_key}&q={self._location}&days=1&aqi=no&alerts=no"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        _LOGGER.error("Error fetching data from WeatherAPI: %s", response.status)
                        return
                    data = await response.json()
        except aiohttp.ClientError as err:
            _LOGGER.error("Error communicating with WeatherAPI: %s", err)
            return

        # --- V3 SCORING LOGIC (WITH MOON DATA) ---
        current_conditions = data.get("current", {})
        # Get astro data from the forecast section
        astro_data = data.get("forecast", {}).get("forecastday", [{}])[0].get("astro", {})
        score = 100

        # Extract key weather metrics
        is_day = current_conditions.get("is_day", 1) == 1
        cloud_cover = current_conditions.get("cloud", 100)
        vis_km = current_conditions.get("vis_km", 0)
        precip_mm = current_conditions.get("precip_mm", 0)
        wind_kph = current_conditions.get("wind_kph", 0)
        condition_text = current_conditions.get("condition", {}).get("text", "").lower()
        
        # Extract astro metrics
        moon_illumination = int(astro_data.get("moon_illumination", 100))
        moon_phase = astro_data.get("moon_phase", "Unknown")

        # --- NIGHT TIME PHOTOGRAPHY MODEL (Astrophotography Focus) ---
        if not is_day:
            # 1. MOON: A bright moon washes out stars. This is now a major factor.
            if moon_illumination > 50:
                score -= 50 # Half moon or more is very bad for deep sky
            elif moon_illumination > 10:
                score -= 25 # Even a crescent moon affects visibility
            
            # 2. CLOUDS: Still the biggest dealbreaker.
            if cloud_cover > 15:
                score -= 60
            elif cloud_cover > 5:
                score -= 30
            
            # 3. VISIBILITY: Penalize anything less than perfect.
            # Since API reports 10km as "good", we can't do much if it's wrong,
            # but we can penalize if it reports anything lower.
            if "mist" in condition_text or "fog" in condition_text or vis_km < 8:
                score -= 40
            
            # 4. PRECIPITATION & WIND
            if precip_mm > 0: score -= 70
            if wind_kph > 25: score -= 20

        # --- DAY TIME PHOTOGRAPHY MODEL (Landscapes, Portraits) ---
        else:
            # A few clouds are often desirable for daytime shots!
            if cloud_cover > 80:
                score -= 40 # Overcast is usually dull
            elif cloud_cover > 20 and cloud_cover < 60:
                score += 5 # Bonus for interesting, partly cloudy skies!
            elif cloud_cover <= 10 and "clear" in condition_text:
                score -= 10 # Perfectly clear can sometimes be harsh/boring

            if "mist" in condition_text or "fog" in condition_text or vis_km < 5:
                score -= 30

            if precip_mm > 0.1: score -= 50
            if wind_kph > 35: score -= 15

        # Final score calculation and update attributes
        self._photogenic_score = max(0, min(100, score)) # Clamp score
        self._api_data = {
            "location": data.get("location", {}).get("name"),
            "is_day": is_day,
            "cloud_cover": f"{cloud_cover}%",
            "visibility_km": vis_km,
            "wind_kph": wind_kph,
            "precip_mm": precip_mm,
            "condition": current_conditions.get("condition", {}).get("text"),
            "moon_illumination": f"{moon_illumination}%",
            "moon_phase": moon_phase,
            "last_updated": current_conditions.get("last_updated"),
        }

