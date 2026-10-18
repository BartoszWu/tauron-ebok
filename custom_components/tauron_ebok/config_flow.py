"""Credentials are entered in HA and validated before creating a config entry."""

from __future__ import annotations

import logging
from hashlib import sha256

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN
from .history import HistoryAccount, HistoryChanged
from .history_coordinator import HISTORY_TARGET
from .models import AuthenticationError, ConnectionError, InteractiveLoginRequired, PortalChanged
from .transport import create_client

_LOGGER = logging.getLogger(__name__)


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return TauronOptionsFlow()

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


class TauronOptionsFlow(OptionsFlow):
    """Explicit selection; no consumption is fetched for unselected contracts."""

    def __init__(self) -> None:
        self._accounts: dict[str, HistoryAccount] = {}
        self._account: HistoryAccount | None = None

    async def _discover(self) -> str | None:
        entry = self.config_entry
        client, transport = create_client(
            self.hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
        try:
            self._accounts = {a.key: a for a in await client.history_accounts()}
        except InteractiveLoginRequired:
            return "interactive_login"
        except AuthenticationError:
            return "invalid_auth"
        except ConnectionError:
            return "cannot_connect"
        except HistoryChanged as err:
            # Messages originate only in our adapter and contain no raw portal
            # values, URLs, account IDs or response bodies.
            _LOGGER.warning("History selection adapter: %s", err)
            return "portal_history"
        except PortalChanged:
            return "portal_history"
        finally:
            await transport.close()
        return None

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            if not user_input["enable_history"]:
                options = dict(self.config_entry.options)
                options.pop(HISTORY_TARGET, None)
                return self.async_create_entry(title="", data=options)
            error = await self._discover()
            if error:
                errors["base"] = error
            elif len(self._accounts) == 1:
                self._account = next(iter(self._accounts.values()))
                return await self.async_step_point()
            else:
                return await self.async_step_account()
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "enable_history",
                        default=bool(self.config_entry.options.get(HISTORY_TARGET)),
                    ): bool,
                }
            ),
        )

    async def async_step_account(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._account = self._accounts.get(user_input["account"])
            if self._account is not None:
                return await self.async_step_point()
            errors["base"] = "portal_history"
        choices = {key: f"{a.system} · …{a.billing[-4:]}" for key, a in self._accounts.items()}
        return self.async_show_form(
            step_id="account",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("account"): vol.In(choices),
                }
            ),
        )

    async def async_step_point(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if self._account is None:
            return self.async_abort(reason="history_selection_required")
        if user_input is not None:
            point = str(user_input.get("point", "")).strip()
            if point and len(point) <= 64:
                options = dict(self.config_entry.options)
                options[HISTORY_TARGET] = {
                    "system": self._account.system,
                    "billing": self._account.billing,
                    "pod": point,
                }
                return self.async_create_entry(title="", data=options)
            errors["base"] = "invalid_point"
        return self.async_show_form(
            step_id="point",
            errors=errors,
            data_schema=vol.Schema({vol.Required("point"): str}),
        )
