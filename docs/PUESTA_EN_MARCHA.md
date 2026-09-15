# Puesta en marcha, en el orden acordado

Todo menos Sungrow y ONNA primero; esos dos, al final. Cada paso deja algo
que se puede probar preguntándole a Jarvis, y ninguno depende de los de
después. El inventario de la casa ya está escrito en
`config/config.casa.yaml`: se copia al complemento y se van descomentando
las secciones a medida que cada sistema entra.

Regla de todo el proceso: **ninguna clave ni contraseña se pega en un chat.**
Van al formulario del complemento, que las guarda en tu Home Assistant.

---

## Fase 0 — El cerebro (una tarde)

1. **Mini PC** (el ACEMAGICIAN K1 aprobado) enchufado por cable al switch
   más cercano al cuadro. Sin WiFi: el bus KNX y el Modbus quieren cable.
2. **Home Assistant OS**: descarga la imagen «Generic x86-64» desde
   home-assistant.io, grábala en un USB con Balena Etcher, arranca el mini PC
   desde el USB e instala en el disco. Diez minutos.
3. En un navegador de casa: `http://homeassistant.local:8123`. Crea tu
   usuario. Ese es el dueño de Home Assistant.
4. **Complemento Casa AI**: Ajustes → Complementos → Tienda → menú de tres
   puntos → Repositorios → pega
   `https://github.com/josuepascual-pixel/casa-ai` → Añadir. Instala
   «Casa AI». Tarda unos minutos porque construye la imagen.
5. **Claves** en la pestaña Configuración del complemento:
   - Clave de Anthropic (console.anthropic.com → API keys).
   - Token de Telegram: crea el bot en @BotFather («/newbot», nombre
     Jarvis). Guarda el token que te da.
   - Token del API: una contraseña larga que te inventes. Es la del panel y
     la del atajo de Siri.
6. Inicia el complemento. Escríbele cualquier cosa al bot: en el Registro
   del complemento sale «Mensaje rechazado … Tu chat_id es NNN». Pon ese
   número en «chats autorizados» y reinicia. Repite con el iPhone de Ana.
7. **Inventario**: Ajustes → Complementos → Casa AI → pestaña Configuración
   no sirve para esto; el fichero está en `/addon_configs/…casa_ai/config.yaml`
   (entra por el complemento «File editor» o por Samba). Pega el contenido de
   `config/config.casa.yaml` y pon los dos `chat_id` en `personas:`.

**Prueba**: «Jarvis, ¿estás ahí?». Y dos órdenes de Telegram que son la
puesta en marcha entera desde el móvil, porque el complemento no tiene
terminal: `/verificar` lee de verdad cada subsistema y dice qué falla y qué
hacer; `/descubrir` barre la red y te escribe los bloques del inventario
para pegar. `/estado` resume qué herramientas están activas y cuáles no, con
el motivo.

---

## Fase 1 — Lo que no necesita Home Assistant (una hora)

### UniFi Network y Protect (red y cámaras)

En la app UniFi: Ajustes → Administradores → añade un usuario local
«jarvis» con permisos de solo lectura en Network y de visualización en
Protect. Su usuario y contraseña van al formulario del complemento, con la IP
de la CloudKey. El certificado autofirmado: exporta el de la consola y ponlo
en la carpeta del complemento, o deja la verificación activada y sigue las
instrucciones del Registro si protesta.

Las tres cámaras ya están declaradas en el inventario con su nombre; el
identificador de Protect lo saca `/verificar` y se pega.

**Prueba**: «¿quién está conectado al wifi?», «¿qué se ve en la sala de
juegos?». Desde el chat de Ana o el tuyo; Leo no las verá nunca.

### BluOS (música)

`/descubrir` en Telegram. Lista los reproductores BluOS que encuentra con su
IP y te escribe el bloque `bluos:` para pegar en el inventario, con la zona
de cada uno.

**Prueba**: «pon música en el salón», «baja el volumen de la cocina».

---

## Fase 2 — Integraciones oficiales de Home Assistant (una tarde)

Todas se instalan igual: Ajustes → Dispositivos y servicios → Añadir
integración → buscar → seguir el asistente. Después de cada una, Jarvis ya
puede usarla con «busca la entidad de …»; declararla en `dispositivos:` del
inventario es lo que le da nombre, consumo y sentido para el excedente
solar. El bloque para cada una está en `config/config.casa.yaml`, comentado.

| Orden | Integración | Qué pide el asistente | Qué declarar |
|---|---|---|---|
| 1 | **Apple TV** | Aparece sola: un código en la pantalla del Apple TV y de cada HomePod | Los altavoces en `alias_entidades` («cocina: media_player.homepod_cocina») |
| 2 | **Wallbox** | Usuario y contraseña de la app Wallbox | `wallbox` con `consumo_w: 7400`, candidato a excedente |
| 3 | **Overkiz** (TaHoma) | Usuario y contraseña de Somfy, servidor «Somfy Europe» | Nada: las persianas y toldos salen solos como `cover.*` |
| 4 | **Nuki** | Token del Bridge (app Nuki → Bridge → API) | Nada. Solo cierra; abrir por chat está vetado |
| 5 | **HEOS** | La IP del Marantz NR1510 (192.168.0.219 según UniFi) | Nada: sale como `media_player` |
| 6 | **LG ThinQ** | Cuenta de LG | Los electrodomésticos que quieras vigilar (lavadora, secadora) |
| 7 | **Anthem A/V** | La IP del MRX 40 (192.168.0.225) | Nada. `send_command` está vetado |
| 8 | **ONVIF** | IP, usuario y contraseña de cada cámara Provision | `camaras:` con `entidad_ha` |

**Pruebas**: «¿está cargando el coche?», «sube las persianas del salón»,
«¿está cerrada la puerta?», «pon el cine en modo película», «avisa en la
cocina de que la lavadora ha terminado» (necesita la Fase 4).

---

## Fase 3 — Integraciones de la comunidad (HACS)

Primero HACS: complemento «Get HACS» → seguir su guía → reiniciar. Luego,
desde HACS, cada una:

| Integración | Repositorio en HACS | Nota |
|---|---|---|
| **EcoWater** | «ecowater» | Solo lectura: sal, consumo, regeneración. Pide la cuenta de HydroLink |
| **Solem** (riego) | «solem» | Depende del modelo de programador; si no aparece, el riego sigue por ONNA |
| **Balboa** (spa) | Ya es oficial: «Balboa Spa Client», por IP | El spa como candidato a excedente, `consumo_w: 3000` |
| **2N** (videoportero) | Comprobar antes si ya está en el bus KNX | Si no, integración 2N por su API HTTP (192.168.0.48) |

---

## Fase 4 — Voz

Todo en `VOZ.md`. En orden:

1. **La voz de Jarvis**: cuenta en ElevenLabs → Voice Design con la
   descripción del proyecto → guardar la que os guste como «Jarvis».
2. **Integración ElevenLabs** en Home Assistant con esa voz. Su entidad
   (`tts.elevenlabs`) va en el formulario del complemento, y el HomePod de la
   cocina en «altavoz del informe».
3. **Atajo de Siri** en tu iPhone y en el de Ana, cada uno con su token
   personal de Home Assistant. Después, en la app Casa, activar en cada
   HomePod «Reconocer mi voz» y «Peticiones personales»: el mismo atajo
   responde en la cocina.
4. **Satélite de Leo (opcional)**: solo si se quiere que hable con Jarvis
   desde su cuarto. Un Home Assistant Voice PE → pipeline de Assist con
   Whisper local, agente «Casa AI» (la integración de la carpeta
   `custom_components/`), voz ElevenLabs. Su `device_id` a `personas:`.

**Pruebas**: «Oye Siri, Jarvis» → «¿cuánto sol hay?», en el iPhone y en el
HomePod de la cocina.

---

## Fase 5 — Tanita

La báscula manda cada pesada a Apple Salud a través de la app My TANITA. De
ahí a Home Assistant va por un atajo del iPhone, y en Home Assistant queda
como un sensor con el nombre de la persona. Un sensor por persona, y solo su
chat lo ve. Tres pasos, y el primero se hace una sola vez.

**1. En Home Assistant, un webhook por persona.** Ajustes → Automatizaciones
→ Crear → editar en YAML, y pegar (cambia `ana` por el nombre en minúsculas
de cada persona, y el `webhook_id` por uno largo e imposible de adivinar):

```yaml
alias: Peso de Ana
triggers:
  - trigger: webhook
    webhook_id: peso-ana-7f3a9c2e1b
    allowed_methods: [POST]
    local_only: true
actions:
  - action: input_number.set_value
    target:
      entity_id: input_number.peso_ana
    data:
      value: "{{ trigger.json.peso }}"
```

Antes, el número donde se guarda: Ajustes → Dispositivos y servicios →
Ayudantes → Crear ayudante → Número, nombre «Peso Ana», mínimo 20, máximo
200, unidad kg. Eso crea `input_number.peso_ana`.

**2. En el iPhone de cada persona, el atajo.** App Atajos → Automatización →
Nueva → «Muestra de salud» → tipo Peso → «Se ha registrado una muestra» →
Ejecutar inmediatamente. Acciones:

1. **Buscar muestras de salud**: tipo Peso, ordenar por fecha de inicio, más
   reciente primero, límite 1.
2. **Obtener detalles de la muestra**: Valor.
3. **Obtener contenido de URL**: `http://<ip-de-home-assistant>:8123/api/webhook/peso-ana-7f3a9c2e1b`,
   método POST, cuerpo de solicitud JSON con una clave `peso` y como valor
   la salida del paso anterior.

`local_only: true` hace que el webhook solo acepte peticiones desde la red
de casa: pesarse fuera no llega, y nadie de fuera puede escribirlo.

**3. En el inventario**, cada sensor en su persona, como entidad privada:

```yaml
personas:
  - nombre: Ana
    nivel: adulto
    telegram: ["…"]
    privadas: ["input_number.peso_ana"]
```

Con eso, «Jarvis, ¿cuánto peso?» responde a Ana con lo suyo, y para todos los
demás ese sensor no existe: ni para el dueño, ni para el informe de la
mañana, ni para un chat sin registrar. Si alguien pregunta por él, Jarvis
responde que no hay ninguna entidad con ese nombre, sin decir que es
privada de otro.

---

## Al final — Sungrow y ONNA

**Sungrow**: con el complemento en marcha, `/verificar` ya dice si lee el
inversor. Lo fino también va por Telegram: `/sondear` dice qué ids de
esclavo responden detrás del Logger; `/sondear leer` muestra todas las
señales para comparar con iSolarCloud; `/sondear barrer 2 0 100` vuelca
registros crudos de un equipo para casarlos con lo que ve la app. Si el instalador ha mandado el
documento de la batería, se rellena `planta.bateria` y ya está; si no, se
casan registros con `barrer`.

**ONNA**: integración `knx` de Home Assistant con la IP del router Zennio
(está en UniFi como cliente Zennio) en modo túnel, y el proyecto ETS del
instalador para que las direcciones tengan nombre. Sin el proyecto, Home
Assistant puede descubrir el bus, pero cada dirección habrá que
identificarla a mano.
