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

        score = 100
        current_conditions = data.get("current", {})
        
        cloud_cover = current_conditions.get("cloud", 100)
        if cloud_cover > 75: score -= 30
        elif cloud_cover > 50: score -= 10

        vis_km = current_conditions.get("vis_km", 0)
        if vis_km < 5: score -= 25
        elif vis_km < 10: score -= 10

        if current_conditions.get("precip_mm", 0) > 0: score -= 40
        if current_conditions.get("wind_kph", 0) > 30: score -= 15

        self._photogenic_score = max(0, score)
        self._api_data = {
            "location": data.get("location", {}).get("name"),
            "cloud_cover": f"{cloud_cover}%",
            "visibility_km": vis_km,
            "wind_kph": current_conditions.get("wind_kph", 0),
            "condition": current_conditions.get("condition", {}).get("text"),
            "last_updated": current_conditions.get("last_updated"),
        }