"""Config flow for Photogenic Sky integration."""
import logging
import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN, CONF_LOCATION_NAME

_LOGGER = logging.getLogger(__name__)

async def _get_geocode(session: aiohttp.ClientSession, location_name: str) -> dict:
    """Get latitude and longitude from a location name using Nominatim."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {'q': location_name, 'format': 'json', 'limit': 1}
    headers = {"User-Agent": "PhotogenicSkyHA/2.1"}

    async with session.get(url, params=params, headers=headers) as response:
        response.raise_for_status()
        results = await response.json()
        if not results:
            raise LocationNotFound
        
        return {
            "latitude": float(results[0]["lat"]),
            "longitude": float(results[0]["lon"]),
            "display_name": results[0]["display_name"]
        }

class PhotogenicSkyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Photogenic Sky."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        # This is needed for future options handling, good practice to have it.
        return PhotogenicSkyOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the user step."""
        errors = {}
        if user_input is not None:
            location_name = user_input[CONF_LOCATION_NAME]
            try:
                async with aiohttp.ClientSession() as session:
                    geocode_data = await _get_geocode(session, location_name)
                
                await self.async_set_unique_id(geocode_data["display_name"])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=geocode_data["display_name"], 
                    data={
                        "latitude": geocode_data["latitude"],
                        "longitude": geocode_data["longitude"],
                        CONF_LOCATION_NAME: geocode_data["display_name"]
                    }
                )
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except LocationNotFound:
                errors["base"] = "location_not_found"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_LOCATION_NAME): str}),
            errors=errors,
        )

class PhotogenicSkyOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow."""
    # This is empty for now, but provides the structure for future user settings.
    def __init__(self, config_entry):
        self.config_entry = config_entry

class LocationNotFound(Exception):
    """Error to indicate the location could not be found."""

