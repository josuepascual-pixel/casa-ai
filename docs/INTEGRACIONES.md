# Tus 18 apps: qué se integra y cómo

Una app por aparato es el síntoma, no el problema. El problema es que ninguna
de ellas puede ver a las demás: la del inversor no sabe que el coche está
cargando, y la del coche no sabe que está saliendo el sol.

**La buena noticia: no hace falta escribir un adaptador para cada una.** Casi
todas tienen integración en Home Assistant, que es exactamente para lo que
existe. Este sistema habla directamente con cinco cosas (el inversor, el bus
KNX, BluOS, UniFi y Protect) porque ahí gana algo concreto, y todo lo demás lo
alcanza a través de Home Assistant con las herramientas genéricas
`casa_buscar_entidades`, `casa_estado` y `casa_accion`.

## El inventario

Lo que hay de verdad, sacado de las etiquetas y de la lista de clientes de la
red: un inversor de cadena **SG50CX-P2** (50 kW, unos 51 kWp de paneles), un
sistema de baterías **ST225CS-2H** (229 kWh, 110 kW) con su controlador
local, un contador **Janitza UMG104** y un **Logger1000** dentro del
COM100D-EU que los reúne y habla Modbus TCP. El Logger y el controlador de la
batería salen en UniFi como dos clientes de Sungrow en el switch de la
barbacoa. Además: pantallas ONNA (Raspberry Pi por cable), videoportero 2N,
caja Somfy TaHoma, enchufe Tapo P110, descalcificadora EcoWater, y todo el
audio de Apple (HomePods, Apple TV, AirPlay).

| App | Qué es | Vía | Estado |
|---|---|---|---|
| **Onna** | Pantallas IP-KNX sobre el bus KNX | Integración `knx` de HA (+ bus directo opcional) | ✅ Integrado |
| **iSolarCloud** | Planta Sungrow: inversor SG50CX-P2, batería ST225CS-2H, contador Janitza UMG104, todos tras un Logger1000 | Modbus TCP al Logger1000 (un id de esclavo por equipo), o sensores de HA | ✅ Integrado (batería: mapa pendiente) |
| **BluOS** | Audio multiroom Bluesound/NAD | API local, puerto 11000 | ✅ Integrado |
| **UniFi** | Red: gateway, switches, AP | API del controlador | ✅ Integrado |
| **Protect** | Cámaras UniFi | API de Protect + visión del modelo | ✅ Integrado |
| **Wallbox** | Cargador del coche eléctrico | Integración **oficial** `wallbox` | 🔧 Instalar en HA |
| **TaHoma** | Hub Somfy: persianas, toldos | Integración **oficial** `overkiz` | 🔧 Instalar en HA |
| **Nuki** | Cerradura inteligente | Integración **oficial** `nuki` (Bridge local) | 🔧 Instalar en HA |
| **HEOS** | Audio multiroom Denon/Marantz | Integración **oficial** `heos` | 🔧 Instalar en HA |
| **LG ThinQ** | Electrodomésticos LG | Integración **oficial** `lg_thinq` | 🔧 Instalar en HA |
| **Anthem Remote** | Procesador de AV Anthem | Integración **oficial** `anthemav` (TCP/IP) | 🔧 Instalar en HA |
| **Casa** (Apple Home) | HomeKit, HomePods, Apple TV | `homekit_controller` para importar; `homekit` para exportar; `apple_tv` para hablar por los HomePods | 🔧 Ver nota y `VOZ.md` |
| **Voz** | Atajo de Siri (adultos) y satélite de Assist (Leo) | `/chat` y `/voz` + integración `casa_ai` de HA | ✅ Integrado, ver `VOZ.md` |
| **EcoWater** | Descalcificadora (HydroLink Plus) | Integración de la comunidad `ecowater` (solo lectura: sal, consumo, regeneración) | 🔧 Instalar en HA |
| **Provision Cam2** | Cámaras Provision-ISR | `onvif` o cámara genérica por RTSP | 🔧 Instalar en HA |
| **MySOLEM** | Riego Solem | Comunidad (HACS). **Depende del modelo** | ⚠️ Ver nota |
| **ControlMySpa** | Spa / jacuzzi Balboa | Comunidad (HACS) + librería Python | ⚠️ No oficial |
| **My2N** | Videoportero 2N | Comunidad (2N/Helios IP). **Puede que ya esté en tu KNX** | ⚠️ Ver nota |
| **BlueEye** | — | — | ⛔ Omitida por decisión |
| **MyTanita** | Báscula de composición corporal | Apple Salud + atajo de iPhone → `/chat`, o la API Health Planet de Tanita | ⚠️ Ver nota: datos de salud, solo a su dueño |

Leyenda: ✅ ya funciona · 🔧 instalar su integración en HA y funciona ·
⚠️ integración de comunidad, más frágil · ⛔ deliberadamente fuera

## Notas que importan

**MySOLEM depende del modelo.** No hay una sola integración, hay varias y no
son intercambiables: los BL-IP van por Bluetooth (y el firmware 6.x de los
BL-IP V2 no está soportado por la integración principal), mientras que los
LR-IS/LR-IP van por LoRa contra la pasarela WiFi y necesitan la variante que
pasa por la nube de MySOLEM. Mira el modelo de tu programador antes de elegir.

**My2N puede que ya lo tengas.** 2N publica un manual de interoperabilidad con
ONNA, así que es posible que tu videoportero ya esté colgado del bus KNX y
aparezca como entidades de HA sin hacer nada. Compruébalo antes de instalar la
integración de comunidad.

**Apple Home es una decisión, no una instalación.** Hay dos direcciones y no
significan lo mismo:
- `homekit_controller` **importa** a HA los aparatos que solo hablan HomeKit.
- `homekit` **exporta** las entidades de HA a la app Casa, para que Siri y los
  HomePod las vean.

Si tienes HomePods, la segunda es la interesante: exportas lo que quieras a la
app Casa y le hablas a Siri para lo simple («enciende el salón»), mientras
reservas este sistema para lo que Siri no puede hacer («¿me compensa cargar el
coche ahora?»). No compiten.

**BlueEye queda fuera por decisión tuya.** No está integrada y no hay nada
pendiente por su parte. Si algún día la quieres dentro, dime qué controla y se
añade: si tiene integración en Home Assistant, es declararla en
`dispositivos:` y no hay que tocar código.

**MyTanita entra, con dos condiciones.** Es una báscula de composición
corporal, y eso son datos de salud: peso, grasa, masa muscular de cada
persona. La primera condición es que **cada dato lo ve solo su dueño**: Jarvis
puede decirte tu peso a ti por tu chat, nunca a Ana, nunca a Leo, y nunca
por un altavoz. La segunda es que **no pasa por el historial de conversación
más de lo imprescindible**: se guarda como lectura de sensor, no como texto.

Cómo llega: la báscula habla por Bluetooth con la app My TANITA del iPhone, y
la app escribe en Apple Salud. No hay integración de Home Assistant para
Tanita ni para Apple Salud, pero sí un camino corto y fiable: una
**automatización de Atajos** en el iPhone («cuando se añada una muestra de
peso a Salud») que manda la lectura a Home Assistant por webhook, donde queda
como sensor con el nombre de la persona (`sensor.peso_josue`). Jarvis lo lee
como cualquier otro sensor, y el veto por persona se hace con la misma
lista blanca que ya restringe a un niño: ese sensor solo lo ve el chat de su
dueño. El camino alternativo, la API Health Planet de Tanita, existe pero
está pensada para Japón y exige alta de desarrollador; queda como plan B.

Lo que hace falta: la automatización en cada iPhone (dos minutos, la
escribo yo paso a paso cuando lleguemos), y en el inventario una entrada por
persona con su sensor. Se hace en la fase de puesta en marcha, después de la
energía y la voz.

**Anthem: `send_command` queda fuera.** La integración permite mandar comandos
arbitrarios al procesador. Lo he dejado fuera de la lista blanca porque es un
canal libre por el que se puede enviar cualquier cosa al equipo; encender,
apagar y el volumen sí están.

**Nuki: solo cerrar.** `lock.lock` está permitido; `lock.unlock` no, y es
deliberado. Abrir la puerta de casa por chat es la única acción de esta lista
que no tiene vuelta atrás si algo va mal. Si la quieres, dilo explícitamente y
hablamos de cómo protegerla.

## Lo que ganas juntándolas: el excedente solar

Esto es lo que ninguna app puede hacer por separado, y es la razón de fondo
para tener un sistema así. La herramienta `excedente_solar` cruza el inversor
con los consumos grandes de la casa:

```
Tú     → ¿puedo poner la lavadora?
Agente → Sí. Estás vertiendo 3,1 kW a la red y la batería está al 94 %, así
         que hay 2,8 kW libres. La lavadora son 2,2 kW: entra de sobra y te
         sale gratis. ¿La arranco?

Tú     → aprovecha el sol que sobra
Agente → Hay 4,7 kW disponibles. Enciendo el termo (2 kW) y la lavadora
         (2,2 kW) y quedan 500 W de margen. El coche son 7,4 kW, no cabe;
         si quieres cargarlo puedo bajarle la corriente a 6 A.
```

Para que funcione hay que declarar los aparatos en la sección `dispositivos:`
de `config/config.yaml` con su consumo nominal y su prioridad. Sin
`consumo_w`, el agente no puede decidir qué cabe.

El criterio que aplica, y que puedes ajustar en `.env`:

- Solo cuenta como excedente **lo que se vierte a la red**, más lo que entra en
  la batería **si está por encima de `EXCEDENTE_SOC_MINIMO`** (90 % por
  defecto). Por debajo de eso, cargar la batería es mejor uso que encender un
  aparato.
- Se reservan `EXCEDENTE_MARGEN_W` (300 W) sin usar, para que una nube o un
  pico de consumo no te pasen a importar de la red.
- La herramienta **recomienda, no ejecuta**. El agente te dice qué encendería y
  cuánto consume, y actúa con `casa_accion` cuando tú lo confirmas.

## Cómo añadir uno de tus aparatos

1. Instala su integración en Home Assistant (la de la tabla).
2. Encuentra los `entity_id` que ha creado: en el chat, «busca las entidades
   del wallbox», o en HA → Herramientas de desarrollo → Estados.
3. Declaralo en `config/config.yaml`:

```yaml
dispositivos:
  - nombre: cargador del coche
    categoria: cargador_vehiculo
    entidad: switch.wallbox_carga
    entidades:
      potencia: sensor.wallbox_potencia_carga
      corriente: number.wallbox_corriente_maxima
    consumo_w: 7400
    excedente: true
    prioridad: 2
    notas: Admite regular la corriente, no solo encender y apagar.
```

4. `curl -X POST localhost:8099/recargar-inventario` y ya lo conoce.

No hace falta tocar código. El nombre que le pongas es el que podrás usar
hablando, y las `notas` se las lee el agente, así que sirven para avisarle de
lo que no puede deducir («no arrancar antes de las 11», «consume mucho»).

## Lo que sigue teniendo sentido tener aparte

Integrar todo no significa que las 18 apps sobren:

- **La app nativa siempre será mejor para la configuración inicial** de cada
  aparato. Empareja el Nuki con su app, calibra el spa con la suya.
- **iSolarCloud** para los históricos largos y las garantías del inversor.
- **Protect** para revisar grabaciones, no solo capturas.
- **UniFi** para la administración de red de verdad.

Lo que este sistema aporta no es sustituirlas: es que puedas preguntar una
cosa y que alguien mire en cinco sitios a la vez para responderte.
