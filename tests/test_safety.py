"""La capa de seguridad es lo que hace aceptable dar a un modelo la llave de la casa."""

from __future__ import annotations

from typing import Any

import pytest

from casa_ai.adapters.base import AdapterError
from casa_ai.agent.registry import Contexto, Herramienta, Registro, Riesgo, esquema
from casa_ai.agent.safety import Ejecutor
from casa_ai.settings import Inventario, Settings
from casa_ai.store import Store

from .dobles import contexto


def _contexto(settings: Settings, store: Store, inventario: Inventario) -> Contexto:
    return contexto(
        settings, inventario, store, por_defecto=True,
        canal="telegram", usuario="123", conversacion="telegram:123",
    )


def _registro(ejecutadas: list[str]) -> Registro:
    async def peligrosa(_ctx: Contexto, valor: int) -> dict[str, Any]:
        ejecutadas.append(f"peligrosa:{valor}")
        return {"hecho": valor}

    async def inocua(_ctx: Contexto) -> dict[str, Any]:
        ejecutadas.append("inocua")
        return {"ok": True}

    async def rota(_ctx: Contexto) -> dict[str, Any]:
        raise AdapterError("el inversor no responde")

    async def explota(_ctx: Contexto) -> dict[str, Any]:
        raise ValueError("bug interno")

    registro = Registro()
    registro.anadir(
        Herramienta(
            nombre="peligrosa",
            descripcion="d" * 50,
            esquema=esquema({"valor": {"type": "integer"}}, obligatorias=["valor"]),
            riesgo=Riesgo.ALTO,
            handler=peligrosa,
            resumen_confirmacion=lambda a: f"Hacer algo gordo con {a['valor']}",
        ),
        Herramienta(
            nombre="inocua", descripcion="d" * 50, esquema=esquema({}),
            riesgo=Riesgo.LECTURA, handler=inocua,
        ),
        Herramienta(
            nombre="rota", descripcion="d" * 50, esquema=esquema({}),
            riesgo=Riesgo.MEDIO, handler=rota,
        ),
        Herramienta(
            nombre="explota", descripcion="d" * 50, esquema=esquema({}),
            riesgo=Riesgo.MEDIO, handler=explota,
        ),
    )
    return registro


@pytest.fixture
def entorno(settings: Settings, store: Store, inventario: Inventario):
    ejecutadas: list[str] = []
    ctx = _contexto(settings, store, inventario)
    return Ejecutor(_registro(ejecutadas), ctx), ejecutadas, store


def _token_de(store: Store, conversacion: str = "telegram:123") -> str:
    return store.pendientes_de(conversacion)[0]["token"]


async def test_riesgo_alto_no_se_ejecuta_a_la_primera(entorno) -> None:
    ejecutor, ejecutadas, store = entorno
    store.nuevo_turno("telegram:123")
    resultado, es_error = await ejecutor.ejecutar("peligrosa", {"valor": 7})

    assert es_error is False
    assert ejecutadas == []  # lo importante: NO se ha tocado nada
    assert "REQUIERE CONFIRMACION" in resultado
    assert "Hacer algo gordo con 7" in resultado
    # El token existe, guardado; su VALOR no aparece en nada que lea el modelo.
    assert _token_de(store) not in resultado
    assert "«si»" in resultado


async def test_el_modelo_no_tiene_ninguna_herramienta_para_confirmar(entorno) -> None:
    """Es la garantia: una inyeccion no tiene a que apuntar. Antes existia
    `ejecutar_accion_pendiente` y el token viajaba en el contexto."""
    ejecutor, ejecutadas, store = entorno
    store.nuevo_turno("telegram:123")
    await ejecutor.ejecutar("peligrosa", {"valor": 42})
    token = _token_de(store)

    resultado, es_error = await ejecutor.ejecutar("ejecutar_accion_pendiente", {"token": token})

    assert es_error is True and "No existe" in resultado
    assert ejecutadas == []
    assert len(store.pendientes_de("telegram:123")) == 1  # sigue pendiente


async def test_confirmar_ejecuta_la_accion_original(entorno) -> None:
    ejecutor, ejecutadas, store = entorno
    store.nuevo_turno("telegram:123")
    await ejecutor.ejecutar("peligrosa", {"valor": 42})

    resultado, es_error = await ejecutor.confirmar(_token_de(store))

    assert es_error is False
    assert resultado == {"hecho": 42}
    assert ejecutadas == ["peligrosa:42"]


async def test_token_invalido_no_ejecuta_nada(entorno) -> None:
    ejecutor, ejecutadas, _ = entorno
    resultado, es_error = await ejecutor.confirmar("inventado")
    assert es_error is True
    assert "no existe" in resultado
    assert ejecutadas == []


async def test_confirmacion_no_es_reutilizable(entorno) -> None:
    ejecutor, ejecutadas, store = entorno
    store.nuevo_turno("telegram:123")
    await ejecutor.ejecutar("peligrosa", {"valor": 1})
    token = _token_de(store)

    await ejecutor.confirmar(token)
    _, es_error = await ejecutor.confirmar(token)

    assert es_error is True
    assert ejecutadas == ["peligrosa:1"]  # una sola vez


async def test_riesgo_bajo_se_ejecuta_directo(entorno) -> None:
    ejecutor, ejecutadas, _ = entorno
    resultado, es_error = await ejecutor.ejecutar("inocua", {})
    assert es_error is False
    assert resultado == {"ok": True}
    assert ejecutadas == ["inocua"]


async def test_sin_confirmacion_configurada_se_ejecuta_directo(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    sin_confirmacion = settings.model_copy(update={"exigir_confirmacion": False})
    ejecutadas: list[str] = []
    ctx = _contexto(sin_confirmacion, store, inventario)
    ejecutor = Ejecutor(_registro(ejecutadas), ctx)

    resultado, es_error = await ejecutor.ejecutar("peligrosa", {"valor": 3})

    assert es_error is False
    assert resultado == {"hecho": 3}
    assert ejecutadas == ["peligrosa:3"]


async def test_error_de_adaptador_llega_como_texto_no_como_excepcion(entorno) -> None:
    ejecutor, _, _ = entorno
    resultado, es_error = await ejecutor.ejecutar("rota", {})
    assert es_error is True
    assert "el inversor no responde" in resultado


async def test_excepcion_inesperada_se_contiene(entorno) -> None:
    ejecutor, _, _ = entorno
    resultado, es_error = await ejecutor.ejecutar("explota", {})
    assert es_error is True
    assert "ValueError" in resultado


async def test_herramienta_desconocida(entorno) -> None:
    ejecutor, _, _ = entorno
    resultado, es_error = await ejecutor.ejecutar("no_existe", {})
    assert es_error is True
    assert "No existe la herramienta" in resultado


async def test_las_acciones_quedan_auditadas(entorno) -> None:
    ejecutor, _, store = entorno
    store.nuevo_turno("telegram:123")
    await ejecutor.ejecutar("peligrosa", {"valor": 5})
    await ejecutor.confirmar(_token_de(store))

    registros = store.auditoria(10)
    herramientas = [r["herramienta"] for r in registros]
    assert herramientas.count("peligrosa") == 2  # la propuesta y la ejecucion
    assert any("confirmada" in r["riesgo"] for r in registros)


async def test_las_lecturas_no_llenan_la_auditoria(entorno) -> None:
    ejecutor, _, store = entorno
    await ejecutor.ejecutar("inocua", {})
    assert store.auditoria(10) == []


async def test_el_token_no_se_escribe_en_la_auditoria(entorno) -> None:
    """La auditoria la puede leer una herramienta de solo lectura y un
    endpoint HTTP; el token es la credencial que autoriza la accion."""
    ejecutor, _, store = entorno
    store.nuevo_turno("telegram:123")
    await ejecutor.ejecutar("peligrosa", {"valor": 3})
    token = _token_de(store)

    for registro in store.auditoria(10):
        assert token not in registro["resultado"]
        assert token not in registro["argumentos"]


# --- Confirmacion fuera de banda --------------------------------------------


async def test_con_botones_el_token_no_llega_al_modelo(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    """Es el punto de todo el mecanismo: una inyeccion de prompt no tiene con
    que trabajar si el token nunca entra en el contexto."""
    ejecutadas: list[str] = []
    ctx = _contexto(settings, store, inventario)
    ctx.confirmacion = "boton"
    ejecutor = Ejecutor(_registro(ejecutadas), ctx)
    store.nuevo_turno(ctx.conversacion)

    aviso, es_error = await ejecutor.ejecutar("peligrosa", {"valor": 7})

    assert es_error is False
    assert ejecutadas == []
    assert "boton" in aviso

    # El token existe y esta guardado esperando la pulsacion, pero su VALOR no
    # aparece en nada que el modelo pueda leer. Es la propiedad que importa: el
    # mensaje puede hablar del token, lo que no puede es contenerlo.
    pendientes = store.pendientes_de(ctx.conversacion)
    assert len(pendientes) == 1
    assert pendientes[0]["token"] not in aviso


async def test_el_boton_ejecuta_la_accion_propuesta(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ejecutadas: list[str] = []
    ctx = _contexto(settings, store, inventario)
    ctx.confirmacion = "boton"
    ejecutor = Ejecutor(_registro(ejecutadas), ctx)
    store.nuevo_turno(ctx.conversacion)

    await ejecutor.ejecutar("peligrosa", {"valor": 42})
    token = store.pendientes_de(ctx.conversacion)[0]["token"]

    resultado, es_error = await ejecutor.confirmar(token)

    assert es_error is False
    assert resultado == {"hecho": 42}
    assert ejecutadas == ["peligrosa:42"]


async def test_el_boton_tampoco_sirve_dos_veces(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ejecutadas: list[str] = []
    ctx = _contexto(settings, store, inventario)
    ctx.confirmacion = "boton"
    ejecutor = Ejecutor(_registro(ejecutadas), ctx)
    store.nuevo_turno(ctx.conversacion)

    await ejecutor.ejecutar("peligrosa", {"valor": 1})
    token = store.pendientes_de(ctx.conversacion)[0]["token"]

    await ejecutor.confirmar(token)
    _, es_error = await ejecutor.confirmar(token)

    assert es_error is True
    assert ejecutadas == ["peligrosa:1"]


async def test_un_token_inventado_en_el_boton_no_ejecuta_nada(
    settings: Settings, store: Store, inventario: Inventario
) -> None:
    ejecutadas: list[str] = []
    ctx = _contexto(settings, store, inventario)
    ejecutor = Ejecutor(_registro(ejecutadas), ctx)

    resultado, es_error = await ejecutor.confirmar("inventado")

    assert es_error is True
    assert "no existe" in resultado
    assert ejecutadas == []
