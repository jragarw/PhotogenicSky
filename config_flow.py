"""Config flow for Photogenic Sky integration."""
import logging
import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY

from .const import DOMAIN, CONF_LOCATION

_LOGGER = logging.getLogger(__name__)

class PhotogenicSkyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Photogenic Sky."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                await self._validate_api_key(user_input[CONF_API_KEY], user_input[CONF_LOCATION])
                await self.async_set_unique_id(user_input[CONF_LOCATION].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_LOCATION], data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_LOCATION): str,
            }),
            errors=errors,
        )

    async def _validate_api_key(self, api_key: str, location: str):
        """Validate the API key by making a test call."""
        url = f"http://api.weatherapi.com/v1/current.json?key={api_key}&q={location}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 401:
                    raise InvalidAuth
                if response.status >= 400:
                    raise CannotConnect
                return True

class CannotConnect(Exception):
    """Error to indicate we cannot connect."""

class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""