"""Montaje de la aplicacion: adaptadores, registro de herramientas y agente."""

from __future__ import annotations

import logging
from typing import Any

from .adapters.bluos import BluOS
from .adapters.camaras_base import elegir_camaras
from .adapters.energia_base import FuenteEnergia
from .adapters.energia_ha import EnergiaHA
from .adapters.homeassistant import DOMINIOS_PARA_NINOS, HomeAssistant
from .adapters.knx_onna import KNXOnna
from .adapters.planta import OUI_SUNGROW, PlantaSungrow
from .adapters.sungrow import Sungrow
from .adapters.unifi import UniFi
from .afirmaciones import es_afirmacion, es_negacion
from .agent.orchestrator import Agente, crear_cliente
from .agent.registry import Confirmacion, Contexto, Registro
from .channels.comun import CANCELAR, decodificar
from .settings import Inventario, Settings, get_settings
from .store import Store
from .tools import construir_registro

log = logging.getLogger(__name__)


def _texto_resultado(resultado: object) -> str:
    """Convierte el resultado de una herramienta en algo legible por una persona.

    Sin esto, las herramientas que no devuelven `detalle` acababan mostrando el
    repr de un diccionario de Python en el chat.
    """
    if isinstance(resultado, str):
        return resultado
    if isinstance(resultado, dict):
        if resultado.get("detalle"):
            return str(resultado["detalle"])
        return ", ".join(f"{k}: {v}" for k, v in resultado.items())
    return str(resultado)


class Aplicacion:
    """Contenedor de dependencias con ciclo de vida explicito."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # Del `settings` recibido, no del global: si no, una Aplicacion
        # construida con otra configuracion leeria el YAML equivocado.
        self.inventario = self.settings.cargar_inventario()
        self.store = Store(self.settings.db_path)
        self.registro: Registro = construir_registro()
        self.ctx = self._construir_contexto()
        # Un cliente para todo el proceso. Antes se construia uno en el
        # __init__ (que nunca respondia: `responder` monta un Agente nuevo por
        # peticion) y otro mas en cada mensaje.
        self.cliente = crear_cliente(self.settings)

    def _construir_contexto(self) -> Contexto:
        """Monta los adaptadores contra el inventario actual.

        Esta separado del __init__ porque varios adaptadores se quedan con una
        referencia al inventario (BluOS con sus reproductores, KNX con sus
        direcciones permitidas, Home Assistant con sus scripts permitidos), asi
        que recargar el YAML obliga a rehacerlos: no basta con releer el
        fichero.
        """
        ha = HomeAssistant(self.settings, self.inventario.scripts_permitidos)
        unifi = UniFi(self.settings)
        return Contexto(
            settings=self.settings,
            inventario=self.inventario,
            store=self.store,
            ha=ha,
            energia=self._elegir_energia(ha, unifi),
            knx=KNXOnna(self.settings, self.inventario),
            musica=BluOS(self.inventario),
            unifi=unifi,
            camaras=elegir_camaras(unifi, ha, self.inventario),
        )

    async def recargar_inventario(self) -> Inventario:
        """Relee config/config.yaml y rehace todo lo que depende de el.

        Cierra los adaptadores y los vuelve a montar, porque se quedan con una
        referencia al inventario. El prompt de sistema, que tambien lleva el
        inventario dentro, se rehace solo: lo construye el Agente de cada turno
        a partir de `ctx.inventario`. El coste es perder las conexiones
        abiertas (la sesion de UniFi, el tunel KNX), que se restablecen solas
        en la siguiente llamada.
        """
        await self.ctx.cerrar()

        self.inventario = self.settings.cargar_inventario()
        self.ctx = self._construir_contexto()
        log.info(
            "Inventario recargado: %s aparatos, %s reproductores, %s camaras",
            len(self.inventario.dispositivos),
            len(self.inventario.bluos),
            len(self.inventario.camaras),
        )
        return self.inventario

    def _fuente_modbus(self, unifi: UniFi) -> FuenteEnergia:
        """La planta (Logger1000) si el YAML la declara; si no, el inversor hibrido."""
        if self.inventario.planta is None:
            return Sungrow(
                self.settings,
                invertir_signo_red=self.settings.sungrow_invertir_signo_red,
            )

        async def ips_sungrow(mac: str | None) -> list[str]:
            # El registrador no tiene IP fija porque el router de Vodafone no
            # deja reservarla; UniFi si sabe donde esta ahora mismo. Sin MAC,
            # todos los equipos Sungrow de la red, y el adaptador prueba cual
            # es el registrador.
            salida: list[str] = []
            for cliente in await unifi.clientes(texto=mac, limite=500):
                suya = str(cliente.get("mac", "")).lower()
                es = suya == mac.lower() if mac else suya.startswith(OUI_SUNGROW)
                if es and cliente.get("ip"):
                    salida.append(str(cliente["ip"]))
            return salida

        return PlantaSungrow(
            self.settings,
            self.inventario.planta,
            buscar_ip=ips_sungrow if unifi.configurado else None,
        )

    def _elegir_energia(self, ha: HomeAssistant, unifi: UniFi) -> FuenteEnergia:
        """Decide si la energia se lee por Modbus directo o via Home Assistant.

        En `auto` manda el Modbus cuando hay un inversor configurado, porque da
        mas datos y permite controlar la bateria. Si no hay inversor alcanzable
        pero si Home Assistant, se cae a los sensores de HA, que es lo que
        permite ejecutar todo esto fuera de la red de casa.
        """
        modbus = self._fuente_modbus(unifi)
        via_ha = EnergiaHA(self.settings, ha, self.inventario.energia_ha)
        origen = self.settings.energia_origen

        if origen == "modbus":
            return modbus
        if origen == "homeassistant":
            return via_ha
        if modbus.configurado:
            return modbus
        if via_ha.configurado:
            log.info("Energia leida via Home Assistant (no hay SUNGROW_HOST).")
            return via_ha
        return modbus  # sin configurar: sus errores explican que falta

    def contexto_para(
        self,
        canal: str,
        usuario: str,
        conversacion: str,
        *,
        confirmacion: Confirmacion = "en_banda",
    ) -> Contexto:
        """Contexto etiquetado con el origen, para auditoria y confirmaciones.

        Comparte los adaptadores (y por tanto conexiones y sesiones) pero cambia
        canal, usuario y conversacion, que es lo que ata una accion pendiente a
        quien la pidio y donde: un token de Telegram no se puede confirmar
        desde WhatsApp, ni desde otro chat, ni en el mismo turno en que se
        propuso. Las tres comprobaciones estan en Store.tomar_pendiente.
        """
        import dataclasses

        persona = self.inventario.persona_de(canal, usuario)
        # El veto esta en el adaptador para que ninguna herramienta pueda
        # saltarselo: un nino ve solo sus dominios, y nadie ve las entidades
        # privadas de otra persona (ni el informe, ni el API, ni un altavoz).
        ha = self.ctx.ha
        ocultas = self.inventario.privadas_ajenas(persona)
        if persona is not None and persona.es_nino:
            ha = ha.restringido_a(DOMINIOS_PARA_NINOS, ocultas=ocultas)
        elif ocultas:
            ha = ha.restringido_a(ocultas=ocultas)
        return dataclasses.replace(
            self.ctx,
            canal=canal,
            usuario=usuario,
            conversacion=conversacion,
            persona=persona,
            ha=ha,
            confirmacion=confirmacion,
        )

    def resumen_configuracion(self) -> dict[str, object]:
        """Que subsistemas estan realmente operativos."""
        return {
            "home_assistant": self.ctx.ha.configurado,
            "energia": self.ctx.energia.configurado,
            "energia_origen": type(self.ctx.energia).__name__,
            "energia_puede_controlar": self.ctx.energia.puede_controlar,
            "knx_onna_directo": self.ctx.knx.configurado,
            "musica_bluos": self.ctx.musica.configurado,
            "unifi": self.ctx.unifi.configurado,
            "camaras": self.ctx.camaras.configurado,
            "camaras_via": self.ctx.camaras.via,
            "herramientas_activas": [h.nombre for h in self.registro.disponibles(self.ctx)],
            # Con el motivo: la pregunta de quien monta la casa es por que no
            # puede pedir algo, y antes solo se podia listar lo que si esta.
            "herramientas_ausentes": self.registro.ausentes(self.ctx),
            "confirmacion_riesgo": self.settings.exigir_confirmacion,
        }

    async def responder(
        self,
        *,
        canal: str,
        usuario: str,
        conversacion: str,
        entrada: object,
        confirmacion: Confirmacion = "en_banda",
    ) -> str:
        """Punto de entrada unico para todos los canales.

        `confirmacion` dice como puede autorizar una accion de riesgo el humano
        que hay al otro lado, y "imposible" es una respuesta valida: la usan las
        rutinas, donde no hay nadie.
        """
        if confirmacion == "en_banda" and isinstance(entrada, str):
            resuelto = await self._resolver_en_banda(canal, usuario, conversacion, entrada)
            if resuelto is not None:
                return resuelto
        ctx = self.contexto_para(
            canal, usuario, conversacion, confirmacion=confirmacion
        )
        agente = Agente(self.settings, self.registro, ctx, self.cliente)
        return await agente.responder(conversacion, entrada)  # type: ignore[arg-type]

    async def _resolver_en_banda(
        self, canal: str, usuario: str, conversacion: str, texto: str
    ) -> str | None:
        """Si hay una accion propuesta en el turno anterior, este mensaje decide.

        Un «si» claro la ejecuta; un «no» claro la cancela; cualquier otra
        cosa la cancela tambien y sigue con el modelo. Devuelve la respuesta
        al usuario cuando la decision se toma aqui, y None cuando el mensaje
        tiene que ir al modelo. El modelo no interviene en la decision: es lo
        que impide que una inyeccion en el turno siguiente confirme nada.
        """
        pendientes = self.store.pendientes_de(
            conversacion, turno=self.store.turno_actual(conversacion)
        )
        if not pendientes:
            return None
        if es_afirmacion(texto):
            if len(pendientes) > 1:
                self.store.cancelar_pendientes(conversacion)
                self._anotar_en_historial(
                    conversacion, "[Habia varias acciones pendientes: cancelada todas.]"
                )
                return (
                    "Habia varias acciones pendientes y no se cual confirmas. Las he "
                    "cancelado: pidemela otra vez, de una en una."
                )
            detalle, _es_error = await self.confirmar_pendiente(
                canal=canal, usuario=usuario, conversacion=conversacion,
                token=pendientes[0]["token"],
            )
            return detalle
        self.store.cancelar_pendientes(conversacion)
        resumenes = "; ".join(p["resumen"] for p in pendientes)
        if es_negacion(texto):
            self._anotar_en_historial(conversacion, f"[El usuario cancelo: {resumenes}.]")
            return "Cancelado."
        # No fue ni si ni no: la propuesta cae, y el modelo se entera para que
        # no la de por viva ni la ejecute por su cuenta.
        self._anotar_en_historial(
            conversacion,
            f"[La propuesta pendiente ({resumenes}) quedo cancelada: el usuario no la "
            "confirmo. Si la sigue queriendo, proponla de nuevo.]",
        )
        return None

    async def confirmar_pendiente(
        self, *, canal: str, usuario: str, conversacion: str, token: str
    ) -> tuple[str, bool]:
        """Ejecuta una accion pendiente porque un humano la confirmo.

        La llama el canal cuando el usuario pulsa el boton, o `_resolver_en_banda`
        cuando escribe que si. Nunca el modelo: el token no entra en su contexto.
        """
        from .agent.safety import Ejecutor

        propuesta = self.store.detalle_pendiente(token)
        ctx = self.contexto_para(canal, usuario, conversacion)
        resultado, es_error = await Ejecutor(self.registro, ctx).confirmar(token)
        if es_error:
            return str(resultado), True

        detalle = _texto_resultado(resultado)
        # El historial tiene que enterarse. Si no, se queda con el "ACCION NO
        # EJECUTADA" del turno anterior y el modelo luego niega que se hiciera,
        # o vuelve a proponer lo mismo.
        self._anotar_en_historial(
            conversacion,
            f"[El usuario confirmo con el boton: {propuesta['resumen']}. "
            f"Ejecutada. Resultado: {detalle}]"
            if propuesta
            else f"[El usuario confirmo una accion pendiente. Resultado: {detalle}]",
        )
        return detalle, False

    def cancelar_pendiente(
        self, *, canal: str, usuario: str, conversacion: str, token: str
    ) -> bool:
        """Descarta una accion pendiente porque el usuario dijo que no."""
        # Se lee antes de cancelar: despues ya no se sabria que se declino, y
        # una auditoria que no dice que accion era no sirve de nada.
        propuesta = self.store.detalle_pendiente(token)
        cancelada = self.store.cancelar_pendiente(
            token, canal=canal, usuario=usuario, conversacion=conversacion
        )
        if not cancelada:
            return False

        self.store.registrar(
            canal=canal,
            usuario=usuario,
            herramienta=propuesta["herramienta"] if propuesta else "(desconocida)",
            argumentos=propuesta["argumentos"] if propuesta else {},
            riesgo="alto (cancelada)",
            resultado="cancelada por el usuario con el boton",
        )
        self._anotar_en_historial(
            conversacion,
            f"[El usuario cancelo con el boton: {propuesta['resumen']}. "
            "No se ha ejecutado.]"
            if propuesta
            else "[El usuario cancelo una accion pendiente. No se ha ejecutado.]",
        )
        return True

    # --- Botones ---------------------------------------------------------
    async def comprobar(self) -> str:
        """Lectura real de cada subsistema, en texto para el movil.

        Cierra con la seguridad de la configuracion: quien pregunta «¿que
        ve Jarvis?» desde el movil es quien tiene que saber si algo queda
        abierto, y ese aviso solo salia en el registro del complemento.
        """
        from .comprobaciones import comprobar_subsistemas, texto

        return texto(await comprobar_subsistemas(self)) + "\n" + self.texto_seguridad()

    def texto_seguridad(self) -> str:
        avisos = self.settings.avisos_de_seguridad()
        if not avisos:
            return "🔒 Seguridad: sin configuraciones expuestas"
        return "\n".join(f"🔓 Seguridad: {aviso}" for aviso in avisos)

    async def descubrir(self, red: str | None = None) -> str:
        """Barre la red de casa y devuelve el informe con los bloques para pegar.

        Existe como metodo porque el complemento no tiene terminal: se pide
        por Telegram o por el API y el resultado llega al movil.
        """
        from .descubrir import escanear, informe, subred_local

        objetivo = red or subred_local()
        if objetivo is None:
            return "No he podido deducir la subred de la casa. Pasala: /descubrir 192.168.0.0/24"
        try:
            return informe(await escanear(objetivo))
        except ValueError as e:
            return str(e)

    async def sondear_planta(self, texto: str = "sondear") -> str:
        """El sondeo de la planta Sungrow, para hacerlo desde el movil."""
        from .adapters.planta import orden_de_sondeo

        energia = self.ctx.energia
        if not isinstance(energia, PlantaSungrow):
            return (
                "La energia no es una planta por Logger1000: declara `planta:` en el "
                "inventario y recarga."
            )
        if not energia.configurado:
            return "La planta no esta configurada: falta SUNGROW_HOST o UniFi para buscarla."
        return await orden_de_sondeo(energia, texto)

    def chats_de_duenos(self) -> set[int]:
        """Los chats de Telegram a los que avisar de algo de seguridad.

        Con personas declaradas, los de nivel dueno; sin ellas, todos los
        autorizados, que es lo que hay.
        """
        chats = self.settings.chats_telegram
        if not self.inventario.personas:
            return chats
        return {
            c for c in chats
            if (p := self.inventario.persona_de("telegram", str(c))) and p.nivel == "dueno"
        }

    def es_dueno(self, canal: str, usuario: str) -> bool:
        persona = self.inventario.persona_de(canal, usuario)
        return persona is None or persona.nivel == "dueno"

    @property
    def nombre_asistente(self) -> str:
        """Como se llama el agente, para que los canales saluden con su nombre."""
        return self.ctx.inventario.asistente.nombre

    def pendientes_para_ofrecer(self, conversacion: str) -> list[dict[str, Any]]:
        """Las acciones que merecen un boton vivo: solo las de ESTE turno.

        Es politica de seguridad, no comodidad, y por eso vive aqui y no en
        cada canal: sin el filtro por turno, una propuesta que el usuario
        ignoro reaparece con boton vivo tras cada mensaje suyo durante los 15
        minutos de vida del token, y un toque por error ejecuta algo que en la
        practica ya habia declinado. Un canal nuevo que preguntase por su
        cuenta reabria el agujero en silencio.
        """
        return self.store.pendientes_de(
            conversacion, turno=self.store.turno_actual(conversacion)
        )

    async def resolver_pulsacion(
        self, dato: str, *, canal: str, usuario: str, conversacion: str
    ) -> str | None:
        """Resuelve la pulsacion de un boton y devuelve el texto de respuesta.

        None significa que el identificador no es de los nuestros. El canal se
        queda solo con su transporte: aqui estan la decision y los efectos
        (auditoria e historial), que ya eran de esta clase.
        """
        pulsacion = decodificar(dato)
        if pulsacion is None:
            return None
        accion, token = pulsacion
        comun = {"canal": canal, "usuario": usuario, "conversacion": conversacion}

        if accion == CANCELAR:
            cancelada = self.cancelar_pendiente(token=token, **comun)
            return (
                "\u2716\ufe0f Cancelado." if cancelada
                else "\u2716\ufe0f Esa accion ya no estaba pendiente."
            )

        detalle, es_error = await self.confirmar_pendiente(token=token, **comun)
        return f"\u26a0\ufe0f No se pudo: {detalle}" if es_error else f"\u2705 {detalle}"

    def _anotar_en_historial(self, conversacion: str, nota: str) -> None:
        """Deja constancia en el hilo de lo que paso fuera del bucle del agente.

        Va como mensaje de usuario porque es informacion que el agente tiene
        que tener en cuenta y viene de fuera de su turno; el corchete marca que
        no lo escribio la persona.
        """
        self.store.anadir_mensaje(
            conversacion, "user", [{"type": "text", "text": nota}]
        )

    async def cerrar(self) -> None:
        await self.cliente.close()
        await self.ctx.cerrar()
