"""Backend FastAPI y arranque del sistema.

Levanta, en un solo proceso:
- la API HTTP (para el webhook de WhatsApp, healthcheck y un endpoint de chat)
- el bot de Telegram por polling
- las rutinas proactivas programadas

Tambien sirve de CLI para hablar con el agente desde la terminal, util para
probar sin moviles de por medio:

    python -m casa_ai.main chat "cuanto esta produciendo el sol?"
    python -m casa_ai.main diagnostico
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .adapters.base import AdapterError
from .app import Aplicacion
from .automations.rutinas import Rutinas
from .channels.whatsapp import CanalWhatsApp
from .panel.datos import recopilar
from .settings import Settings, api_key_presente, get_settings

log = logging.getLogger(__name__)


def configurar_logging(nivel: str) -> None:
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # El polling de Telegram y httpx son muy charlatanes en INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


class PeticionChat(BaseModel):
    mensaje: str
    # Etiqueta libre para separar hilos de conversacion. Se le pone prefijo
    # `http:` en el servidor: si el llamante pudiera dar la conversacion entera,
    # leeria el historial de los chats de Telegram.
    hilo: str = Field(default="default", max_length=64)


class PeticionVoz(BaseModel):
    """Lo que manda la integracion de Home Assistant por cada frase dicha.

    `dispositivo` es la identidad que pone la integracion de Home Assistant:
    el usuario de HA que hablo (`usuario-<id>`) o, solo si no hay usuario, el
    satelite que oyo la frase. Con el se resuelve la persona (`personas:` del
    inventario), asi que un satelite del cuarto del nino solo obtiene lo que
    un nino puede, y lo que no este declarado tambien es nino.
    """

    dispositivo: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:@-]+$")
    texto: str = Field(min_length=1, max_length=4000)


class PeticionDescubrir(BaseModel):
    red: str | None = Field(default=None, max_length=32, pattern=r"^[0-9./]+$")


class PeticionSondeo(BaseModel):
    orden: str = Field(default="sondear", max_length=64, pattern=r"^[a-z0-9 ]+$")


class RespuestaChat(BaseModel):
    respuesta: str


# Identidad del canal HTTP. Es fija a proposito: si viniera en el cuerpo de la
# peticion, el llamante podria hacerse pasar por un chat de Telegram y
# consumir sus acciones pendientes de confirmacion.
USUARIO_HTTP = "api"


def _autenticador(settings: Settings):
    """Exige el token de API en todos los endpoints.

    Falla cerrado: si no hay token configurado, el API no se sirve. El canal
    HTTP da el mismo control de la casa que Telegram (encender, apagar, ver
    camaras, y con confirmacion tocar la bateria o el wifi), asi que dejarlo
    abierto porque "esta en la LAN" no es defendible: basta un dispositivo
    invitado o un tunel puesto delante para el webhook de WhatsApp.
    """

    async def verificar(authorization: str = Header(default="")) -> None:
        if not settings.api_token:
            raise HTTPException(
                503,
                "API sin token. Define API_TOKEN en .env para poder usar los "
                "endpoints HTTP. Generalo con: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"',
            )
        esperado = f"Bearer {settings.api_token}"
        # compare_digest sobre bytes: con `str` lanza TypeError en cuanto llega
        # un caracter no ASCII, y eso convertia un token invalido en un 500.
        if not secrets.compare_digest(authorization.encode(), esperado.encode()):
            raise HTTPException(401, "Token invalido o ausente.")

    return verificar


def crear_app() -> FastAPI:
    settings = get_settings()
    configurar_logging(settings.log_level)

    for aviso in settings.avisos_de_seguridad():
        log.warning("SEGURIDAD: %s", aviso)

    if not api_key_presente():
        log.warning(
            "Sin credenciales de Anthropic. Exporta ANTHROPIC_API_KEY o inicia "
            "sesion con `ant auth login`; el agente no podra responder."
        )

    aplicacion = Aplicacion(settings)
    whatsapp = CanalWhatsApp(aplicacion)
    bot: Any = None
    rutinas: Rutinas | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal bot, rutinas
        log.info("Subsistemas: %s", aplicacion.resumen_configuracion())

        if settings.telegram_token:
            from .channels.telegram import BotTelegram

            bot = BotTelegram(aplicacion)
            try:
                await bot.iniciar()
            except Exception:  # noqa: BLE001 - sin Telegram el resto debe seguir
                log.exception("No se pudo iniciar el bot de Telegram")
                bot = None
        else:
            log.info("TELEGRAM_TOKEN no configurado: canal de Telegram desactivado.")

        rutinas = Rutinas(aplicacion, bot)
        rutinas.iniciar()

        try:
            yield
        finally:
            if rutinas is not None:
                rutinas.detener()
            if bot is not None:
                await bot.detener()
            await whatsapp.cerrar()
            await aplicacion.cerrar()

    autenticar = _autenticador(settings)

    api = FastAPI(
        title="Casa AI",
        description=(
            "Agentes conversacionales con control real de energia solar Sungrow, "
            "domotica ONNA/KNX, audio BluOS, camaras y red UniFi."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    if whatsapp.configurado:
        api.include_router(whatsapp.router)
    else:
        log.info("WhatsApp no configurado: webhook no registrado.")

    # --- Panel web -------------------------------------------------------
    # La pagina y su script se sirven sin token porque no contienen ningun
    # dato: piden el token al usuario y con el llaman a /api/panel. Los datos
    # si van autenticados.
    _PANEL = Path(__file__).parent / "panel"

    @api.get("/panel", include_in_schema=False)
    async def panel() -> FileResponse:
        return FileResponse(_PANEL / "index.html", media_type="text/html")

    @api.get("/panel.js", include_in_schema=False)
    async def panel_js() -> FileResponse:
        return FileResponse(_PANEL / "panel.js", media_type="application/javascript")

    @api.get("/api/panel", dependencies=[Depends(autenticar)])
    async def datos_panel() -> dict[str, Any]:
        """Todo lo que el panel muestra, en una sola pasada. Solo lectura."""
        ctx = aplicacion.contexto_para("http", USUARIO_HTTP, "http:panel")
        return await recopilar(ctx)

    @api.get("/api/panel/camara/{nombre}", dependencies=[Depends(autenticar)])
    async def camara_panel(nombre: str) -> Response:
        """Captura JPEG. Va por fetch con cabecera, no por <img src>."""
        ctx = aplicacion.contexto_para("http", USUARIO_HTTP, "http:panel")
        try:
            imagen, _identificador = await ctx.camaras.captura(nombre)
        except AdapterError as e:
            raise HTTPException(502, str(e)) from e
        return Response(
            content=imagen,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @api.get("/salud", dependencies=[Depends(autenticar)])
    async def salud() -> dict[str, Any]:
        return {"estado": "ok", "subsistemas": aplicacion.resumen_configuracion()}

    @api.post("/chat", response_model=RespuestaChat,
              dependencies=[Depends(autenticar)])
    async def chat(peticion: PeticionChat) -> RespuestaChat:
        """Chat por HTTP, autenticado con API_TOKEN.

        La identidad es siempre `api`, no la que diga el cuerpo de la peticion,
        y el hilo va con prefijo `http:`. Asi este canal no puede hacerse pasar
        por un chat de Telegram ni leer su historial.
        """
        if not api_key_presente():
            raise HTTPException(503, "Sin credenciales de Anthropic configuradas.")
        respuesta = await aplicacion.responder(
            canal="http",
            usuario=USUARIO_HTTP,
            conversacion=f"http:{peticion.hilo}",
            entrada=peticion.mensaje,
        )
        return RespuestaChat(respuesta=respuesta)

    @api.post("/voz", response_model=RespuestaChat, dependencies=[Depends(autenticar)])
    async def voz(peticion: PeticionVoz) -> RespuestaChat:
        """Una frase dicha a un satelite de voz, via Home Assistant.

        Aqui el cuerpo SI trae una identidad, la del aparato, y es aceptable
        por dos razones: quien llama ya tiene el API_TOKEN, con el que `/chat`
        le da acceso de dueno, asi que nombrar un aparato nunca escala nada; y
        un aparato que no este declarado en `personas:` cuenta como nino, haya
        seccion `personas:` o no. La
        conversacion va con prefijo `voz:` para que no pueda leer el historial
        de ningun chat.
        """
        if not api_key_presente():
            raise HTTPException(503, "Sin credenciales de Anthropic configuradas.")
        respuesta = await aplicacion.responder(
            canal="voz",
            usuario=peticion.dispositivo,
            conversacion=f"voz:{peticion.dispositivo}",
            entrada=peticion.texto,
        )
        return RespuestaChat(respuesta=respuesta)

    @api.get("/verificar", dependencies=[Depends(autenticar)])
    async def verificar() -> dict[str, Any]:
        """Lectura real de cada subsistema, para la puesta en marcha sin terminal."""
        from .comprobaciones import comprobar_subsistemas

        return {
            "comprobaciones": [
                {"nombre": c.nombre, "estado": c.estado, "detalle": c.detalle, "pista": c.pista}
                for c in await comprobar_subsistemas(aplicacion)
            ]
        }

    @api.post("/descubrir", dependencies=[Depends(autenticar)])
    async def descubrir(peticion: PeticionDescubrir | None = None) -> dict[str, str]:
        """Barre la red de casa. Solo abre conexiones TCP; no escribe en nada."""
        return {"informe": await aplicacion.descubrir(peticion.red if peticion else None)}

    @api.post("/planta/sondear", dependencies=[Depends(autenticar)])
    async def sondear_planta(peticion: PeticionSondeo | None = None) -> dict[str, str]:
        """Sondeo de la planta Sungrow por el Logger1000, sin terminal."""
        orden = peticion.orden if peticion else "sondear"
        return {"informe": await aplicacion.sondear_planta(orden)}

    @api.post("/recargar-inventario", dependencies=[Depends(autenticar)])
    async def recargar() -> dict[str, Any]:
        """Relee config/config.yaml y rehace lo que depende de el."""
        inventario = await aplicacion.recargar_inventario()
        return {
            "zonas": len(inventario.zonas),
            "aparatos": len(inventario.dispositivos),
            "reproductores": len(inventario.bluos),
            "camaras": len(inventario.camaras),
            "direcciones_knx": len(inventario.knx),
            "herramientas_activas": len(aplicacion.registro.disponibles(aplicacion.ctx)),
        }

    @api.get("/auditoria", dependencies=[Depends(autenticar)])
    async def auditoria(limite: int = 20) -> dict[str, Any]:
        return {"acciones": aplicacion.store.auditoria(limite)}

    @api.get("/avisos-seguridad", dependencies=[Depends(autenticar)])
    async def avisos() -> dict[str, Any]:
        """Configuraciones que funcionan pero dejan la instalacion expuesta."""
        return {"avisos": settings.avisos_de_seguridad()}

    return api


# --- CLI --------------------------------------------------------------------


async def _cli_chat(mensaje: str) -> None:
    settings = get_settings()
    configurar_logging(settings.log_level)
    aplicacion = Aplicacion(settings)
    try:
        respuesta = await aplicacion.responder(
            canal="cli", usuario="local", conversacion="cli:local", entrada=mensaje
        )
        print(f"\n{respuesta}\n")
    finally:
        await aplicacion.cerrar()


async def _cli_diagnostico() -> None:
    import json

    settings = get_settings()
    configurar_logging(settings.log_level)
    aplicacion = Aplicacion(settings)
    print(json.dumps(aplicacion.resumen_configuracion(), indent=2, ensure_ascii=False))
    await aplicacion.cerrar()


def run() -> None:
    """Punto de entrada del paquete (`casa-ai`)."""
    import uvicorn

    settings = get_settings()
    if settings.api_expuesta_sin_token:
        log.error(
            "Negado: API_HOST=%s expondria el control de la casa sin API_TOKEN. "
            "Define el token, o deja API_HOST=127.0.0.1.",
            settings.api_host,
        )
        raise SystemExit(1)

    uvicorn.run(
        "casa_ai.main:crear_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_puerto,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    argumentos = sys.argv[1:]
    if argumentos and argumentos[0] == "chat":
        asyncio.run(_cli_chat(" ".join(argumentos[1:]) or "como va todo?"))
    elif argumentos and argumentos[0] == "diagnostico":
        asyncio.run(_cli_diagnostico())
    else:
        run()
