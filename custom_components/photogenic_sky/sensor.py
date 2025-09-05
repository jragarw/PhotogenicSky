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
    # We now pass the 'hass' object to the sensor so it can access other entities
    async_add_entities([PhotogenicSkySensor(hass, api_key, location, config_entry.entry_id)], True)

class PhotogenicSkySensor(SensorEntity):
    """Representation of a Photogenic Sky Sensor."""

    def __init__(self, hass, api_key, location, entry_id):
        """Initialize the sensor."""
        self.hass = hass # Store the hass object
        self._api_key = api_key
        self._location = location
        self._attr_name = f"Photogenic Sky {location}"
        self._attr_unique_id = f"{entry_id}_{location.lower().replace(' ', '_')}"
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:camera-iris" # Changed icon to reflect light
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

        # --- V4 SCORING ENGINE (TIME & LIGHT AWARE) ---
        
        # Get Sun entity state from Home Assistant
        sun_state = self.hass.states.get('sun.sun')
        if sun_state is None or 'elevation' not in sun_state.attributes:
            _LOGGER.warning("Sun entity not available. Scoring will be less accurate.")
            sun_elevation = 90 if data.get("current", {}).get("is_day") else -90
        else:
            sun_elevation = sun_state.attributes.get('elevation', 0)

        # Extract weather data
        current_conditions = data.get("current", {})
        astro_data = data.get("forecast", {}).get("forecastday", [{}])[0].get("astro", {})
        
        cloud_cover = current_conditions.get("cloud", 100)
        vis_km = current_conditions.get("vis_km", 0)
        precip_mm = current_conditions.get("precip_mm", 0)
        wind_kph = current_conditions.get("wind_kph", 0)
        moon_illumination = int(astro_data.get("moon_illumination", 100))
        
        score = 0
        summary = ""
        lighting_condition = ""

        # --- MODEL SELECTION BASED ON SUN ELEVATION ---

        # 1. NIGHT MODEL (Astrophotography)
        if sun_elevation < -6:
            lighting_condition = "Night"
            score = 100
            summary = "Night: "
            # Heavily penalize moon and clouds for astro
            score -= moon_illumination * 0.6 # 60 points off for full moon
            score -= cloud_cover * 0.8 # 80 points off for full clouds
            if vis_km < 10: score -= 20
            if precip_mm > 0: score -= 100
            if wind_kph > 25: score -= 20
            
            if score > 85: summary += "Excellent clear sky for astrophotography."
            elif score > 60: summary += "Good conditions, but some moonlight or thin clouds."
            else: summary += "Poor conditions for astrophotography."

        # 2. BLUE HOUR MODEL (Sunrise/Sunset Twilight)
        elif -6 <= sun_elevation < -4:
            lighting_condition = "Blue Hour"
            score = 100
            summary = "Blue Hour: "
            # Ideal is clear and calm for cityscapes/landscapes
            score -= cloud_cover * 0.3 # Clouds are less of an issue
            if vis_km < 8: score -= 40
            if precip_mm > 0: score -= 60
            if wind_kph > 20: score -= 30

            if score > 80: summary += "Excellent, clear and calm conditions."
            else: summary += "Decent, but visibility or wind could be better."

        # 3. GOLDEN HOUR MODEL (The Magic Light)
        elif -4 <= sun_elevation < 6:
            lighting_condition = "Golden Hour"
            score = 100
            summary = "Golden Hour: "
            # Ideal is some clouds, but not overcast
            if cloud_cover < 15 or cloud_cover > 75:
                score -= 50 # Penalize clear skies or fully overcast
            if vis_km < 10: score -= 30
            if precip_mm > 0.1: score -= 80
            if wind_kph > 30: score -= 20

            if score > 85: summary += "Potentially stunning sunrise/sunset!"
            elif score > 60: summary += "Good conditions, but clouds might not be ideal."
            else: summary += "Conditions are not favorable for a good sunrise/sunset."

        # 4. DAYTIME MODEL (General Photography)
        else:
            lighting_condition = "Daytime"
            score = 100
            summary = "Daytime: "
            # Penalize harsh light of a perfectly clear sky
            if cloud_cover == 0:
                score -= 25
                summary += "Harsh light due to clear sky. "
            elif cloud_cover > 80:
                score -= 60
                summary += "Dull, overcast conditions. "
            
            if vis_km < 8: score -= 40
            if precip_mm > 0.2: score -= 70
            if wind_kph > 35: score -= 25

            if score > 75: summary += "Good general conditions with some clouds."
            elif score > 50: summary += "Acceptable conditions, but not perfect."
            else: summary += "Poor general photography conditions."


        # Final score calculation and update attributes
        self._photogenic_score = max(0, min(100, int(score))) # Clamp and integerize
        self._api_data = {
            "photogenic_summary": summary,
            "lighting_condition": lighting_condition,
            "sun_elevation": round(sun_elevation, 2),
            "cloud_cover": f"{cloud_cover}%",
            "visibility_km": vis_km,
            "wind_kph": wind_kph,
            "precip_mm": precip_mm,
            "moon_illumination": f"{moon_illumination}%",
            "last_updated": current_conditions.get("last_updated"),
        }

