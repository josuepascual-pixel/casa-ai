"""El agente de conversacion: cada frase va al backend con el aparato que la oyo."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_TOKEN, CONF_URL


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([AgenteCasaAI(entry)])


class AgenteCasaAI(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        # La identidad es el usuario de Home Assistant cuando lo hay: cualquier
        # cuenta de HA puede mandar `device_id` a mano por la API de
        # conversacion, asi que el aparato solo vale cuando no hay usuario
        # detras (un satelite cuyo pipeline lanza el propio HA).
        usuario = user_input.context.user_id
        if usuario:
            dispositivo = f"usuario-{usuario}"
        else:
            dispositivo = user_input.device_id or "sin-identidad"
        sesion = async_get_clientsession(self.hass)
        try:
            async with sesion.post(
                f"{self.entry.data[CONF_URL]}/voz",
                json={"dispositivo": dispositivo, "texto": user_input.text},
                headers={"Authorization": f"Bearer {self.entry.data[CONF_TOKEN]}"},
                timeout=120,
            ) as resp:
                if resp.status != 200:
                    texto = f"Casa AI ha devuelto {resp.status}. Mira el registro del complemento."
                else:
                    texto = (await resp.json())["respuesta"]
        except Exception as e:  # noqa: BLE001 - la voz tiene que decir algo, no callarse
            texto = f"No he podido hablar con Casa AI: {e}"

        respuesta = intent.IntentResponse(language=user_input.language)
        respuesta.async_set_speech(texto)
        return conversation.ConversationResult(
            response=respuesta, conversation_id=user_input.conversation_id
        )
