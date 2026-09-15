# Casa AI

Sistema de agentes con IA que controlan de verdad la instalación de la casa. Le
hablas por Telegram o WhatsApp (texto o nota de voz) y actúa: mira la
producción solar, fuerza la batería, sube una persiana, pone música en la
cocina, te dice quién está conectado al wifi o te describe lo que ve una
cámara.

No es un chatbot que explica cómo hacer las cosas. Ejecuta.

## Los sistemas que controla

| Subsistema | Hardware | Cómo se integra |
|---|---|---|
| Energía | **Sungrow**: inversor híbrido, o planta con inversor de cadena + batería + contador tras un Logger1000 | Modbus TCP directo (mapa de la planta en el YAML), o sensores de Home Assistant |
| Domótica | **ONNA** (bus KNX) | Home Assistant (integración `knx`); bus KNX directo como último recurso |
| Música | **BluOS** (Bluesound / NAD) | API local en el puerto 11000, sin nube |
| Red | **UniFi Network** | API del controlador |
| Cámaras | **UniFi Protect** | API de Protect o proxy de Home Assistant, con visión real sobre las capturas |
| Todo lo demás | Wallbox, TaHoma, Nuki, HEOS, LG ThinQ, Anthem, spa, riego… | Home Assistant, con catálogo semántico propio — ver [`docs/INTEGRACIONES.md`](docs/INTEGRACIONES.md) |
| Voz | HomePods y Apple TV para hablar; iPhone o satélite de Assist para escuchar | `/chat` desde un atajo de Siri, `/voz` desde la integración de Home Assistant, y `tts.speak` para la voz de salida — ver [`docs/VOZ.md`](docs/VOZ.md) |

Todo es local: el agente habla con el hardware por la LAN. Lo único que sale a
internet son los turnos de conversación con Claude. Energía y cámaras tienen
además un camino alternativo a través de Home Assistant, que es lo que permite
ejecutar el backend fuera de casa si te interesa.

## Cómo se pide algo

```
Tú     → ¿cuánto está produciendo el sol?
Agente → 3,2 kW. La batería al 78 % cargando a 1,1 kW y estás
         exportando 900 W a la red.

Tú     → pon la lista de jazz en el salón y la cocina, volumen bajo
Agente → Hecho. Salón y cocina agrupados, preset «Jazz», volumen 22.

Tú     → ¿hay alguien en la puerta?
Agente → Sí, una persona con una caja, de espaldas a la cámara.
         Parece un reparto.

Tú     → ¿puedo poner la lavadora?
Agente → Sí. Estás vertiendo 3,1 kW y la batería al 94 %, así que hay
         2,8 kW libres. La lavadora son 2,2 kW: entra de sobra y te
         sale gratis. ¿La arranco?

Tú     → carga la batería a 3 kW desde la red
Agente → Voy a forzar la carga de la batería a 3000 W tirando de red.
         Ahora mismo estás en punta tarifaria, así que te costará
         dinero. ¿Lo confirmo?
Tú     → sí
Agente → Hecho: cargando a 3000 W. Recuerda volver a autoconsumo.
```

Ese último caso es la pieza importante: **las acciones de riesgo no se ejecutan
a la primera**.

## Seguridad: por qué se le puede dar la llave de la casa

Cada herramienta está clasificada por riesgo, y la clasificación se comprueba
en los tests:

- **Lectura** — consultar estado. Se ejecuta sin preguntar.
- **Medio** — cambios reversibles y evidentes: luces, persianas, volumen,
  escenas. Se ejecuta directamente.
- **Alto** — batería, wifi, bloqueo de dispositivos, escritura cruda en el bus
  KNX. **No se ejecuta.** El sistema crea una acción pendiente con un token y
  obliga al agente a pedir confirmación. Solo cuando confirmas en un mensaje
  posterior se ejecuta.

En Telegram y WhatsApp, además, **el token de confirmación no pasa por el
contexto del modelo**: el agente explica lo que quiere hacer y se detiene, y te llega un
mensaje aparte con dos botones. Al pulsar, el código se ejecuta directamente
sin volver a pasar por el modelo. Una inyección de prompt se queda sin nada que
reutilizar.

Y —esto es lo que hace que la garantía valga algo también en los demás
canales— **la espera al humano está en el código, no en el prompt**. El store cuenta turnos humanos, y una acción
propuesta en el turno N solo se puede confirmar en el N+1 o más tarde. Sin eso,
el modelo podía proponer y confirmar en la misma vuelta, y bastaba una
inyección en cualquier texto que llegase a su contexto —el hostname de un
equipo que se une al wifi, el título de una emisora— para que ejecutase una
acción física sin que nadie dijera nada.

Además:

- Los tokens de confirmación son de **128 bits**, **caducan a los 15 minutos**,
  se usan **una sola vez** y están atados a **canal, usuario y conversación**:
  un token de Telegram no se puede confirmar desde WhatsApp ni desde otro chat.
- **El token nunca se escribe en la auditoría.** La puede leer una herramienta
  de solo lectura y un endpoint HTTP; el token autoriza la acción.
- **El API HTTP exige `API_TOKEN`** y su identidad es fija: un llamante no
  puede hacerse pasar por un chat de Telegram. Sin token, los endpoints
  devuelven 503 en vez de servirse abiertos. Por defecto solo escucha en
  localhost.
- **El webhook de WhatsApp verifica la firma HMAC de Meta** antes de mirar el
  contenido. El número del remitente viaja en el cuerpo, así que sin eso
  cualquiera podría hacerse pasar por ti.
- **Lista blanca de servicios** en Home Assistant. El agente no puede llamar a
  `homeassistant.stop`, ni abrir cerraduras (`lock.unlock`), ni desarmar la
  alarma: no están en la lista, a propósito. Y los **scripts están denegados**
  salvo los que declares, porque un script de HA ejecuta cualquier secuencia y
  se saltaría esa exclusión.
- **Lista blanca en el bus KNX.** Solo se puede escribir en las direcciones de
  grupo declaradas en `config/config.yaml`. Una dirección inventada se rechaza.
- **Tope duro de potencia** en la batería (`SUNGROW_MAX_POTENCIA_W`). Ninguna
  herramienta puede superarlo ni con confirmación.
- **Autorización por chat_id / número.** Si la lista está vacía, no se atiende
  a nadie. Un bot de Telegram es alcanzable por cualquiera.
- **Auditoría completa** en SQLite: cada acción, quién la pidió y qué resultó.
  Consultable desde el propio chat con `/auditoria`.
- **Verificación de TLS activada** por defecto: por el canal de UniFi viajan
  las credenciales del administrador local.
- Las **rutinas programadas no pueden ejecutar acciones de riesgo**: a las 8 de
  la mañana no hay nadie para confirmar. Avisan y proponen.

El sistema pasó una revisión de seguridad que encontró seis vulnerabilidades
reales, todas corregidas y todas con test que las cubre. Están documentadas una
por una, con lo que permitían y lo que sigue sin resolver, en
**[`docs/SEGURIDAD.md`](docs/SEGURIDAD.md)**. Cómo hablarle por voz, incluido un
satélite en el cuarto de un niño sin teléfono: [`docs/VOZ.md`](docs/VOZ.md). El orden
real de instalación, fase a fase: [`docs/PUESTA_EN_MARCHA.md`](docs/PUESTA_EN_MARCHA.md).

## Puesta en marcha

Hazlo por partes. Cada paso se verifica solo, y el sistema funciona con lo que
tenga configurado: lo que falte simplemente no se le ofrece al agente.

### Paso 0 — Elegir la máquina e instalar

**Si tienes Home Assistant OS** (o vas a instalarlo), la ruta corta es el
**complemento**: este repositorio es también una tienda de complementos de HA.
Ajustes → Complementos → Tienda → Repositorios → pega la URL del repo →
instala **Casa AI** → rellena el formulario → Iniciar. No hay Docker ni `.env`,
y el acceso a Home Assistant se resuelve solo. Está explicado en
[`addon/casa_ai/DOCS.md`](../addon/casa_ai/DOCS.md); los pasos 1 a 5 de abajo
siguen valiendo, con el formulario en lugar del `.env`.

Para el resto de casos hace falta Python 3.11+ (o Docker) en **un equipo de tu
red de casa**: el NAS,
un mini PC, una Raspberry, o el mismo sitio donde corra Home Assistant. Cuatro
de los cinco subsistemas solo hablan dentro de la LAN.

**El gateway UniFi no sirve como máquina** —es un electrodoméstico cerrado, las
actualizaciones de firmware borran cualquier modificación, y además es tu
router—, pero sí es el mejor sitio para el túnel VPN si acabas ejecutando esto
en la nube. La comparativa de equipos y las tres opciones de despliegue con sus
concesiones están en **[`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md)**; el código
es el mismo en todas.

```bash
git clone https://github.com/josuepascual-pixel/casa-ai && cd casa-ai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,voz]"      # sin `voz` va igual, solo por texto

cp .env.example .env
cp config/config.example.yaml config/config.yaml
```

El extra `voz` (transcripción local de notas de voz, más `ffmpeg` en el
sistema) es la dependencia más pesada del proyecto y la única exigente en
equipos pequeños. Déjala fuera si te basta con texto: el sistema lo detecta y
te lo dice en lugar de fallar de forma rara.

### Paso 1 — Que hable (15 min)

Dos cosas en `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
API_TOKEN=          # python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Comprueba y habla:

```bash
python -m casa_ai.verificar
python -m casa_ai.main chat "hola, qué sistemas controlas?"
```

Ya tienes el agente vivo, aunque todavía sin nada que controlar.

### Paso 2 — Encontrar los aparatos (2 min)

```bash
python -m casa_ai.descubrir
```

Recorre tu red y te dice qué ha encontrado y dónde: reproductores BluOS (con
su nombre real), el inversor, Home Assistant, el UniFi. Además **te escribe los
bloques de `.env` y de `config.yaml` listos para pegar**. Solo lee; no cambia
nada en ningún equipo.

### Paso 3 — Los tokens

Tres sitios de donde sacarlos:

| Qué | Dónde |
|---|---|
| `HA_TOKEN` | Home Assistant → tu perfil (abajo izquierda) → Seguridad → Tokens de acceso de larga duración |
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/botfather) en Telegram → `/newbot` |
| `UNIFI_USUARIO` / `UNIFI_PASSWORD` | Consola UniFi → Settings → Admins → crea un admin **local** (no la cuenta de ui.com) |

Con el bot de Telegram hay un segundo paso: arranca el backend, escríbele algo,
y los logs te dirán tu `chat_id`. Ponlo en `TELEGRAM_CHATS_AUTORIZADOS` y
reinicia. **Hasta que lo hagas el bot no responde a nadie**, y eso es
deliberado.

### Paso 4 — Verificar de verdad

```bash
python -m casa_ai.verificar
```

Contacta con cada subsistema, hace una lectura real y te dice qué responde y
qué falla, con la causa y el arreglo. Aquí también salen los `id` de tus
cámaras, para pegarlos en `config.yaml`.

Al final te guía la **única** comprobación que no se puede automatizar: abres
iSolarCloud en el móvil y confirmas que el sentido de la batería y de la red
coinciden con lo que lee el sistema. Si no coinciden, te ofrece corregir el
`.env` él mismo. No hacen falta números exactos —las lecturas no son del mismo
segundo—, solo que el sentido sea el mismo.

> El dongle WiNet-S admite **una sola** conexión Modbus simultánea. Si Home
> Assistant ya está leyendo el inversor, este cliente no entrará. Hay que
> elegir uno de los dos.

### Paso 5 — Arrancar

```bash
casa-ai                # backend + bot de Telegram + rutinas
# o
docker compose up -d
```

Desde el chat: `/estado` te dice qué hay conectado, `/auditoria` qué se ha
ejecutado, `/reset` olvida la conversación.

Y hay un **panel web** en `http://<equipo>:8099/panel`: producción, consumo,
batería, de dónde sale lo que consume la casa ahora mismo, el excedente
disponible con qué cabe en él, el estado de los aparatos, las cámaras y un
chat. Pide el token al abrirlo y lo guarda solo en esa pestaña. **Solo lee**:
las acciones van por el chat, que pasa por la capa de seguridad.

Y por HTTP, con el token:

```bash
curl -X POST localhost:8099/chat \
     -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"mensaje":"¿cuánto produce el sol?"}'

curl localhost:8099/avisos-seguridad -H "Authorization: Bearer $API_TOKEN"
```

### Ajustar la casa a tu vocabulario

En `config/config.yaml`, la sección `alias_entidades` es la que más rendimiento
da por minuto invertido:

```yaml
alias_entidades:
  luz salon: light.knx_salon_general
  persiana cocina: cover.knx_persiana_cocina
  termo: switch.knx_termo_electrico
```

Con eso, «apaga la luz del salón» no depende de que el agente acierte el
`entity_id`. Y `notas_casa` le da el contexto que no puede deducir: tu tarifa
eléctrica, qué no debe arrancar antes de las 11, qué consume mucho.

La sección `personas:` dice quién habla por cada chat y con qué nivel:
`dueno`, `adulto` o `nino`. Un niño solo puede con luces, música y persianas,
y preguntar cuánto sol hay; nada de cámaras, energía ni red. Quien no esté en
la lista cuenta como niño.

La sección `asistente:` le da nombre (`Jarvis` por defecto) y trato (`usted`
o `tu`). El carácter no se configura: sereno, formal, con algo de ironía seca,
y está escrito en el prompt para que no se pueda perder por un YAML mal
escrito.

Recarga sin reiniciar:
`curl -X POST localhost:8099/recargar-inventario -H "Authorization: Bearer $API_TOKEN"`

## El excedente solar: lo que ninguna app hace sola

La razón de fondo para tener un sistema así. `excedente_solar` cruza el
inversor con los consumos grandes de la casa —cargador del coche, spa, termo,
lavadora— y dice qué cabe en el sol que está sobrando ahora mismo.

Solo cuenta como excedente lo que se vierte a la red, más lo que entra en la
batería si ya está casi llena (por debajo de eso, cargarla es mejor uso), y
reserva un margen para que una nube no te pase a importar. Recomienda; no
ejecuta hasta que confirmas.

Declara tus aparatos en la sección `dispositivos:` de `config/config.yaml` con
su consumo nominal, y ya. Detalle en
[`docs/INTEGRACIONES.md`](docs/INTEGRACIONES.md).

## Productividad: el agente que habla primero

Dos rutinas programadas, con las mismas herramientas y la misma capa de
seguridad:

- **Informe matinal** (8:00): estado de la batería tras la noche, cualquier
  cosa rara (equipo de red caído, cámara desconectada, batería fría) y una
  recomendación para el día.
- **Vigilancia** (cada 30 min): solo interrumpe si hay algo que merezca la
  pena. Si no, calla.

Se configuran en `src/casa_ai/automations/rutinas.py`.

## Arquitectura

```
Telegram / WhatsApp / HTTP / CLI
              │
       ┌──────▼──────┐
       │   Agente    │  Claude Opus 5, bucle manual con tool use
       │ (streaming) │  thinking adaptativo + caché de prompt
       └──────┬──────┘
       ┌──────▼──────┐
       │  Ejecutor   │  clasifica riesgo · confirma · audita
       └──────┬──────┘
       ┌──────▼──────────────────────────────────┐
       │ Home Assistant · Sungrow · KNX/ONNA     │
       │ BluOS · UniFi Network · UniFi Protect   │
       └─────────────────────────────────────────┘
```

Detalle en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md). Dónde instalarlo, en
[`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md). Modelo de amenazas y revisión de
seguridad, en [`docs/SEGURIDAD.md`](docs/SEGURIDAD.md).

## Desarrollo

```bash
PYTHONPATH=src python -m pytest tests -q   # 158 tests
ruff check src tests
```

Los tests cubren el decodificado Modbus con sus signos, la capa de seguridad
(incluido el flujo de confirmación entre turnos), el parseo de BluOS, la lista
blanca de Home Assistant, el descubrimiento de red y el bucle del agente con la
API simulada. No tocan hardware real.

Comandos disponibles:

| Comando | Para qué |
|---|---|
| `casa-ai` | Arranca backend, bot y rutinas |
| `python -m casa_ai.verificar` | Comprueba cada subsistema y guía los arreglos |
| `python -m casa_ai.descubrir` | Busca los aparatos en tu red |
| `python -m casa_ai.main chat "..."` | Habla con el agente desde la terminal |
| `python -m casa_ai.main diagnostico` | Qué subsistemas están operativos |
| `python -m casa_ai.adapters.sungrow` | Volcado crudo de registros del inversor híbrido |
| `python -m casa_ai.adapters.planta sondear` | Planta con Logger1000: qué ids de esclavo responden |
| `python -m casa_ai.voz disenar` | Genera vistas previas de la voz de Jarvis con Voice Design de ElevenLabs (sin clonar a nadie) |
| `python -m casa_ai.adapters.planta leer` | Planta: todas las señales declaradas, para casarlas con iSolarCloud |
| `GET /avisos-seguridad` | Configuraciones que dejan la instalación expuesta |

## Añadir una herramienta

Un fichero en `src/casa_ai/tools/`, una `Herramienta` con su esquema, su riesgo
y su handler. El registro la recoge sola. Si es de riesgo alto, los tests te
obligarán a escribir el resumen de confirmación.

## Límites conocidos

- KNX no confirma ejecución: un telegrama enviado no garantiza que el actuador
  haya actuado. Para tener certeza hay que leer el estado.
- WhatsApp necesita un webhook público por HTTPS (un túnel de Cloudflare o
  similar). Telegram va por polling y no necesita abrir nada.
- El histórico depende de Home Assistant: por Modbus el inversor solo da el
  instante actual y los acumulados totales.
- El dongle WiNet-S de Sungrow admite una sola conexión Modbus. Si Home
  Assistant ya la tiene, usa `ENERGIA_ORIGEN=homeassistant` y se leen los
  mismos datos de sus sensores, sin conflicto.
- Leyendo la energía vía Home Assistant, controlar la batería solo es posible
  si declaras las entidades de control en `energia_ha:`. Si no están, el
  sistema dice que solo puede leer en lugar de fingir que ha actuado.
