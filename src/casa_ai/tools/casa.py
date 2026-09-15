"""Herramientas de domotica: Home Assistant (frontal de ONNA/KNX) y bus KNX directo."""

from __future__ import annotations

from typing import Any

from ..adapters.base import AdapterError
from ..adapters.homeassistant import SERVICIOS_QUE_ABREN, es_acceso
from ..agent.registry import Contexto, Herramienta, Riesgo, esquema
from .comun import resolver_unica

# Atributos que Home Assistant devuelve siempre y que solo gastan contexto.
_ATRIBUTOS_RUIDO = frozenset(
    {"icon", "supported_features", "supported_color_modes", "hvac_modes"}
)

# Rango razonable de temperatura por dominio. No es lo mismo un termostato de
# habitacion que el agua de un spa o un termo: con el rango de habitacion,
# "pon el spa a 38 grados" se rechazaba como absurdo.
_RANGO_TEMPERATURA: dict[str, tuple[float, float]] = {
    "climate": (5, 32),
    "water_heater": (20, 75),
}

# El servicio y el parametro para cambiar de modo cambian con el dominio.
_SERVICIO_MODO: dict[str, tuple[str, str]] = {
    "climate": ("set_hvac_mode", "hvac_mode"),
    "water_heater": ("set_operation_mode", "operation_mode"),
}


def _resolver_alias(ctx: Contexto, referencia: str) -> str:
    return ctx.inventario.resolver_alias(referencia)


async def _buscar(
    ctx: Contexto, dominio: str | None = None, texto: str | None = None
) -> dict[str, Any]:
    entidades = await ctx.ha.buscar_entidades(dominio=dominio, texto=texto)
    return {
        "encontradas": len(entidades),
        "entidades": entidades,
        "alias_configurados": ctx.inventario.alias_entidades or None,
    }


async def _estado(ctx: Contexto, entidad: str) -> dict[str, Any]:
    eid = _resolver_alias(ctx, entidad)
    datos = await ctx.ha.estado(eid)
    atributos = datos.get("attributes", {}) or {}
    return {
        "entity_id": datos.get("entity_id"),
        "nombre": atributos.get("friendly_name"),
        "estado": datos.get("state"),
        "atributos": {
            k: v
            for k, v in atributos.items()
            # Las listas de modos soportados y los iconos solo gastan contexto.
            if k not in _ATRIBUTOS_RUIDO
        },
        "ultimo_cambio": datos.get("last_changed"),
    }


_SERVICIOS: dict[str, str] = {
    "encender": "turn_on",
    "apagar": "turn_off",
    "alternar": "toggle",
    "subir": "open_cover",
    "bajar": "close_cover",
    "parar": "stop_cover",
    "posicion": "set_cover_position",
    "temperatura": "set_temperature",
    "brillo": "turn_on",
    "activar": "turn_on",
    "pulsar": "press",
}


def _porcentaje(valor: float) -> int:
    return int(max(0, min(100, valor)))


async def _accion(
    ctx: Contexto,
    entidad: str,
    accion: str,
    valor: float | None = None,
    temperatura: float | None = None,
    modo: str | None = None,
) -> dict[str, Any]:
    eid = _resolver_alias(ctx, entidad)
    if "." not in eid:
        raise AdapterError(
            f"'{entidad}' no es un entity_id valido ni un alias conocido. "
            "Usa casa_buscar_entidades para localizar la entidad correcta."
        )
    dominio = eid.split(".", 1)[0]
    parametros: dict[str, Any] = {}

    nombre_accion = accion.strip().lower()
    if nombre_accion not in _SERVICIOS and nombre_accion != "modo":
        raise AdapterError(
            f"'{accion}' no es una accion de casa_accion. Las que hay: "
            f"{', '.join(sorted([*_SERVICIOS, 'modo']))}."
        )
    servicio = _SERVICIOS.get(nombre_accion, nombre_accion)

    # "modo" depende del dominio: un clima usa set_hvac_mode/hvac_mode y un
    # termo o spa usa set_operation_mode/operation_mode. Antes mapeaba fijo a
    # set_hvac_mode y nunca enviaba el modo, asi que la llamada no hacia nada.
    if nombre_accion == "modo":
        par = _SERVICIO_MODO.get(dominio)
        if par is None:
            raise AdapterError(
                f"No se cambiar de modo una entidad '{dominio}'. Los modos son de "
                "climatizacion (climate) y de agua caliente (water_heater)."
            )
        servicio, clave = par
        if not modo:
            # Se intenta enriquecer el error con los modos que admite la
            # entidad, pero sin que la ruta de error dependa de la red: si no
            # se puede leer, el mensaje basico ya es accionable.
            posibles: Any = None
            try:
                estado = await ctx.ha.estado(eid)
                posibles = (estado.get("attributes", {}) or {}).get(
                    "hvac_modes" if dominio == "climate" else "operation_list"
                )
            except AdapterError:
                pass
            raise AdapterError(
                "Para 'modo' hace falta indicar cual en el argumento `modo`."
                + (
                    f" Los de '{eid}' son: {', '.join(map(str, posibles))}."
                    if posibles
                    else " Mira casa_estado para ver los que admite la entidad."
                )
            )
        parametros[clave] = modo

    # El despacho va por la accion que pidio el usuario, no por el servicio:
    # "encender" y "brillo" comparten `turn_on`, asi que el servicio no es
    # clave unica y este if/elif miraba dos claves distintas segun la rama.
    elif nombre_accion == "posicion":
        if valor is None:
            raise AdapterError(
                "Para 'posicion' hace falta un valor de 0 (cerrada) a 100 (abierta)."
            )
        parametros["position"] = _porcentaje(valor)
    elif nombre_accion == "temperatura":
        objetivo = temperatura if temperatura is not None else valor
        if objetivo is None:
            raise AdapterError("Para 'temperatura' hace falta el valor en grados.")
        minimo, maximo = _RANGO_TEMPERATURA.get(dominio, (5, 75))
        if not minimo <= objetivo <= maximo:
            raise AdapterError(
                f"{objetivo} C esta fuera del rango razonable para un "
                f"'{dominio}' ({minimo:.0f}-{maximo:.0f} C)."
            )
        parametros["temperature"] = float(objetivo)
    elif nombre_accion == "brillo":
        if valor is None:
            raise AdapterError("Para 'brillo' hace falta un valor de 0 a 100.")
        parametros["brightness_pct"] = _porcentaje(valor)

    if dominio == "cover" and servicio in SERVICIOS_QUE_ABREN:
        # Se mira al final, con los argumentos ya validados: la lectura del
        # estado es una peticion a HA y no hace falta para rechazar un valor.
        if es_acceso(await ctx.ha.estado(eid)):
            raise AdapterError(
                f"'{eid}' es una puerta de garaje, un porton o una puerta, no una "
                "persiana. Abrirla es abrir la casa: usa casa_abrir_acceso, que pide "
                "confirmacion."
            )
    await ctx.ha.llamar_servicio(dominio, servicio, {"entity_id": eid, **parametros})
    return {
        "entity_id": eid,
        "servicio": f"{dominio}.{servicio}",
        "parametros": parametros,
        "detalle": "Orden enviada a Home Assistant.",
    }


async def _abrir_acceso(ctx: Contexto, entidad: str) -> dict[str, Any]:
    """Abre una puerta de garaje, un porton o una puerta motorizada.

    Herramienta aparte de `casa_accion` por lo mismo que `lock.unlock` no esta
    en la lista blanca: abrir la casa no es mover una persiana. Es de riesgo
    alto, exige confirmacion y no es para ninos.
    """
    eid = _resolver_alias(ctx, entidad)
    if not eid.startswith("cover."):
        raise AdapterError(f"'{entidad}' no es un cover de Home Assistant.")
    if not es_acceso(await ctx.ha.estado(eid)):
        raise AdapterError(
            f"'{eid}' no es una puerta ni un porton (device_class garage, gate o "
            "door): es una persiana o un toldo, y eso va por casa_accion."
        )
    await ctx.ha.llamar_servicio("cover", "open_cover", {"entity_id": eid})
    return {"entity_id": eid, "detalle": "Abriendo. Acuerdate de cerrarla."}


async def _escena(ctx: Contexto, nombre: str) -> dict[str, Any]:
    eid = _resolver_alias(ctx, nombre)
    if not eid.startswith(("scene.", "script.")):
        eid = resolver_unica(
            await ctx.ha.buscar_entidades(dominio="scene", texto=nombre),
            nombre,
            "escenas",
            "Usa casa_buscar_entidades para ver las que hay.",
        )
    dominio = eid.split(".", 1)[0]
    await ctx.ha.llamar_servicio(dominio, "turn_on", {"entity_id": eid})
    return {"escena": eid, "detalle": "Escena activada."}


async def _knx_listar(ctx: Contexto) -> dict[str, Any]:
    return {
        "aviso": (
            "Estas son las unicas direcciones de grupo permitidas. Prefiere las "
            "entidades de Home Assistant siempre que existan."
        ),
        "direcciones": ctx.knx.listar(),
    }


async def _knx_leer(ctx: Contexto, direccion: str) -> dict[str, Any]:
    return await ctx.knx.leer(direccion)


async def _knx_escribir(ctx: Contexto, direccion: str, valor: Any) -> dict[str, Any]:
    return await ctx.knx.escribir(direccion, valor)


HERRAMIENTAS = [
    Herramienta(
        nombre="casa_buscar_entidades",
        descripcion=(
            "Busca entidades de la casa en Home Assistant por dominio (light, "
            "switch, cover, climate, sensor, scene, media_player, binary_sensor...) "
            "y/o por texto en el nombre. Empieza siempre por aqui cuando no sepas "
            "el entity_id exacto: nunca adivines un entity_id."
        ),
        esquema=esquema(
            {
                "dominio": {"type": "string", "description": "light, cover, climate, sensor..."},
                "texto": {"type": "string", "description": "Texto a buscar en nombre o id"},
            }
        ),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_buscar,
        requiere="ha",
    ),
    Herramienta(
        nombre="casa_estado",
        descripcion=(
            "Estado y atributos actuales de una entidad concreta "
            "(o de un alias configurado)."
        ),
        esquema=esquema(
            {"entidad": {"type": "string", "description": "entity_id o alias"}},
            obligatorias=["entidad"],
        ),
        riesgo=Riesgo.LECTURA,
        para_ninos=True,
        handler=_estado,
        requiere="ha",
    ),
    Herramienta(
        nombre="casa_accion",
        descripcion=(
            "Actua sobre una entidad de la casa. Acciones: encender, apagar, "
            "alternar, brillo (valor 0-100), subir/bajar/parar (persianas), "
            "posicion (valor 0-100), temperatura (grados, sirve tanto para "
            "climatizacion como para agua caliente), modo (indica cual en el "
            "argumento `modo`), activar, pulsar. La domotica de ONNA llega al bus "
            "KNX a traves de Home Assistant. Son cambios reversibles, no hace "
            "falta confirmacion."
        ),
        esquema=esquema(
            {
                "entidad": {"type": "string", "description": "entity_id o alias"},
                "accion": {"type": "string", "enum": sorted([*_SERVICIOS, "modo"])},
                "valor": {"type": "number", "description": "Para brillo, posicion o temperatura"},
                "temperatura": {"type": "number", "description": "Grados objetivo"},
                "modo": {
                    "type": "string",
                    "description": (
                        "Para la accion 'modo': el modo exacto de la entidad, p.ej. "
                        "heat, cool, off, eco. Si no lo sabes, mira casa_estado."
                    ),
                },
            },
            obligatorias=["entidad", "accion"],
        ),
        riesgo=Riesgo.MEDIO,
        para_ninos=True,
        handler=_accion,
        requiere="ha",
    ),
    Herramienta(
        nombre="casa_abrir_acceso",
        descripcion=(
            "Abre una puerta de garaje, un porton o una puerta motorizada (un cover "
            "con device_class garage, gate o door). Es abrir la casa: solo cuando el "
            "usuario lo pida de forma explicita, y siempre pide confirmacion. Para "
            "persianas y toldos usa casa_accion."
        ),
        esquema=esquema(
            {"entidad": {"type": "string", "description": "Alias o entity_id cover.*"}},
            obligatorias=["entidad"],
        ),
        riesgo=Riesgo.ALTO,
        handler=_abrir_acceso,
        resumen_confirmacion=lambda a: f"Abrir {a.get('entidad')} (puerta o porton)",
        requiere="ha",
    ),
    Herramienta(
        nombre="casa_escena",
        descripcion="Activa una escena o script de Home Assistant por nombre o entity_id.",
        esquema=esquema({"nombre": {"type": "string"}}, obligatorias=["nombre"]),
        riesgo=Riesgo.MEDIO,
        handler=_escena,
        requiere="ha",
    ),
    Herramienta(
        nombre="knx_listar_direcciones",
        descripcion=(
            "Lista las direcciones de grupo KNX declaradas del sistema ONNA. Solo "
            "para lo que no este expuesto como entidad en Home Assistant."
        ),
        esquema=esquema({}),
        riesgo=Riesgo.LECTURA,
        handler=_knx_listar,
        requiere="knx",
    ),
    Herramienta(
        nombre="knx_leer",
        descripcion="Lee el valor actual de una direccion de grupo KNX declarada del bus de ONNA.",
        esquema=esquema(
            {"direccion": {"type": "string", "description": "Nombre declarado o 'x/y/z'"}},
            obligatorias=["direccion"],
        ),
        riesgo=Riesgo.LECTURA,
        handler=_knx_leer,
        requiere="knx",
    ),
    Herramienta(
        nombre="knx_escribir",
        descripcion=(
            "Escribe un telegrama directamente en el bus KNX de ONNA. Es la via de "
            "ultimo recurso: actua sobre el actuador sin pasar por Home Assistant y "
            "sin confirmacion de ejecucion del bus. Solo acepta direcciones "
            "declaradas en la configuracion."
        ),
        esquema=esquema(
            {
                "direccion": {"type": "string", "description": "Nombre declarado o 'x/y/z'"},
                "valor": {
                    "type": ["boolean", "number", "string"],
                    "description": "Valor acorde al DPT de esa direccion",
                },
            },
            obligatorias=["direccion", "valor"],
        ),
        riesgo=Riesgo.ALTO,
        handler=_knx_escribir,
        resumen_confirmacion=lambda a: (
            f"Escribir {a.get('valor')} directamente en la direccion KNX "
            f"'{a.get('direccion')}' del bus de ONNA"
        ),
        requiere="knx",
    ),
]
