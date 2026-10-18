"""Credentials are entered in HA and validated before creating a config entry."""

from __future__ import annotations

from hashlib import sha256

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector

from .const import DOMAIN
from .models import AuthenticationError, ConnectionError, InteractiveLoginRequired, PortalChanged
from .transport import create_client


def credentials_schema(username: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class TauronConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, user_input: dict) -> str | None:
        client, transport = create_client(
            self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
        )
        try:
            await client.verify_login()
        except InteractiveLoginRequired:
            return "interactive_login"
        except AuthenticationError:
            return "invalid_auth"
        except ConnectionError:
            return "cannot_connect"
        except PortalChanged as err:
            return err.ui_error
        finally:
            await transport.close()
        return None

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            user_input = {**user_input, CONF_USERNAME: user_input[CONF_USERNAME].strip()}
            if not user_input[CONF_USERNAME] or not user_input[CONF_PASSWORD]:
                errors["base"] = "invalid_auth"
            else:
                identity = sha256(user_input[CONF_USERNAME].casefold().encode()).hexdigest()
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                error = await self._validate(user_input)
                if error is None:
                    return self.async_create_entry(title="TAURON eBOK", data=user_input)
                errors["base"] = error
        username = user_input[CONF_USERNAME] if user_input is not None else ""
        return self.async_show_form(
            step_id="user", data_schema=credentials_schema(username), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            # Reauth changes the password only, never the account or entity identity.
            updated = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            error = await self._validate(updated)
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates=updated)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )
