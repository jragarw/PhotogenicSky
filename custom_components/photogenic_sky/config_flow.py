"""Config flow for Photogenic Sky integration."""
import logging

from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class PhotogenicSkyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Photogenic Sky."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the user step."""
        # Check if an entry is already configured. We only allow one.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            # User has confirmed. Create the entry.
            # The title will be the location name from HA config.
            # The data payload is empty as we don't need to store anything.
            location_name = self.hass.config.location_name
            return self.async_create_entry(title=location_name, data={})

        # Show a simple confirmation form to the user.
        return self.async_show_form(step_id="user")

