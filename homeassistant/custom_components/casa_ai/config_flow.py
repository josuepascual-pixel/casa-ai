"""Alta de la integracion: la URL del backend y el token del API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from homeassistant import config_entries

from .const import CONF_TOKEN, CONF_URL, DOMINIO

ESQUEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="http://localhost:8099"): str,
        vol.Required(CONF_TOKEN): str,
    }
)


class CasaAIConfigFlow(config_entries.ConfigFlow, domain=DOMINIO):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errores: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            sesion = async_get_clientsession(self.hass)
            try:
                async with sesion.get(
                    f"{url}/salud",
                    headers={"Authorization": f"Bearer {user_input[CONF_TOKEN]}"},
                    timeout=10,
                ) as resp:
                    if resp.status == 401:
                        errores["base"] = "token_incorrecto"
                    elif resp.status != 200:
                        errores["base"] = "no_responde"
            except Exception:  # noqa: BLE001 - cualquier fallo de red es "no responde"
                errores["base"] = "no_responde"
            if not errores:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Casa AI", data={CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN]}
                )
        return self.async_show_form(step_id="user", data_schema=ESQUEMA, errors=errores)
