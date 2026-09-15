"""Las camaras, por los dos caminos que hay.

El proyecto ya resolvia "dos backends intercambiables, uno con capacidades
extra" en la capa de adaptador para la energia (`EstadoEnergia` mas
`_elegir_energia`). Las camaras lo resolvian otra vez, una capa mas arriba,
dentro de las herramientas: la misma escalera `if unifi... if ha...` escrita
tres veces, y un `id_protect` que a veces contenia un `entity_id` de Home
Assistant, discriminado mirando si empezaba por "camera.".

Aqui estan las dos fuentes a la altura que les toca. Con eso las herramientas
declaran `requiere="camaras"` y el panel puede pedirle los bytes al adaptador
en vez de a un handler privado.

Los dos caminos:

1. **UniFi Protect directo**, si el backend esta en la red de las camaras. Da
   mas datos: estado de grabacion, deteccion inteligente, eventos.
2. **Home Assistant**, via su proxy de camaras. Es la que permite que la vision
   siga funcionando con el backend fuera de casa, porque el que habla con las
   camaras es HA, que si esta dentro.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..settings import Camara, Inventario
from .base import AdapterError

if TYPE_CHECKING:
    from .homeassistant import HomeAssistant
    from .unifi import UniFi

# Limite prudente: una captura en alta calidad de una camara 4K puede ser
# grande y cada imagen ocupa contexto. Si se pasa, se pide calidad normal.
MAX_BYTES_IMAGEN = 4 * 1024 * 1024


@runtime_checkable
class FuenteCamaras(Protocol):
    """Lo que el resto del sistema puede pedirle a una fuente de camaras."""

    @property
    def configurado(self) -> bool: ...

    @property
    def via(self) -> str:
        """Como se llama este camino, para decirselo al modelo y al usuario."""
        ...

    @property
    def tiene_eventos(self) -> bool:
        """Si puede dar eventos, no solo capturas."""
        ...

    async def listar(self) -> dict[str, Any]: ...

    async def captura(self, camara: str) -> tuple[bytes, str]:
        """Devuelve `(jpeg, identificador usado)`."""
        ...

    async def eventos(self, horas: int) -> list[dict[str, Any]]: ...

    async def cerrar(self) -> None: ...


def _alias(inventario: Inventario) -> list[dict[str, Any]] | None:
    return [
        {"nombre": c.nombre, "id": c.id_protect, "entidad": c.entidad_ha, "zona": c.zona}
        for c in inventario.camaras
    ] or None


class CamarasProtect:
    """UniFi Protect directo."""

    via = "UniFi Protect"
    tiene_eventos = True

    def __init__(self, unifi: UniFi, inventario: Inventario) -> None:
        self._unifi = unifi
        self._inv = inventario

    @property
    def configurado(self) -> bool:
        return self._unifi.configurado

    async def cerrar(self) -> None:
        # El cliente es el del adaptador UniFi, que se cierra por su cuenta.
        return None

    async def listar(self) -> dict[str, Any]:
        return {
            "via": "unifi_protect",
            "camaras": await self._unifi.camaras(),
            "alias_configurados": _alias(self._inv),
        }

    async def captura(self, camara: str) -> tuple[bytes, str]:
        alias = self._inv.camara_por_nombre(camara)
        id_camara = alias.id_protect if alias and alias.id_protect else camara
        if alias is None or not alias.id_protect:
            # Puede que el usuario haya dicho el nombre que tiene en Protect.
            for c in await self._unifi.camaras():
                if str(c.get("nombre", "")).lower() == camara.strip().lower():
                    id_camara = str(c["id"])
                    break
        imagen = await self._unifi.snapshot(id_camara, alta_calidad=True)
        if len(imagen) > MAX_BYTES_IMAGEN:
            imagen = await self._unifi.snapshot(id_camara, alta_calidad=False)
        return imagen, id_camara

    async def eventos(self, horas: int) -> list[dict[str, Any]]:
        return await self._unifi.eventos(horas=horas)


class CamarasHA:
    """El proxy de camaras de Home Assistant."""

    via = "Home Assistant"
    tiene_eventos = False

    def __init__(self, ha: HomeAssistant, inventario: Inventario) -> None:
        self._ha = ha
        self._inv = inventario

    @property
    def configurado(self) -> bool:
        return self._ha.configurado

    async def cerrar(self) -> None:
        return None

    async def listar(self) -> dict[str, Any]:
        return {
            "via": "home_assistant",
            "camaras": await self._ha.buscar_entidades(dominio="camera"),
            "alias_configurados": _alias(self._inv),
            "nota": (
                "Leidas de Home Assistant. No hay estado de grabacion ni eventos: "
                "para eso hace falta acceso directo a UniFi Protect."
            ),
        }

    async def captura(self, camara: str) -> tuple[bytes, str]:
        from ..tools.comun import resolver_unica

        entity_id = _entidad_declarada(self._inv.camara_por_nombre(camara)) or camara
        if not entity_id.startswith("camera."):
            entity_id = resolver_unica(
                await self._ha.buscar_entidades(dominio="camera", texto=camara),
                camara,
                "camaras",
                "Usa camaras_listar para ver las que hay.",
            )
        return await self._ha.snapshot_camara(entity_id), entity_id

    async def eventos(self, horas: int) -> list[dict[str, Any]]:
        raise AdapterError(
            "Los eventos de camara solo estan disponibles con acceso directo a "
            "UniFi Protect. Por Home Assistant solo se pueden ver capturas. Si "
            "necesitas eventos, mira los sensores de movimiento con "
            "casa_buscar_entidades sobre el dominio binary_sensor."
        )


def _entidad_declarada(alias: Camara | None) -> str | None:
    """La entidad de HA de una camara del inventario, si se sabe.

    `entidad_ha` es el sitio correcto. Se sigue aceptando un `entity_id` puesto
    en `id_protect` porque era la unica forma de declararlo y hay casas con el
    YAML ya escrito asi.
    """
    if alias is None:
        return None
    if alias.entidad_ha:
        return alias.entidad_ha
    if alias.id_protect and alias.id_protect.startswith("camera."):
        return alias.id_protect
    return None


class SinCamaras:
    """No hay ningun camino a las camaras.

    Existe para que el error lo diga con esas palabras. Devolver la fuente de
    Protect sin configurar haria que el fallo hablase solo de UNIFI_HOST, y por
    Home Assistant tambien se ve, asi que el mensaje seria incompleto.
    """

    via = "ninguno"
    tiene_eventos = False
    configurado = False

    async def cerrar(self) -> None:
        return None

    def _no_hay(self) -> AdapterError:
        return AdapterError(
            "No hay camino a las camaras. Hacen falta UNIFI_HOST con UniFi "
            "Protect (da eventos y estado de grabacion) o HA_TOKEN con las "
            "camaras integradas en Home Assistant (solo capturas, pero funciona "
            "con el backend fuera de casa)."
        )

    async def listar(self) -> dict[str, Any]:
        raise self._no_hay()

    async def captura(self, camara: str) -> tuple[bytes, str]:
        raise self._no_hay()

    async def eventos(self, horas: int) -> list[dict[str, Any]]:
        raise self._no_hay()


def elegir_camaras(
    unifi: UniFi, ha: HomeAssistant, inventario: Inventario
) -> FuenteCamaras:
    """Protect directo si se puede, y si no el proxy de Home Assistant.

    Protect da mas (grabacion, eventos, deteccion inteligente); el proxy de HA
    es lo que sostiene la vision con el backend fuera de casa.
    """
    if unifi.configurado:
        return CamarasProtect(unifi, inventario)
    if ha.configurado:
        return CamarasHA(ha, inventario)
    return SinCamaras()
