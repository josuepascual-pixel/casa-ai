"""Canal de WhatsApp via Cloud API de Meta.

A diferencia de Telegram (que va por polling), WhatsApp necesita que Meta
pueda alcanzar un webhook publico por HTTPS. Opciones tipicas para una casa:
un tunel (Cloudflare Tunnel, Tailscale Funnel) o un reverse proxy con
certificado. No abras el puerto a pelo.

Seguridad. Dos capas, y la primera es la que importa:

1. **Firma HMAC.** Meta firma cada entrega con el secreto de la app en la
   cabecera X-Hub-Signature-256, y aqui se verifica contra el cuerpo CRUDO
   antes de mirar nada. Sin esto la segunda capa no vale de nada: el numero
   del remitente viaja dentro del cuerpo, asi que cualquiera que alcance el
   webhook (y es publico por diseno) podria mandar un JSON a mano diciendo que
   es el dueno de la casa. Si no hay WHATSAPP_APP_SECRET, no se atiende.
2. **Lista de numeros autorizados**, una vez sabemos que el mensaje viene de
   Meta de verdad. Vacia = no se atiende a nadie.

Las acciones de riesgo se confirman con botones interactivos, igual que en
Telegram, asi que el token no pasa por el contexto del modelo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from ..app import Aplicacion
from ..bloques import imagen_jpeg
from ..stt import TranscripcionError, transcribir
from .comun import CANCELAR, CONFIRMAR, codificar, partir

log = logging.getLogger(__name__)

CANAL = "whatsapp"
API_BASE = "https://graph.facebook.com/v21.0"

# Limites de la Cloud API. El texto de un mensaje admite 4096 caracteres, pero
# se trocea antes para dejar margen a los emojis de varios bytes.
LIMITE_MENSAJE = 4000
MAX_TITULO = 20


class CanalWhatsApp:
    def __init__(self, app: Aplicacion) -> None:
        self.app = app
        self.router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
        self.router.add_api_route("", self.verificar, methods=["GET"])
        self.router.add_api_route("", self.recibir, methods=["POST"])
        # Uno para todo el proceso. Abrir y cerrar un AsyncClient por peticion
        # costaba ~66 ms de CPU y un handshake TLS nuevo a graph.facebook.com,
        # y una respuesta troceada en dos con un boton son tres peticiones.
        self._http: httpx.AsyncClient | None = None

    @property
    def configurado(self) -> bool:
        s = self.app.settings
        return bool(
            s.whatsapp_token and s.whatsapp_phone_number_id and s.whatsapp_app_secret
        )

    def _firma_valida(self, cabecera: str, cuerpo: bytes) -> bool:
        """Comprueba X-Hub-Signature-256 contra el cuerpo crudo."""
        secreto = self.app.settings.whatsapp_app_secret
        if not secreto:
            return False
        if not cabecera.startswith("sha256="):
            return False
        esperada = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        # En bytes: con `str` no ASCII, compare_digest lanza TypeError.
        return hmac.compare_digest(cabecera[len("sha256="):].encode(), esperada.encode())

    # --- Webhook ---------------------------------------------------------
    async def verificar(self, request: Request) -> Response:
        """Handshake de suscripcion que hace Meta al configurar el webhook."""
        params = request.query_params
        esperado = self.app.settings.whatsapp_verify_token
        recibido = params.get("hub.verify_token", "")
        if (
            params.get("hub.mode") == "subscribe"
            and esperado
            and secrets.compare_digest(recibido.encode(), esperado.encode())
        ):
            return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
        log.warning("Verificacion de webhook de WhatsApp rechazada")
        return Response(status_code=403, content="verificacion fallida")

    async def recibir(
        self,
        request: Request,
        tareas: BackgroundTasks,
        x_hub_signature_256: str = Header(default=""),
    ) -> dict[str, str]:
        """Meta reintenta si no respondemos rapido: se procesa en segundo plano."""
        # El cuerpo CRUDO, antes de parsear: la firma cubre los bytes exactos.
        crudo = await request.body()

        if not self.app.settings.whatsapp_app_secret:
            log.error(
                "Entrega de WhatsApp descartada: falta WHATSAPP_APP_SECRET, asi "
                "que no se puede comprobar que venga de Meta."
            )
            raise HTTPException(503, "webhook sin secreto configurado")

        if not self._firma_valida(x_hub_signature_256, crudo):
            log.warning(
                "Entrega de WhatsApp con firma invalida descartada (%s bytes)", len(crudo)
            )
            raise HTTPException(401, "firma invalida")

        try:
            cuerpo = json.loads(crudo)
        except json.JSONDecodeError:
            raise HTTPException(400, "cuerpo no es JSON valido") from None

        for mensaje, numero in _extraer(cuerpo):
            if numero not in self.app.settings.numeros_whatsapp:
                log.warning("Mensaje de WhatsApp rechazado del numero %s", numero)
                continue
            tareas.add_task(self._procesar, mensaje, numero)
        return {"status": "ok"}

    # --- Procesado -------------------------------------------------------
    async def _procesar(self, mensaje: dict[str, Any], numero: str) -> None:
        conversacion = f"{CANAL}:{numero}"
        try:
            # Una pulsacion de boton no es un turno del agente: ejecuta o
            # cancela directamente, sin que el modelo intervenga.
            if mensaje.get("type") == "interactive":
                await self._pulsacion(mensaje, numero, conversacion)
                return

            entrada = await self._a_entrada(mensaje, numero)
            if entrada is None:
                return
            respuesta = await self.app.responder(
                canal=CANAL,
                usuario=numero,
                conversacion=conversacion,
                entrada=entrada,
                # Este canal tiene botones, asi que el token de confirmacion no
                # necesita pasar por el contexto del modelo.
                confirmacion="boton",
            )
            await self._enviar(numero, respuesta)
            await self._ofrecer_botones(numero, conversacion)
        except Exception:  # noqa: BLE001 - tarea de fondo, nunca debe morir en silencio
            log.exception("Fallo procesando mensaje de WhatsApp de %s", numero)
            await self._enviar(numero, "Algo ha fallado por dentro. Revisa los logs.")

    async def _pulsacion(
        self, mensaje: dict[str, Any], numero: str, conversacion: str
    ) -> None:
        """El usuario ha pulsado Confirmar o Cancelar.

        Este camino NO pasa por el modelo: la pulsacion es la prueba de que un
        humano ha dicho si, que es justo lo que la comprobacion de turno
        intenta establecer cuando la confirmacion va por conversacion.
        """
        interactivo = mensaje.get("interactive") or {}
        respuesta_boton = interactivo.get("button_reply") or {}
        identificador = str(respuesta_boton.get("id", ""))

        texto = await self.app.resolver_pulsacion(
            identificador, canal=CANAL, usuario=numero, conversacion=conversacion
        )
        if texto is None:
            log.warning("Pulsacion de WhatsApp no reconocida: %s", identificador[:40])
            return
        await self._enviar(numero, texto)

    async def _ofrecer_botones(self, numero: str, conversacion: str) -> None:
        """Manda un boton por cada accion de riesgo que quedo propuesta."""
        for pendiente in self.app.pendientes_para_ofrecer(conversacion):
            await self._enviar_botones(numero, pendiente["resumen"], pendiente["token"])

    async def _enviar_botones(self, numero: str, resumen: str, token: str) -> None:
        await self._peticion({
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "interactive",
            "interactive": {
                "type": "button",
                # El cuerpo de un interactivo admite 1024 caracteres.
                "body": {"text": f"⚠️ {resumen}"[:1024]},
                "action": {"buttons": [
                    {"type": "reply",
                     "reply": {"id": codificar(CONFIRMAR, token),
                               "title": "✅ Confirmar"[:MAX_TITULO]}},
                    {"type": "reply",
                     "reply": {"id": codificar(CANCELAR, token),
                               "title": "✖️ Cancelar"[:MAX_TITULO]}},
                ]},
            },
        })

    async def _a_entrada(self, mensaje: dict[str, Any], numero: str) -> Any:
        tipo = mensaje.get("type")
        if tipo == "text":
            return (mensaje.get("text") or {}).get("body", "")
        if tipo in ("audio", "voice"):
            media_id = (mensaje.get(tipo) or {}).get("id")
            if not media_id:
                return None
            audio = await self._descargar_media(media_id)
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "nota.ogg"
                destino.write_bytes(audio)
                try:
                    texto = await transcribir(destino, self.app.settings)
                except TranscripcionError as e:
                    await self._enviar(numero, str(e))
                    return None
            await self._enviar(numero, f"🎙 «{texto}»")
            return texto
        if tipo == "image":
            media_id = (mensaje.get("image") or {}).get("id")
            if not media_id:
                return None
            datos = await self._descargar_media(media_id)
            return imagen_jpeg(
                datos,
                (mensaje.get("image") or {}).get("caption") or "Que ves en esta foto?",
            )
        await self._enviar(numero, f"No se manejar mensajes de tipo '{tipo}'.")
        return None

    async def _descargar_media(self, media_id: str) -> bytes:
        """La Cloud API da primero una URL firmada y luego el binario."""
        cliente = self._cliente()
        meta = await cliente.get(f"/{media_id}")
        meta.raise_for_status()
        url = meta.json().get("url")
        if not url:
            raise RuntimeError(f"WhatsApp no devolvio URL para el media {media_id}")
        # URL absoluta a otro host (lookaside.fbsbx.com): la cabecera de
        # autorizacion la sigue necesitando, y el cliente ya la lleva puesta.
        binario = await cliente.get(url)
        binario.raise_for_status()
        return binario.content

    async def _enviar(self, numero: str, texto: str) -> None:
        for trozo in partir(texto, LIMITE_MENSAJE):
            await self._peticion({
                "messaging_product": "whatsapp",
                "to": numero,
                "type": "text",
                "text": {"body": trozo},
            })

    def _cliente(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=API_BASE,
                headers={"Authorization": f"Bearer {self.app.settings.whatsapp_token}"},
                timeout=30.0,
            )
        return self._http

    async def cerrar(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _peticion(self, cuerpo: dict[str, Any]) -> None:
        if not self.configurado:
            log.error(
                "WhatsApp sin configurar: no se puede enviar a %s", cuerpo.get("to")
            )
            return
        ruta = f"/{self.app.settings.whatsapp_phone_number_id}/messages"
        r = await self._cliente().post(ruta, json=cuerpo)
        if r.is_error:
            log.error("WhatsApp rechazo el envio: %s %s", r.status_code, r.text[:300])


def _extraer(cuerpo: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Saca los mensajes del sobre anidado que manda Meta."""
    salida: list[tuple[dict[str, Any], str]] = []
    for entrada in cuerpo.get("entry", []) or []:
        for cambio in entrada.get("changes", []) or []:
            valor = cambio.get("value", {}) or {}
            for mensaje in valor.get("messages", []) or []:
                numero = mensaje.get("from")
                if numero:
                    salida.append((mensaje, str(numero)))
    return salida
