"""Platform for sensor integration."""
import logging
from datetime import timedelta, datetime
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
    async_add_entities([PhotogenicSkySensor(hass, api_key, location, config_entry.entry_id)], True)

class PhotogenicSkySensor(SensorEntity):
    """Representation of a Photogenic Sky Sensor."""

    def __init__(self, hass, api_key, location, entry_id):
        """Initialize the sensor."""
        self.hass = hass
        self._api_key = api_key
        self._location = location
        self._attr_name = f"Photogenic Sky {location}"
        self._attr_unique_id = f"{entry_id}_{location.lower().replace(' ', '_')}"
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

        # --- V5 SENSOR: FULL PLANNING DATA ---
        
        # Get Sun entity state from Home Assistant for accurate elevation
        sun_state = self.hass.states.get('sun.sun')
        if sun_state is None or 'elevation' not in sun_state.attributes:
            _LOGGER.warning("Sun entity not available. Scoring will be less accurate.")
            sun_elevation = 90 if data.get("current", {}).get("is_day") else -90
        else:
            sun_elevation = sun_state.attributes.get('elevation', 0)

        # Extract all relevant data structures from the API response
        current_conditions = data.get("current", {})
        forecast_day_data = data.get("forecast", {}).get("forecastday", [{}])[0]
        astro_data = forecast_day_data.get("astro", {})
        
        # Extract current weather conditions for scoring
        cloud_cover = current_conditions.get("cloud", 100)
        vis_km = current_conditions.get("vis_km", 0)
        precip_mm = current_conditions.get("precip_mm", 0)
        wind_kph = current_conditions.get("wind_kph", 0)
        moon_illumination = int(astro_data.get("moon_illumination", 100))
        
        # --- NEW PLANNING ATTRIBUTES ---
        # Get current hour to find hourly forecast data
        current_hour = datetime.fromtimestamp(data.get("location", {}).get("localtime_epoch", 0)).hour
        hourly_forecasts = forecast_day_data.get("hour", [])
        chance_of_rain = 0
        if len(hourly_forecasts) > current_hour:
            chance_of_rain = hourly_forecasts[current_hour].get("chance_of_rain", 0)

        # --- SCORING LOGIC (Unchanged from V4) ---
        score = 0
        summary = ""
        lighting_condition = ""
        
        # 1. NIGHT MODEL
        if sun_elevation < -6:
            lighting_condition = "Night"
            score = 100
            summary = "Night: "
            score -= moon_illumination * 0.6
            score -= cloud_cover * 0.8
            if vis_km < 10: score -= 20
            if precip_mm > 0: score -= 100
            if wind_kph > 25: score -= 20
            if score > 85: summary += "Excellent clear sky for astrophotography."
            elif score > 60: summary += "Good conditions, but some moonlight or thin clouds."
            else: summary += "Poor conditions for astrophotography."

        # 2. BLUE HOUR MODEL
        elif -6 <= sun_elevation < -4:
            lighting_condition = "Blue Hour"
            score = 100
            summary = "Blue Hour: "
            score -= cloud_cover * 0.3
            if vis_km < 8: score -= 40
            if precip_mm > 0: score -= 60
            if wind_kph > 20: score -= 30
            if score > 80: summary += "Excellent, clear and calm conditions."
            else: summary += "Decent, but visibility or wind could be better."

        # 3. GOLDEN HOUR MODEL
        elif -4 <= sun_elevation < 6:
            lighting_condition = "Golden Hour"
            score = 100
            summary = "Golden Hour: "
            if cloud_cover < 15 or cloud_cover > 75: score -= 50
            if vis_km < 10: score -= 30
            if precip_mm > 0.1: score -= 80
            if wind_kph > 30: score -= 20
            if score > 85: summary += "Potentially stunning sunrise/sunset!"
            elif score > 60: summary += "Good conditions, but clouds might not be ideal."
            else: summary += "Conditions are not favorable for a good sunrise/sunset."

        # 4. DAYTIME MODEL
        else:
            lighting_condition = "Daytime"
            score = 100
            summary = "Daytime: "
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

        # --- Final score calculation and comprehensive attribute update ---
        self._photogenic_score = max(0, min(100, int(score)))
        self._api_data = {
            # Core Summary
            "photogenic_summary": summary,
            "lighting_condition": lighting_condition,
            "sun_elevation": round(sun_elevation, 2),
            
            # Key Current Conditions
            "condition": current_conditions.get("condition", {}).get("text"),
            "cloud_cover": f"{cloud_cover}%",
            "visibility_km": vis_km,
            "humidity": f"{current_conditions.get('humidity', 0)}%",
            "uv_index": current_conditions.get('uv', 0),
            
            # NEW Planning Attributes
            "sunrise": astro_data.get("sunrise"),
            "sunset": astro_data.get("sunset"),
            "moonrise": astro_data.get("moonrise"),
            "moonset": astro_data.get("moonset"),
            "moon_illumination": f"{moon_illumination}%",
            "chance_of_rain": f"{chance_of_rain}%",
            "feels_like_c": f"{current_conditions.get('feelslike_c', 0)}°C",
            
            # System
            "last_updated": current_conditions.get("last_updated"),
        }

