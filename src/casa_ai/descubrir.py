"""Descubrimiento de equipos en la red local.

Encontrar las IP de cada aparato es la parte mas tediosa de la puesta en
marcha, y la que mas se equivoca a mano. Este comando recorre tu subred y te
dice que ha encontrado y donde, y ademas escribe el bloque de configuracion
listo para pegar.

    python -m casa_ai.descubrir
    python -m casa_ai.descubrir 192.168.1.0/24     # subred concreta

Solo abre conexiones TCP y, en el caso de BluOS, una peticion HTTP de lectura.
No escribe nada en ningun equipo.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import sys
from collections import Counter
from dataclasses import dataclass, field

# Puerto -> que suele haber detras. El orden importa para el informe.
SERVICIOS: dict[int, str] = {
    11000: "BluOS (musica)",
    502: "Modbus TCP (inversor Sungrow)",
    8123: "Home Assistant",
    443: "UniFi / HTTPS",
    8443: "UniFi (controlador clasico)",
}

# Hosts probados a la vez. Suficiente para barrer un /24 en segundos sin
# saturar routers domesticos ni disparar proteccion contra escaneos.
CONCURRENCIA = 120
TIMEOUT_TCP = 0.6
TIMEOUT_HTTP = 2.0


@dataclass
class Hallazgo:
    ip: str
    puertos: list[int] = field(default_factory=list)
    # Datos extra que hayamos podido sonsacar (nombre y modelo del BluOS).
    detalles: dict[str, str] = field(default_factory=dict)


def subred_local() -> str | None:
    """Deduce la subred /24 de esta maquina sin enviar trafico."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No envia nada: solo hace que el kernel elija la interfaz de salida.
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None
    try:
        red = ipaddress.ip_network(f"{ip}/24", strict=False)
    except ValueError:
        return None
    return str(red)


async def _puerto_abierto(ip: str, puerto: int) -> bool:
    try:
        futuro = asyncio.open_connection(ip, puerto)
        lector, escritor = await asyncio.wait_for(futuro, timeout=TIMEOUT_TCP)
    except (TimeoutError, OSError):
        return False
    escritor.close()
    try:
        await escritor.wait_closed()
    except OSError:
        pass
    return True


async def _identificar_bluos(ip: str) -> dict[str, str]:
    """Pregunta a un BluOS su nombre y modelo con /SyncStatus."""
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_HTTP) as cliente:
            r = await cliente.get(f"http://{ip}:11000/SyncStatus")
            r.raise_for_status()
            texto = r.text
    except Exception:  # noqa: BLE001 - el equipo puede no ser un BluOS
        return {}

    # Parseo minimo de los atributos del XML; no merece un parser completo.
    # El \b importa: buscar la cadena "name=" a pelo casaba tambien con
    # cualquier atributo terminado en name= (model_name=, por ejemplo).
    detalles: dict[str, str] = {}
    for atributo, clave in (("name", "nombre"), ("model", "modelo")):
        encontrado = re.search(rf'\b{atributo}="([^"]*)"', texto)
        if encontrado and encontrado.group(1):
            detalles[clave] = encontrado.group(1)
    return detalles


async def _examinar(ip: str, semaforo: asyncio.Semaphore) -> Hallazgo | None:
    async with semaforo:
        # Los cinco puertos a la vez: en serie, un host silencioso ocupaba su
        # ranura del semaforo 5 x TIMEOUT_TCP = 3 s, y eso multiplicado por las
        # oleadas de un /24 contradecia el "barrer un /24 en segundos".
        resultados = await asyncio.gather(
            *(_puerto_abierto(ip, p) for p in SERVICIOS)
        )
        abiertos = [p for p, abierto in zip(SERVICIOS, resultados, strict=True) if abierto]
    if not abiertos:
        return None
    hallazgo = Hallazgo(ip=ip, puertos=abiertos)
    if 11000 in abiertos:
        hallazgo.detalles = await _identificar_bluos(ip)
    return hallazgo


async def escanear(red: str) -> list[Hallazgo]:
    objetivo = ipaddress.ip_network(red, strict=False)
    if objetivo.num_addresses > 1024:
        raise ValueError(
            f"{red} tiene {objetivo.num_addresses} direcciones. Usa una subred "
            "de como maximo /22 para no tardar una eternidad."
        )
    semaforo = asyncio.Semaphore(CONCURRENCIA)
    tareas = [_examinar(str(ip), semaforo) for ip in objetivo.hosts()]
    resultados = await asyncio.gather(*tareas)
    return [h for h in resultados if h is not None]


# --- Informe ----------------------------------------------------------------


def _bloque_yaml(hallazgos: list[Hallazgo]) -> str:
    """Genera el `bluos:` de config.yaml listo para pegar."""
    reproductores = [h for h in hallazgos if 11000 in h.puertos]
    if not reproductores:
        return ""
    lineas = ["bluos:"]
    for h in reproductores:
        nombre = h.detalles.get("nombre") or f"Player-{h.ip.split('.')[-1]}"
        lineas.append(f"  - nombre: {nombre}")
        lineas.append(f"    host: {h.ip}")
        lineas.append(f"    zona: {nombre.lower().replace(' ', '-')}   # ajusta la zona")
    return "\n".join(lineas)


# Si un puerto aparece en mas hosts que esto, no distingue nada: o hay un
# proxy transparente por medio, o un cortafuegos que acepta todo. Sugerir una
# IP concreta en ese caso seria enganar.
MAX_HOSTS_POR_PUERTO = 5


def _puertos_poco_fiables(hallazgos: list[Hallazgo]) -> set[int]:
    cuenta = Counter(p for h in hallazgos for p in h.puertos)
    return {p for p, n in cuenta.items() if n > MAX_HOSTS_POR_PUERTO}


def _bloque_env(hallazgos: list[Hallazgo]) -> str:
    """Genera las lineas de .env que se pueden deducir del escaneo."""
    dudosos = _puertos_poco_fiables(hallazgos)
    lineas: list[str] = []
    for h in hallazgos:
        if 502 in h.puertos and 502 not in dudosos:
            lineas.append(f"SUNGROW_HOST={h.ip}")
        if 8123 in h.puertos and 8123 not in dudosos:
            lineas.append(f"HA_URL=http://{h.ip}:8123")
        for puerto in (443, 8443):
            if puerto in h.puertos and puerto not in dudosos:
                lineas.append(f"UNIFI_HOST={h.ip}    # si este es tu UniFi")
                lineas.append(f"UNIFI_PUERTO={puerto}")
                break
    return "\n".join(lineas)


def informe(hallazgos: list[Hallazgo]) -> str:
    if not hallazgos:
        return (
            "No se ha encontrado nada.\n\n"
            "Lo mas probable es que estes ejecutando esto desde una maquina que\n"
            "no esta en la misma red que tus aparatos (un contenedor con red\n"
            "propia, una VPN, o una red de invitados). Ejecutalo desde un equipo\n"
            "conectado a la red de casa."
        )

    partes = [f"Encontrados {len(hallazgos)} equipos con puertos interesantes:\n"]
    for h in sorted(hallazgos, key=lambda x: ipaddress.ip_address(x.ip)):
        etiquetas = ", ".join(SERVICIOS[p] for p in h.puertos)
        linea = f"  {h.ip:<16} {etiquetas}"
        if h.detalles:
            extra = " / ".join(f"{k}: {v}" for k, v in h.detalles.items())
            linea += f"\n  {'':<16} -> {extra}"
        partes.append(linea)

    dudosos = _puertos_poco_fiables(hallazgos)
    if dudosos:
        cuales = ", ".join(str(p) for p in sorted(dudosos))
        partes.append(
            f"\n⚠️  El puerto {cuales} responde en demasiados hosts. Eso no es normal\n"
            "    en una red domestica: suele indicar un proxy transparente o un\n"
            "    cortafuegos que acepta cualquier conexion. Ignoro ese puerto para\n"
            "    no sugerirte una IP falsa."
        )

    env = _bloque_env(hallazgos)
    if env:
        partes.append("\n--- Para tu .env " + "-" * 40 + f"\n{env}")

    yaml_bluos = _bloque_yaml(hallazgos)
    if yaml_bluos:
        partes.append("\n--- Para tu config/config.yaml " + "-" * 29 + f"\n{yaml_bluos}")

    partes.append(
        "\nAvisos:\n"
        "  - El puerto 443 lo tiene mucha cosa. Confirma cual es tu UniFi.\n"
        "  - Si el inversor aparece en 502 pero luego falla al leer, es que el\n"
        "    dongle WiNet-S ya tiene su unica conexion Modbus ocupada.\n"
        "  - Las camaras NO salen aqui: se descubren desde UniFi Protect con la\n"
        "    herramienta camaras_listar una vez configurado UniFi."
    )
    return "\n".join(partes)


async def _main(argumentos: list[str]) -> int:
    red = argumentos[0] if argumentos else subred_local()
    if red is None:
        print(
            "No he podido deducir tu subred. Pasala a mano, por ejemplo:\n"
            "  python -m casa_ai.descubrir 192.168.1.0/24"
        )
        return 1
    print(f"Escaneando {red} (puertos {', '.join(str(p) for p in SERVICIOS)})...\n")
    try:
        hallazgos = await escanear(red)
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    print(informe(hallazgos))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main(sys.argv[1:])))
