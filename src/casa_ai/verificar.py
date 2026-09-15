"""Comprobacion de la instalacion, subsistema por subsistema.

    python -m casa_ai.verificar

Contacta de verdad con cada sistema configurado y dice si responde, que ha
leido y, si falla, que hacer. Al final guia la unica verificacion que no se
puede automatizar: comparar los signos del inversor con la app iSolarCloud,
porque el firmware de Sungrow no es homogeneo y nadie mas que tu puede mirar
las dos pantallas a la vez.

Es seguro: solo lee. No cambia nada en ningun equipo.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .adapters.energia_base import FuenteEnergia
from .settings import Settings, get_inventario, get_settings

OK = "✅"
FALLO = "❌"
SALTADO = "⊘ "


async def _preguntar(mensaje: str) -> str:
    """input() bloquearia el bucle de eventos; va a un hilo."""
    return await asyncio.to_thread(input, mensaje)


def _titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


async def _credenciales_claude() -> bool:
    from .settings import api_key_presente

    if api_key_presente():
        print(f"{OK} Claude: credenciales presentes")
        return True
    print(f"{FALLO} Claude: sin credenciales")
    print("   Pon ANTHROPIC_API_KEY en .env, o ejecuta `ant auth login`.")
    print("   Sin esto no hay agente: el resto da igual.")
    return False


async def _canales(s: Settings) -> None:
    if s.api_token:
        print(f"{OK} API HTTP: token definido")
    else:
        print(f"{SALTADO} API HTTP: sin API_TOKEN, los endpoints devuelven 503")
        print('   Generalo: python -c "import secrets; print(secrets.token_urlsafe(32))"')

    if s.telegram_token:
        if s.chats_telegram:
            print(f"{OK} Telegram: token y {len(s.chats_telegram)} chat(s) autorizado(s)")
        else:
            print(f"{FALLO} Telegram: token puesto pero NINGUN chat autorizado")
            print("   El bot no respondera a nadie. Arranca el backend, escribele,")
            print("   y los logs te diran tu chat_id para TELEGRAM_CHATS_AUTORIZADOS.")
    else:
        print(f"{SALTADO} Telegram: sin configurar (TELEGRAM_TOKEN)")

    if s.whatsapp_token and s.whatsapp_phone_number_id:
        if not s.whatsapp_app_secret:
            print(f"{FALLO} WhatsApp: falta WHATSAPP_APP_SECRET")
            print("   Sin el no se puede comprobar que la entrega venga de Meta, y el")
            print("   numero del remitente viaja en el cuerpo: el webhook no atendera.")
        elif s.numeros_whatsapp:
            print(f"{OK} WhatsApp: {len(s.numeros_whatsapp)} numero(s) autorizado(s)")
        else:
            print(f"{FALLO} WhatsApp: configurado pero sin numeros autorizados")
    else:
        print(f"{SALTADO} WhatsApp: sin configurar (opcional)")


async def _verificar_sungrow(inversor: FuenteEnergia, s: Settings, interactivo: bool) -> None:
    """La parte que necesita tus ojos: comparar signos con iSolarCloud."""
    _titulo("Inversor Sungrow: verificacion de signos")

    if not inversor.configurado:
        print(f"{SALTADO} Sin SUNGROW_HOST. Saltando.")
        return

    try:
        estado = await inversor.estado(usar_cache=False)
    except Exception as e:  # noqa: BLE001
        print(f"{FALLO} No se pudo leer el inversor: {e}")
        return

    r = estado.resumen()
    print("Esto es lo que leo AHORA MISMO por Modbus:\n")
    print(f"  Produccion solar      {r['solar_w']:>8} W")
    print(f"  Consumo de la casa    {r['consumo_casa_w']:>8} W   (calculado por balance)")
    print(f"  Bateria               {abs(r['bateria_w']):>8} W   {r['bateria_estado']}")
    soc = r["bateria_soc_pct"]
    print(f"  Carga de bateria      {'n/d' if soc is None else soc:>8} %")
    print(f"  Red electrica         {abs(r['red_w']):>8} W   {r['red_estado']}")
    print(f"  Modo del inversor     {r.get('modo_ems', 'n/d'):>8}")
    print(f"\n  (registros crudos: {estado.crudo})")
    # Cada fuente Modbus dice donde se invierte su signo; el diagnostico no
    # conoce las clases.
    print(f"  ({getattr(inversor, 'nota_signo_red', 'sin nota de signo')})")

    print("\nAbre la app iSolarCloud en el movil y compara estas tres cosas:\n")
    print("  1. La produccion solar coincide, mas o menos?")
    print("  2. La bateria dice lo mismo (cargando / descargando / en reposo)?")
    print("  3. La red dice lo mismo (exportando / importando)?")
    print("\nNo tienen que cuadrar al vatio: las lecturas no son del mismo segundo.")
    print("Lo que importa es que el SENTIDO sea el mismo.")

    if not interactivo:
        print("\n(Ejecutalo sin --no-interactivo para que te guie en el arreglo.)")
        return

    respuesta = (await _preguntar(
        "\nEl sentido de la RED coincide con la app? [s/n/x=no lo se] "
    )).strip().lower()

    if respuesta.startswith("s"):
        if s.sungrow_invertir_signo_red:
            print(f"\n{OK} Perfecto. Deja SUNGROW_INVERTIR_SIGNO_RED=true como esta.")
        else:
            print(f"\n{OK} Perfecto, no hay que tocar nada.")
    elif respuesta.startswith("n"):
        nuevo = "false" if s.sungrow_invertir_signo_red else "true"
        print("\nHay que invertirlo. Pon esto en tu .env:\n")
        print(f"    SUNGROW_INVERTIR_SIGNO_RED={nuevo}\n")
        if (await _preguntar("Lo escribo yo en .env? [s/n] ")).strip().lower().startswith("s"):
            _escribir_env("SUNGROW_INVERTIR_SIGNO_RED", nuevo)
        else:
            print("Vale, cambialo a mano y vuelve a ejecutar esto para confirmar.")
    else:
        print(
            "\nPara saberlo con seguridad: enciende algo que consuma mucho (un horno,\n"
            "el termo) con poco sol. Deberias ver la red 'importando'. Si dice\n"
            "'exportando', hay que invertirla."
        )


def _escribir_env(clave: str, valor: str) -> None:
    """Fija una clave en .env sin tocar el resto del fichero."""
    ruta = Path(".env")
    if not ruta.exists():
        print(f"{FALLO} No encuentro .env en este directorio. Hazlo a mano.")
        return
    lineas = ruta.read_text("utf-8").splitlines()
    # Se sustituyen TODAS las apariciones. Si la clave estuviera duplicada,
    # python-dotenv se queda con la ultima, asi que cambiar solo la primera
    # dejaria ganando el valor viejo y el usuario creeria que lo ha cambiado.
    encontrada = False
    for i, linea in enumerate(lineas):
        if linea.strip().startswith(f"{clave}="):
            lineas[i] = f"{clave}={valor}"
            encontrada = True
    if not encontrada:
        lineas.append(f"{clave}={valor}")
    ruta.write_text("\n".join(lineas) + "\n", "utf-8")
    print(f"{OK} .env actualizado: {clave}={valor}")
    print("   Reinicia el backend para que lo coja.")


async def _main(interactivo: bool) -> int:
    s = get_settings()
    inventario = get_inventario()

    _titulo("Lo imprescindible")
    hay_claude = await _credenciales_claude()
    await _canales(s)

    if not await asyncio.to_thread(Path(s.config_path).exists):
        print(f"{FALLO} No existe {s.config_path}")
        print("   Copialo: cp config/config.example.yaml config/config.yaml")
    else:
        print(
            f"{OK} Inventario: {len(inventario.bluos)} reproductor(es), "
            f"{len(inventario.camaras)} camara(s), "
            f"{len(inventario.knx)} direccion(es) KNX, "
            f"{len(inventario.alias_entidades)} alias"
        )

    avisos = s.avisos_de_seguridad()
    if avisos:
        _titulo("Avisos de seguridad")
        for aviso in avisos:
            print(f"{FALLO} {aviso}")
    else:
        print(f"{OK} Seguridad: sin configuraciones expuestas")

    _titulo("Subsistemas (lectura real)")
    # Se construye la aplicacion de verdad, para verificar los mismos
    # adaptadores que usaria en marcha (incluido el origen de la energia).
    from .app import Aplicacion
    from .comprobaciones import comprobar_subsistemas, texto

    app = Aplicacion(s)
    energia = app.ctx.energia
    print(texto(await comprobar_subsistemas(app)))

    if hasattr(energia, "nota_signo_red"):
        await _verificar_sungrow(energia, s, interactivo)
    else:
        _titulo("Inversor Sungrow: verificacion de signos")
        print(
            f"{SALTADO} La energia se lee via Home Assistant, no por Modbus.\n"
            "   Los signos los decide HA. Si algo sale al reves, corrigelo con los\n"
            "   `factor_*` de la seccion `energia_ha:` de config/config.yaml\n"
            "   (pon -1 para invertir un signo, o 1000 si el sensor da kW)."
        )

    await app.cerrar()

    _titulo("Siguiente paso")
    if not hay_claude:
        print("Consigue credenciales de Claude. Sin eso no hay agente.")
        return 1
    print("Habla con el:  python -m casa_ai.main chat \"como va todo?\"")
    print("Arranca todo:  casa-ai")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main(interactivo="--no-interactivo" not in sys.argv)))
