"""Una lectura real de cada subsistema, con el resultado como datos.

Vivia dentro de `verificar.py` como una tira de `print`. Al pasar el sistema
a complemento de Home Assistant dejo de haber terminal, y la pregunta «¿que
ve Jarvis y que le falta?» se hace desde el movil: `/verificar` en Telegram
y `GET /verificar` en el API. Aqui esta lo que los tres comparten.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .app import Aplicacion

Estado = Literal["ok", "fallo", "sin_configurar"]
ICONO = {"ok": "✅", "fallo": "❌", "sin_configurar": "⊘"}


@dataclass
class Comprobacion:
    nombre: str
    estado: Estado
    detalle: str
    pista: str = ""

    def linea(self) -> str:
        texto = f"{ICONO[self.estado]} {self.nombre}: {self.detalle}"
        if self.estado != "ok" and self.pista:
            texto += f"\n   {self.pista}"
        return texto


async def _una(
    nombre: str, adaptador: Any, prueba: Callable[[], Awaitable[str]], pista: str
) -> Comprobacion:
    if not adaptador.configurado:
        return Comprobacion(nombre, "sin_configurar", "sin configurar", pista)
    try:
        return Comprobacion(nombre, "ok", await prueba())
    except Exception as e:  # noqa: BLE001 - frontera con hardware ajeno
        return Comprobacion(nombre, "fallo", str(e), pista)


async def comprobar_subsistemas(app: Aplicacion) -> list[Comprobacion]:
    """Lee de verdad cada subsistema configurado y dice que ha encontrado."""
    ctx = app.ctx
    ha, energia, knx, musica, unifi = ctx.ha, ctx.energia, ctx.knx, ctx.musica, ctx.unifi

    async def prueba_ha() -> str:
        estados = await ha.estados()
        dominios = {e["entity_id"].split(".", 1)[0] for e in estados}
        knx_entidades = [e for e in estados if "knx" in e["entity_id"].lower()]
        extra = f", {len(knx_entidades)} parecen de KNX/ONNA" if knx_entidades else ""
        return f"{len(estados)} entidades en {len(dominios)} dominios{extra}"

    async def prueba_energia() -> str:
        e = await energia.estado(usar_cache=False)
        control = "" if energia.puede_controlar else " (solo lectura)"
        soc = (
            "sin dato de bateria" if e.bateria_soc is None else f"bateria al {e.bateria_soc:.0f} %"
        )
        return f"{e.pv_w} W solares, {soc}, via {e.origen}{control}"

    async def prueba_knx() -> str:
        if not knx.direcciones:
            return "tunel disponible, pero no hay direcciones declaradas en el YAML"
        primera = knx.direcciones[0]
        lectura = await knx.leer(primera.nombre)
        return f"leido '{primera.nombre}' = {lectura['valor']}"

    async def prueba_musica() -> str:
        estados = await musica.estado_todos()
        vivos = [e for e in estados if "error" not in e]
        caidos = [e["reproductor"] for e in estados if "error" in e]
        texto = f"{len(vivos)}/{len(estados)} reproductores responden"
        return texto + (f" (sin respuesta: {', '.join(caidos)})" if caidos else "")

    async def prueba_unifi() -> str:
        salud = await unifi.salud()
        dispositivos = await unifi.dispositivos()
        wan = salud.get("wan", {})
        return (
            f"{len(dispositivos)} equipos, WAN {wan.get('estado', '?')} (IP {wan.get('ip', '?')})"
        )

    async def prueba_camaras() -> str:
        camaras = await unifi.camaras()
        nombres = ", ".join(f"{c['nombre']} [{c['id']}]" for c in camaras[:4])
        return f"{len(camaras)} camaras: {nombres}. Copia esos id a `camaras:` del inventario"

    salida = [
        await _una(
            "Home Assistant", ha, prueba_ha,
            "Pon HA_URL y HA_TOKEN. En el complemento viene solo.",
        ),
        await _una(
            "Energia (solar y bateria)", energia, prueba_energia,
            "Modbus con SUNGROW_HOST (o UniFi para buscar el registrador), o sensores "
            "de Home Assistant con la seccion `energia_ha:` del inventario.",
        ),
        await _una(
            "Bus KNX directo (ONNA)", knx, prueba_knx,
            "Opcional: solo para direcciones que Home Assistant no exponga. "
            "KNX_HABILITADO=true y KNX_GATEWAY_IP.",
        ),
        await _una(
            "Musica BluOS", musica, prueba_musica,
            "Declara los reproductores en `bluos:` del inventario. /descubrir los encuentra.",
        ),
        await _una(
            "Red UniFi", unifi, prueba_unifi,
            "UNIFI_HOST, UNIFI_USUARIO y UNIFI_PASSWORD, con una cuenta LOCAL de la "
            "consola, no la de ui.com.",
        ),
    ]
    if unifi.configurado:
        salida.append(
            await _una("Camaras UniFi Protect", unifi, prueba_camaras, "Necesita UniFi.")
        )
    return salida


def texto(comprobaciones: list[Comprobacion]) -> str:
    return "\n".join(c.linea() for c in comprobaciones)
