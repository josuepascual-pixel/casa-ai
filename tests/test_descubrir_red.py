"""Interioridades del descubrimiento: sondeo de puertos e identificacion.

Es el primer comando que se ejecuta en la maquina de destino, asi que conviene
que funcione. El sondeo se prueba contra un socket local de verdad, no contra
un doble: lo que importa es que distinga un puerto abierto de uno cerrado.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import respx

from casa_ai.descubrir import (
    Hallazgo,
    _examinar,
    _identificar_bluos,
    _main,
    _puerto_abierto,
    informe,
    subred_local,
)


@pytest.fixture
async def puerto_escuchando():
    """Un servidor TCP de verdad en un puerto libre."""
    servidor = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    puerto = servidor.sockets[0].getsockname()[1]
    yield puerto
    servidor.close()
    await servidor.wait_closed()


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# --- Sondeo de puertos ------------------------------------------------------


async def test_detecta_un_puerto_abierto(puerto_escuchando: int) -> None:
    assert await _puerto_abierto("127.0.0.1", puerto_escuchando) is True


async def test_detecta_un_puerto_cerrado() -> None:
    assert await _puerto_abierto("127.0.0.1", _puerto_libre()) is False


async def test_un_host_inalcanzable_no_revienta() -> None:
    """La IP 192.0.2.1 es de documentacion: nunca responde."""
    assert await _puerto_abierto("192.0.2.1", 11000) is False


async def test_un_nombre_que_no_resuelve_no_revienta() -> None:
    assert await _puerto_abierto("no-existe.invalid", 80) is False


# --- Identificacion de un BluOS --------------------------------------------


@respx.mock
async def test_identifica_nombre_y_modelo() -> None:
    respx.get("http://10.0.0.50:11000/SyncStatus").mock(
        return_value=httpx.Response(200, text=
            '<SyncStatus icon="/images/n130.png" name="Salon" model="N130"/>'
        )
    )
    datos = await _identificar_bluos("10.0.0.50")

    assert datos == {"nombre": "Salon", "modelo": "N130"}


@respx.mock
async def test_el_icono_no_se_guarda() -> None:
    """Es una ruta interna del aparato, no aporta nada al inventario."""
    respx.get("http://10.0.0.50:11000/SyncStatus").mock(
        return_value=httpx.Response(200, text='<SyncStatus icon="/x.png"/>')
    )
    assert "icono" not in await _identificar_bluos("10.0.0.50")


@respx.mock
async def test_algo_que_no_es_un_bluos_no_confunde() -> None:
    """Otro servicio en el 11000 devuelve cualquier cosa; no debe inventarse
    un nombre a partir de ella."""
    respx.get("http://10.0.0.50:11000/SyncStatus").mock(
        return_value=httpx.Response(200, text="<html><body>hola</body></html>")
    )
    assert await _identificar_bluos("10.0.0.50") == {}


@respx.mock
async def test_un_404_en_syncstatus_no_revienta() -> None:
    respx.get("http://10.0.0.50:11000/SyncStatus").mock(
        return_value=httpx.Response(404)
    )
    assert await _identificar_bluos("10.0.0.50") == {}


@respx.mock
async def test_un_timeout_no_revienta() -> None:
    respx.get("http://10.0.0.50:11000/SyncStatus").mock(
        side_effect=httpx.ConnectTimeout("tarda")
    )
    assert await _identificar_bluos("10.0.0.50") == {}


# --- Examen de un host ------------------------------------------------------


async def test_un_host_sin_nada_abierto_no_es_hallazgo(monkeypatch) -> None:
    from casa_ai import descubrir

    async def cerrado(ip: str, puerto: int) -> bool:
        return False

    monkeypatch.setattr(descubrir, "_puerto_abierto", cerrado)

    assert await _examinar("10.0.0.7", asyncio.Semaphore(4)) is None


async def test_un_host_con_bluos_se_identifica(monkeypatch) -> None:
    from casa_ai import descubrir

    async def solo_musica(ip: str, puerto: int) -> bool:
        return puerto == 11000

    async def identifica(ip: str) -> dict[str, str]:
        return {"nombre": "Terraza"}

    monkeypatch.setattr(descubrir, "_puerto_abierto", solo_musica)
    monkeypatch.setattr(descubrir, "_identificar_bluos", identifica)

    hallazgo = await _examinar("10.0.0.8", asyncio.Semaphore(4))

    assert hallazgo is not None
    assert hallazgo.puertos == [11000]
    assert hallazgo.detalles["nombre"] == "Terraza"


async def test_un_host_sin_bluos_no_se_interroga(monkeypatch) -> None:
    """Preguntar /SyncStatus a un switch es perder tiempo por cada host."""
    from casa_ai import descubrir

    interrogados: list[str] = []

    async def solo_modbus(ip: str, puerto: int) -> bool:
        return puerto == 502

    async def identifica(ip: str) -> dict[str, str]:
        interrogados.append(ip)
        return {}

    monkeypatch.setattr(descubrir, "_puerto_abierto", solo_modbus)
    monkeypatch.setattr(descubrir, "_identificar_bluos", identifica)

    hallazgo = await _examinar("10.0.0.9", asyncio.Semaphore(4))

    assert hallazgo is not None and hallazgo.puertos == [502]
    assert interrogados == []


# --- Subred local -----------------------------------------------------------


def test_la_subred_local_es_un_24_o_nada() -> None:
    red = subred_local()
    if red is not None:
        assert red.endswith("/24")
        assert red.count(".") == 3


# --- Comando ----------------------------------------------------------------


async def test_el_comando_rechaza_una_subred_absurda(capsys) -> None:
    codigo = await _main(["10.0.0.0/8"])

    assert codigo == 1
    assert "como maximo" in capsys.readouterr().out


async def test_el_comando_escanea_lo_que_se_le_pide(monkeypatch, capsys) -> None:
    from casa_ai import descubrir

    async def cerrado(ip: str, puerto: int) -> bool:
        return False

    monkeypatch.setattr(descubrir, "_puerto_abierto", cerrado)

    codigo = await _main(["192.168.222.0/30"])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "192.168.222.0/30" in salida
    assert "misma red" in salida  # el aviso de no haber encontrado nada


async def test_sin_poder_deducir_la_subred_lo_explica(monkeypatch, capsys) -> None:
    from casa_ai import descubrir

    monkeypatch.setattr(descubrir, "subred_local", lambda: None)

    codigo = await _main([])

    assert codigo == 1
    assert "192.168.1.0/24" in capsys.readouterr().out  # el ejemplo a copiar


async def test_el_informe_incluye_el_bluos_encontrado(monkeypatch, capsys) -> None:
    from casa_ai import descubrir

    async def solo_uno(ip: str, puerto: int) -> bool:
        return ip == "192.168.222.1" and puerto == 11000

    async def identifica(ip: str) -> dict[str, str]:
        return {"nombre": "Salon", "modelo": "N130"}

    monkeypatch.setattr(descubrir, "_puerto_abierto", solo_uno)
    monkeypatch.setattr(descubrir, "_identificar_bluos", identifica)

    await _main(["192.168.222.0/30"])

    salida = capsys.readouterr().out
    assert "Salon" in salida
    assert "nombre: Salon" in salida  # el bloque de YAML listo para pegar


def test_el_informe_ordena_por_ip() -> None:
    hallazgos = [
        Hallazgo(ip="10.0.0.20", puertos=[502]),
        Hallazgo(ip="10.0.0.3", puertos=[8123]),
    ]
    texto = informe(hallazgos)
    assert texto.index("10.0.0.3") < texto.index("10.0.0.20")
