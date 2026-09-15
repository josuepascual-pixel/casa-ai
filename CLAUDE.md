# Casa AI

Agentes conversacionales que controlan de verdad una casa: fotovoltaica
Sungrow, domótica ONNA (bus KNX), audio BluOS, red UniFi, cámaras Protect, y el
resto de aparatos a través de Home Assistant. Se le habla por Telegram o
WhatsApp, texto o nota de voz.

## Comandos

```bash
pip install -e ".[dev,voz]"                  # `voz` es opcional y pesada
PYTHONPATH=src python -m pytest tests -q     # 540 tests, no tocan hardware
ruff check src tests                         # debe quedar limpio
casa-ai                                      # backend + bot + rutinas

python -m casa_ai.verificar                  # comprueba cada subsistema de verdad
python -m casa_ai.descubrir                  # busca los aparatos en la LAN
python -m casa_ai.main chat "como va todo?"  # hablar desde la terminal
python -m casa_ai.adapters.sungrow           # volcado crudo (inversor hibrido)
python -m casa_ai.adapters.planta sondear    # planta: que ids responden tras el Logger1000
python -m casa_ai.voz disenar                # vistas previas de la voz de Jarvis (ElevenLabs)
```

Los tests necesitan `PYTHONPATH=src` (layout src, sin instalar). `faster-whisper`
**no** está instalado en desarrollo: es un extra, y hay un test que comprueba
que el núcleo no lo arrastra.

## Cómo está montado

```
canal (telegram/whatsapp/http/cli)
  └─ Aplicacion.responder(canal, usuario, conversacion, entrada)
       └─ Agente          bucle manual con tool use, streaming
            └─ Ejecutor   clasifica riesgo · confirma · audita
                 └─ adaptadores
```

`Aplicacion.responder` es el único punto de entrada. Un canal nuevo (Siri,
panel web, Matrix) solo tiene que llamarlo: seguridad, auditoría e historial
ya están resueltos.

## Decisiones que no son obvias

Si vas a cambiar algo de esto, lee primero por qué está así.

**El bucle del agente es manual, no el tool runner del SDK.** La confirmación
de acciones de riesgo cruza turnos: el usuario dice «fuerza carga», el sistema
pide confirmación, y el «sí» llega en un mensaje posterior. El tool runner
cierra el bucle cuando no hay más llamadas, así que ese «sí» tiene que entrar
como turno nuevo con el historial detrás.

**El orden de las herramientas es estable a propósito.** `tools` se renderiza
antes que `system`, y la caché de prompt es coincidencia de prefijo. Si el
orden variara, se invalidaría en cada mensaje. `Registro.disponibles()`
devuelve ordenado alfabéticamente; hay un test que lo fija.

**La hora va en el turno del usuario, nunca en el system prompt.** Por lo
mismo: en el prefijo cacheado sería un fallo de caché garantizado.

**Toda marca de tiempo visible pasa por `tiempo.py`.** En contenedor la hora
del sistema es UTC y el agente cree que la casa está en `ZONA_HORARIA`; sin
esto la auditoría sale desfasada y el agente informa de horas falsas.

**Los signos del inversor se derivan del registro de estado 13000, no del
valor.** El firmware de Sungrow no es homogéneo: a veces 13021 viene firmado y
a veces es magnitud sin signo. Las direcciones del código están en **base 0**
(pymodbus), no en la base 1 del documento de protocolo.

**La planta comercial lleva el mapa de registros en el YAML, no en el código.**
Tras un Logger1000 hay tres equipos (inversor SG-CX, batería ST, contador
Janitza), cada uno con su id de esclavo y su mapa, y solo el del inversor es
público. `adapters/planta.py` trae los de serie y `planta:` en `config.yaml`
los completa señal a señal; un nombre nuevo se lee y sale en `crudo`, que es
cómo se casa un registro contra iSolarCloud sin tocar código. El control de
la batería son escrituras declaradas por modo (`ordenes:`), y sin ellas el
adaptador solo lee y la herramienta de control no se ofrece. Sin
`SUNGROW_HOST`, el registrador se busca en UniFi (por `SUNGROW_MAC` o por el
prefijo de fabricante de Sungrow, probando por Modbus cuál contesta) y se
vuelve a buscar si deja de responder, porque el router no deja reservar IP.

**Los campos energéticos desconocidos son `None`, no cero.** `resumen()` omite
lo que falta. Devolver `0.0` en la salud de la batería porque no se pudo leer
haría que el agente informase de una batería muerta.

**Los `tool_result` vuelven todos en un solo mensaje de usuario.** Repartirlos
en varios le enseña al modelo a dejar de pedir llamadas en paralelo.

**Recargar el inventario rehace los adaptadores, no solo relee el YAML.**
BluOS, KNX y Home Assistant se quedan con una referencia al inventario, y el
prompt del sistema lleva el inventario dentro, así que `Aplicacion.recargar_inventario()`
cierra y reconstruye contexto y agente. Limpiar la caché de `get_inventario()`
no hacía nada en ejecución.

**Cada camino a un subsistema es un adaptador, no un `if` en la herramienta.**
La energía se lee por Modbus o por sensores de Home Assistant, y las cámaras
por UniFi Protect o por el proxy de HA. Los dos casos son un `Protocol`
(`FuenteEnergia`, `FuenteCamaras`) con su elector en `Aplicacion`. Las cámaras
lo hacían dentro de la herramienta: la misma escalera tres veces, y un
`id_protect` que a veces guardaba un `entity_id` de HA. Ahora cada sistema
tiene su campo (`id_protect`, `entidad_ha`) y los dos son opcionales.

**`camara_ver` devuelve un bloque de imagen, no metadatos.** El modelo mira la
captura de verdad. Por eso «¿hay alguien en la puerta?» funciona.

## Seguridad: las reglas que no se relajan

- **Riesgo ALTO no se ejecuta.** Crea una acción pendiente con token y exige
  confirmación en un mensaje posterior. Tokens de 128 bits, de un solo uso,
  caducan, y atados a canal + usuario + conversación.
- **El token nunca pasa por el contexto del modelo, en ningún canal, y el
  modelo no tiene ninguna herramienta para confirmar.** En Telegram y WhatsApp
  el canal manda botones y el token va en el `callback_data`; al pulsar, el
  canal llama al código de confirmación directamente
  (`Aplicacion.confirmar_pendiente`). En los canales sin botones (API,
  terminal, satélite de voz) el «sí» lo reconoce **el código**
  (`afirmaciones.py`, desde `Aplicacion.responder`): un sí claro ejecuta, un no
  claro cancela, y cualquier otro mensaje cancela la propuesta y sigue. Antes el
  modelo recibía el token y llamaba a `ejecutar_accion_pendiente`; la garantía
  era «pasó un turno humano», no «el humano dijo que sí», y una inyección en el
  turno siguiente podía confirmar. Canal, usuario, conversación, un solo uso y
  caducidad se comprueban siempre en `Store.tomar_pendiente`. **No relajes
  esto.**
- **El token no se escribe en la auditoría**: la lee una herramienta de solo
  lectura y un endpoint HTTP.
- **Todo endpoint HTTP exige `API_TOKEN`** y la identidad del canal es fija,
  nunca la del cuerpo de la petición. El webhook de WhatsApp verifica el HMAC
  de Meta antes de mirar el contenido.
- **Toda herramienta de riesgo ALTO necesita `resumen_confirmacion`.** Hay un
  test que lo obliga: sin él no se puede pedir una confirmación útil.
- **Listas blancas, no listas negras.** Servicios de Home Assistant
  (`SERVICIOS_PERMITIDOS`) y direcciones del bus KNX (solo las del YAML).
  Fuera a propósito: `homeassistant.stop`, `lock.unlock`,
  `alarm_control_panel.alarm_disarm`, `remote.send_command`, `knx.send`. Un
  `cover` con `device_class` garage, gate o door es abrir la casa: solo
  `casa_abrir_acceso` (riesgo alto). Y los `script.`
  denegados salvo los declarados: un script ejecuta cualquier secuencia y se
  saltaría esas exclusiones.
- **La autorización falla cerrada.** Lista de chats vacía = no se atiende a
  nadie.
- **Las rutinas programadas no ejecutan acciones de riesgo.** A las 8 de la
  mañana no hay nadie para confirmar, y eso está **declarado**:
  `confirmacion="imposible"` en el `Contexto` hace que el Ejecutor no cree
  acción pendiente ni emita token. Apoyarse solo en el guardia de turno dejaba
  el token vivo y saliendo por Telegram dentro del aviso de la rutina.
- **Lo que no está configurado no se le ofrece al modelo.** `requiere` con el
  combinador escrito —`"nombre"` o `Cualquiera(...)` para «basta uno»,
  `Todos(...)` para «hacen falta todos»— y `disponible_si` solo para
  **capacidades**, lo que «estar configurado» no puede decir. Una tupla que
  significara O en un sitio y Y en otro era indistinguible a la vista.
  `Registro.ausentes(ctx)` dice qué falta y por qué; sale en `/salud` y en
  `/estado`.

- **Quién habla lo decide el canal, y un niño solo ve la lista blanca.**
  `personas:` en el inventario asigna un nivel a cada chat o número; el
  `Contexto` lleva la `persona` y el registro no ofrece a un niño nada que no
  esté marcado `para_ninos` (ninguna de riesgo alto puede estarlo; hay un
  test). `Registro.disponible()` lo vuelve a comprobar en el Ejecutor, y el
  niño recibe Home Assistant **restringido** a sus dominios en el propio
  adaptador, para que ninguna herramienta pueda saltárselo. Las entidades
  `privadas` de una persona (su peso) las oculta esa misma vista a todos los
  demás, incluido el dueño y las rutinas, con el mensaje de una entidad
  inexistente. Quien no está declarado es lo que su canal diga en
  `NIVEL_SIN_DECLARAR`: dueño para `cli`, `http` y `rutina`, niño para `voz`,
  y en los chats niño si hay personas declaradas.

## Añadir cosas

**Una herramienta**: un fichero en `src/casa_ai/tools/`, una `Herramienta` en su
lista `HERRAMIENTAS`, y `construir_registro()` la recoge. Los tests de
`test_registry.py` exigen descripción suficiente, esquema válido para
`strict: true` y, si es de riesgo alto, resumen de confirmación.

**Un sistema nuevo**: un adaptador en `adapters/` con `configurado` y
`cerrar()`, añadirlo al `Contexto`, y `requiere="nombre"` en sus herramientas.

**Un segundo camino a algo que ya existe** (otra marca de inversor, otro NVR):
no una escalera `if` dentro de la herramienta. Un `Protocol` en `adapters/` con
las dos implementaciones y un elector, como `FuenteEnergia` y `FuenteCamaras`.
Las herramientas piden `ctx.energia` o `ctx.camaras` y no saben cuál hay.

**Un aparato de los que ya hay en Home Assistant**: no toques código. Declárelo
en la sección `dispositivos:` de `config/config.yaml` con su `consumo_w` y
recarga con `POST /recargar-inventario`. Ver `docs/INTEGRACIONES.md`.

## Convenciones

- **Todo en castellano**: nombres de clases, funciones, variables, comentarios,
  mensajes de error y descripciones de herramientas. Los nombres de las
  herramientas también, porque el usuario habla en castellano.
- **Los errores de adaptador se redactan para que el agente los lea y actúe**:
  dicen qué falta y cómo arreglarlo, no solo qué falló.
- Líneas de 100 columnas, `from __future__ import annotations`, tipos en todas
  las firmas.
- Los comentarios explican **por qué**, no qué. Si un comentario se puede
  deducir del código, fuera.

## Documentación

- `README.md` — puesta en marcha paso a paso
- `docs/ARQUITECTURA.md` — decisiones de diseño en detalle
- `docs/DESPLIEGUE.md` — dónde instalarlo y qué se pierde en cada opción
- `docs/INTEGRACIONES.md` — las apps de la casa y su vía de integración
- `docs/SEGURIDAD.md` — modelo de amenazas, la revisión y lo que sigue abierto
- `docs/VOZ.md` — los tres caminos de voz: Telegram, atajo de Siri y satélite por aparato
- `docs/RED.md` — lo que Jarvis no puede proteger: la red, las cuentas y los móviles
- `docs/PUESTA_EN_MARCHA.md` — el orden real de instalación, fase a fase, con `config/config.casa.yaml`

## El panel

`src/casa_ai/panel/` — página en `/panel`, script en `/panel.js`, datos en
`/api/panel`. La página y el script se sirven sin token porque no contienen
datos; los datos sí van autenticados. **El panel solo lee**: no hay ninguna
acción, van por `/chat` para que pasen por la capa de seguridad. Hay un test
que lo comprueba, y otro que no se use `innerHTML` (los nombres vienen de
aparatos y de Home Assistant: son datos, no marcado).

La paleta y las formas salen de la guía de visualización del proyecto y están
validadas: si tocas los colores, vuelve a pasar el validador. Con una sola
fuente de suministro la barra apilada se sustituye por un indicador, porque
una barra de un solo segmento es un antipatrón.
