"""Descubrimiento de equipos: informe y generacion de configuracion."""

from __future__ import annotations

import pytest

from casa_ai.descubrir import Hallazgo, _bloque_env, _bloque_yaml, escanear, informe


def test_bloque_yaml_usa_el_nombre_real_del_reproductor() -> None:
    hallazgos = [
        Hallazgo(
            ip="192.168.1.50",
            puertos=[11000],
            detalles={"nombre": "Salon", "modelo": "N130"},
        ),
        Hallazgo(ip="192.168.1.51", puertos=[11000], detalles={"nombre": "Cocina"}),
    ]
    bloque = _bloque_yaml(hallazgos)

    assert "nombre: Salon" in bloque
    assert "host: 192.168.1.50" in bloque
    assert "nombre: Cocina" in bloque
    assert bloque.startswith("bluos:")


def test_bloque_yaml_inventa_nombre_si_el_equipo_no_lo_da() -> None:
    bloque = _bloque_yaml([Hallazgo(ip="192.168.1.77", puertos=[11000])])
    assert "Player-77" in bloque


def test_bloque_yaml_vacio_sin_reproductores() -> None:
    assert _bloque_yaml([Hallazgo(ip="192.168.1.1", puertos=[443])]) == ""


def test_bloque_env_deduce_hosts() -> None:
    hallazgos = [
        Hallazgo(ip="192.168.1.30", puertos=[502]),
        Hallazgo(ip="192.168.1.10", puertos=[8123]),
        Hallazgo(ip="192.168.1.1", puertos=[443]),
    ]
    env = _bloque_env(hallazgos)

    assert "SUNGROW_HOST=192.168.1.30" in env
    assert "HA_URL=http://192.168.1.10:8123" in env
    assert "UNIFI_HOST=192.168.1.1" in env
    assert "UNIFI_PUERTO=443" in env


def test_controlador_unifi_clasico_usa_8443() -> None:
    env = _bloque_env([Hallazgo(ip="192.168.1.9", puertos=[8443])])
    assert "UNIFI_PUERTO=8443" in env


def test_informe_sin_hallazgos_explica_la_causa_probable() -> None:
    texto = informe([])
    assert "misma red" in texto


def test_informe_avisa_de_los_limites() -> None:
    texto = informe([Hallazgo(ip="192.168.1.50", puertos=[11000], detalles={"nombre": "Salon"})])
    assert "camaras NO salen aqui" in texto
    assert "Salon" in texto


async def test_se_rechaza_una_subred_absurda() -> None:
    """Un /8 son 16 millones de direcciones: no se intenta."""
    with pytest.raises(ValueError, match="como maximo"):
        await escanear("10.0.0.0/8")


async def test_escaneo_de_una_subred_sin_nada(monkeypatch) -> None:
    from casa_ai import descubrir

    async def nada(ip: str, puerto: int) -> bool:
        return False

    monkeypatch.setattr(descubrir, "_puerto_abierto", nada)
    assert await escanear("192.168.99.0/30") == []


async def test_escaneo_encuentra_un_bluos(monkeypatch) -> None:
    from casa_ai import descubrir

    async def solo_11000(ip: str, puerto: int) -> bool:
        return puerto == 11000 and ip == "192.168.99.1"

    async def identifica(ip: str) -> dict[str, str]:
        return {"nombre": "Terraza", "modelo": "PULSE"}

    monkeypatch.setattr(descubrir, "_puerto_abierto", solo_11000)
    monkeypatch.setattr(descubrir, "_identificar_bluos", identifica)

    hallazgos = await escanear("192.168.99.0/30")

    assert len(hallazgos) == 1
    assert hallazgos[0].ip == "192.168.99.1"
    assert hallazgos[0].detalles["nombre"] == "Terraza"


def test_un_puerto_en_demasiados_hosts_se_ignora() -> None:
    """Un proxy transparente hace que todo parezca abierto en 443.

    Sugerir una IP concreta en ese caso seria enganar al usuario.
    """
    muchos = [Hallazgo(ip=f"10.0.0.{i}", puertos=[443]) for i in range(1, 12)]
    env = _bloque_env(muchos)
    assert "UNIFI_HOST" not in env

    texto = informe(muchos)
    assert "proxy transparente" in texto


def test_un_puerto_en_pocos_hosts_si_se_sugiere() -> None:
    pocos = [Hallazgo(ip="10.0.0.1", puertos=[443]), Hallazgo(ip="10.0.0.2", puertos=[443])]
    assert "UNIFI_HOST=10.0.0.1" in _bloque_env(pocos)


def test_los_puertos_distintivos_no_se_descartan_por_culpa_de_otro() -> None:
    """Que 443 sea ruido no debe invalidar el 502 del inversor."""
    hallazgos = [Hallazgo(ip=f"10.0.0.{i}", puertos=[443]) for i in range(1, 12)]
    hallazgos.append(Hallazgo(ip="10.0.0.30", puertos=[443, 502]))
    env = _bloque_env(hallazgos)

    assert "SUNGROW_HOST=10.0.0.30" in env
    assert "UNIFI_HOST" not in env
