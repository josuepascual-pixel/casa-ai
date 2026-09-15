"""Casa AI como agente de conversacion de Home Assistant.

Lo que se dice a un satelite de voz (o se escribe en Assist) va al backend
de Casa AI por HTTP, con el identificador del aparato, y la respuesta vuelve
al pipeline para que la lea la voz de Jarvis. Toda la seguridad (quien es
quien, que puede pedir, confirmaciones) esta en el backend, no aqui.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATAFORMAS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATAFORMAS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATAFORMAS)
