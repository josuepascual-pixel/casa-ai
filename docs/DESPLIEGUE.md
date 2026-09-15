# Dónde instalarlo

El código es el mismo en las tres opciones. Lo que cambia es de dónde puede
leer, y eso lo decide la red, no el software.

## La restricción que manda

Cuatro de los cinco subsistemas **solo hablan dentro de tu red local**:

| Sistema | Protocolo | ¿Alcanzable desde internet? |
|---|---|---|
| Inversor Sungrow | Modbus TCP (502) | No |
| Música BluOS | HTTP (11000) | No |
| Bus KNX de ONNA | KNXnet/IP (3671) | No |
| UniFi Network y Protect | HTTPS local | No |
| Home Assistant | HTTPS | **Sí**, si lo expones |

Ninguno de esos protocolos tiene autenticación pensada para internet — BluOS
literalmente no tiene ninguna — así que **abrirlos al exterior no es una
opción**. Cualquier despliegue en la nube necesita, por tanto, *algo* en casa
que haga de puente. La pregunta real no es «nube o local», es **qué pones en
casa**.

La buena noticia: casi seguro ya tienes ese algo.

---

## ¿Y el gateway UniFi como máquina?

**No.** Es la pregunta natural —ya está encendido, ya está en la red— pero el
gateway tiene dos papeles posibles y solo sirve para uno:

| Papel | ¿Sirve el gateway? |
|---|---|
| **Extremo del túnel VPN** (opción B) | ✅ Sí, y es la mejor opción: los UDM, UDR, UDM-Pro y UCG traen servidor WireGuard integrado. Cero hardware nuevo. |
| **Máquina que ejecuta la aplicación** | ❌ No |

Por qué no, en orden de importancia:

1. **Es tu router.** Todo el tráfico de la casa pasa por ahí. Meterle un
   proceso Python con un agente, una base de datos y (si activas voz) un
   modelo de transcripción, es apostar tu conectividad entera a que ese
   proceso se porte bien. Cuando falle, no te quedarás sin asistente: te
   quedarás sin internet, y sin el asistente además.
2. **UniFi OS es un electrodoméstico cerrado.** Ubiquiti no ofrece ninguna
   forma soportada de instalar aplicaciones de terceros. Hay rutas de la
   comunidad para meter contenedores con `unifi-os shell` y scripts de
   arranque persistentes, pero son no soportadas y Ubiquiti las ha ido
   cerrando en las versiones nuevas.
3. **Cada actualización de firmware te lo borra.** Las modificaciones fuera
   de las rutas oficiales no sobreviven a un upgrade. Acabarías reinstalando
   a mano cada pocos meses, o retrasando actualizaciones de seguridad de tu
   router para no perder la instalación. Las dos salidas son malas.
4. **Los recursos están comprometidos.** La RAM y el almacenamiento de esos
   equipos están dimensionados para el stack de UniFi, y si tienes Protect
   grabando, ya va justo. No hay margen pensado para un inquilino extra.

Resumen: usa el gateway para el túnel, que es exactamente para lo que
Ubiquiti lo diseñó, y ejecuta la aplicación en otro sitio.

## Qué máquina, entonces

La respuesta depende de una sola cosa: **¿dónde corre tu Home Assistant?**

- **Ya lo tienes en un equipo que admite Docker** (NAS, mini PC, Proxmox,
  Raspberry con Raspberry Pi OS, HA Container o HA Supervised) → ese equipo.
  No compres nada, ve a la opción A.
- **Lo tienes como HA OS** (la instalación tipo electrodoméstico, incluidos
  Home Assistant Green y Yellow) → instala el **complemento**, opción 0. HA OS
  no deja lanzar contenedores sueltos, pero los complementos son exactamente
  eso con un formulario delante.
- **Todavía no tienes Home Assistant** → instala HA OS en la máquina que
  compres y ve a la opción 0. Es la ruta con menos pasos: un solo aparato, sin
  Docker, sin ficheros `.env`.

## Opción 0 — Complemento de Home Assistant (la más sencilla)

Este repositorio es también una tienda de complementos. En Home Assistant:
Ajustes → Complementos → Tienda → menú de tres puntos → Repositorios → pegar
la URL del repo → instalar **Casa AI** → rellenar el formulario → Iniciar.
Los detalles están en [`addon/casa_ai/DOCS.md`](../addon/casa_ai/DOCS.md).

Lo que resuelve por su cuenta, y por eso es la más sencilla:

- **No hay token de Home Assistant que crear**: usa el del Supervisor.
- **Datos persistentes y en las copias de seguridad** de HA, en `/data`.
- **Red del host**: KNX, Modbus y BluOS sin NAT ni `KNX_ROUTE_BACK`.
- **Actualizaciones** como cualquier complemento. Cada versión instala la
  etiqueta git `v<versión>` del repositorio; hay un test que obliga a que la
  versión del complemento y la del paquete coincidan.

Funciona todo lo de la opción A. La única diferencia práctica es que el
inventario (`config.yaml`) vive en la carpeta del complemento, que se edita
desde el editor de ficheros de HA o por Samba.

### Con qué te vale

| Equipo | Suficiente | Notas |
|---|---|---|
| NAS que ya tengas (Synology, QNAP, Unraid) | ✅ | La mejor opción si ya está encendido 24/7 |
| Mini PC x86 de segunda mano (un Dell/HP/Lenovo micro, 80-150 €) | ✅ Con holgura | Corre HA y esto sobrados, y la voz va rápida |
| Raspberry Pi 5 | ✅ | Cómodo, voz incluida |
| Raspberry Pi 4 (2 GB o más) | ✅ Justo con voz | Con el modelo `tiny` o `base` va bien; con `small` en 2 GB va apretado |
| Raspberry Pi 3 o Zero | ⚠️ Solo texto | Instala sin el extra de voz |
| Gateway UniFi, switch, punto de acceso | ❌ | Ver arriba |

**Sobre la transcripción de voz**: es la única parte exigente, y por eso es un
extra opcional. Si instalas sin ella, el sistema funciona igual por texto y te
lo dice claramente si le mandas una nota de voz. Para dejarla fuera:

```bash
pip install -e .                                  # sin voz
docker compose build --build-arg CON_VOZ=false    # imagen ligera
```

Y si la quieres pero el equipo es modesto, baja el modelo en `.env`:
`WHISPER_MODELO=base` (o `tiny`), en vez de `small`.

## Opción A — Junto a Home Assistant (recomendada)

Si Home Assistant ya corre en algún sitio de tu casa, ese sitio sirve. No hace
falta comprar nada.

```bash
docker compose up -d
```

- **Coste**: 0 €
- **Funciona todo**: Modbus, BluOS, KNX, UniFi, cámaras, control de batería.
- **Nada expuesto a internet**. El backend solo hace conexiones de salida
  (HTTPS a la API de Claude y a Telegram). Ningún puerto entrante.
- Si se cae internet, pierdes al agente pero no la casa: Home Assistant y el
  bus KNX siguen funcionando por su cuenta.

**Dónde exactamente**: cualquier equipo de la LAN que pueda correr Docker —un
NAS Synology o QNAP, un mini PC, una Raspberry Pi 4 o 5, un Proxmox. Si tu
Home Assistant es **HA OS** (la instalación tipo electrodoméstico), no te deja
lanzar contenedores arbitrarios; en ese caso usa otro equipo de la red o
instala Docker en un Pi aparte. Si es **HA Container** o **HA Supervised**, el
mismo host vale.

Un Raspberry Pi 4 de segunda mano son unos 40-60 €, y ya te sirve para esto y
para HA. Es bastante más barato que un VPS a dos años.

### En un NAS, mini PC o Proxmox (el caso más cómodo)

Si ahí ya corre Home Assistant en contenedor, esto va al lado y no hay nada
que decidir:

```bash
# En el equipo, no en tu portátil
git clone https://github.com/josuepascual-pixel/casa-ai && cd casa-ai
cp .env.example .env && cp config/config.example.yaml config/config.yaml
# rellena .env y config.yaml
docker compose up -d
docker compose logs -f
```

Cuatro detalles de este escenario:

- **Red bridge es suficiente.** Modbus, BluOS, UniFi y el túnel KNX son todos
  unicast hacia IP conocidas, y `KNX_ROUTE_BACK=true` (el valor por defecto)
  se encarga del NAT. Solo necesitarías `network_mode: host` si algún día
  añades descubrimiento por multicast; en ese caso quita la sección `ports`,
  que es incompatible, y pon `KNX_ROUTE_BACK=false`.
- **La zona horaria ya está resuelta.** El `docker-compose.yml` pasa
  `TZ=${ZONA_HORARIA}` al contenedor, y además todas las marcas de tiempo que
  ve el agente se renderizan con `ZONA_HORARIA` explícitamente. Sin eso la
  auditoría saldría en UTC y el agente informaría de horas equivocadas.
- **`config/` se monta en solo lectura.** El endpoint
  `/recargar-inventario` relee el YAML sin reiniciar, así que puedes editarlo
  desde el NAS y recargar.
- **Si Home Assistant ya lee el inversor por Modbus**, no habrá una segunda
  conexión disponible en el WiNet-S. Pon `ENERGIA_ORIGEN=homeassistant` y se
  leen los mismos datos de sus sensores, sin conflicto. Es exactamente el
  mismo código.

En Synology: Container Manager admite `docker-compose.yml` directamente
(Proyecto → Crear). En Proxmox, lo natural es un contenedor LXC con Docker o
una VM ligera.

---

## Opción B — Nube + túnel a tu red (VPN)

El backend vive en un VPS y se une a tu red de casa por una red virtual, así
que ve el inversor y los BluOS como si estuviera dentro.

- **Coste**: 4-6 €/mes de VPS
- **Funciona todo**, igual que la opción A.
- **Puente en casa**: el extremo del túnel. Y aquí está la clave: **tu gateway
  UniFi ya lo hace**. Los UDM, UDR y UDM-Pro traen servidor WireGuard y VPN
  integrados. Configúralo ahí y no necesitas ningún equipo nuevo.
  Alternativa: [Tailscale](https://tailscale.com) como add-on de Home
  Assistant, que es gratis para uso personal y atraviesa NAT sin abrir nada.

**Lo que hay que sopesar**: le estás dando a un host en la nube una ruta hacia
tu red doméstica. Si ese VPS se compromete, el atacante está dentro de tu LAN.
Y el túnel es un punto de fallo más: si cae, el agente se queda ciego aunque
tu casa esté perfectamente. Para lo que este sistema hace, es un riesgo que no
compra gran cosa frente a la opción A.

---

## Opción C — Nube, hablando solo con Home Assistant

El backend en la nube y **un único** endpoint expuesto: Home Assistant. Todo
lo demás lo alcanza HA, que sí está en casa.

Configuración:

```
# .env
ENERGIA_ORIGEN=homeassistant
HA_URL=https://tu-instancia.ui.nabu.casa     # o tu túnel
HA_TOKEN=...
# Sin SUNGROW_HOST, sin UNIFI_HOST, sin reproductores BluOS en el YAML
```

Y la sección `energia_ha:` de `config/config.yaml` mapeando tus sensores.

Para exponer HA: [Nabu Casa](https://www.nabucasa.com) (6,50 €/mes, cifrado y
sin tocar el router) o un Cloudflare Tunnel (gratis). **No abras el puerto
8123 del router a pelo.**

- **Coste**: VPS + la exposición de HA
- **Superficie de ataque**: la menor de las opciones en la nube. Un solo
  endpoint, autenticado con token, y sin ruta a tu LAN.

### Qué funciona y qué no en esta opción

| Capacidad | Estado |
|---|---|
| Domótica ONNA/KNX (luces, persianas, clima, escenas) | ✅ Igual que siempre, es HA quien habla con el bus |
| Leer energía (solar, batería, red, consumo) | ✅ Vía sensores de HA |
| Ver cámaras y describir la imagen | ✅ Vía el proxy de cámaras de HA |
| Música | ✅ Como `media_player` con `casa_accion` (la integración Bluesound de HA) |
| Controlar la batería | ⚠️ Solo si declaras las entidades de control en `energia_ha:`. Si no, el sistema lo dice claramente en vez de fingir |
| Agrupar zonas de música, presets BluOS | ❌ Necesitan la API local del puerto 11000 |
| Eventos de cámara y detección inteligente | ❌ Necesitan UniFi Protect directo |
| Activar/desactivar wifi, bloquear dispositivos, reiniciar equipos UniFi | ❌ Necesitan el controlador UniFi |
| Escribir en el bus KNX crudo | ❌ Necesita el gateway KNX |

Si eliges esta opción y echas de menos algo de la mitad de abajo, se puede
recuperar: la integración UniFi de HA cubre parte de la red, y para la música
se puede hacer un adaptador que pase por `media_player`. Pero empieza por
comprobar si lo echas de menos de verdad.

---

## Recomendación

**Opción A.** Si Home Assistant ya corre en tu casa, no hay nada que comprar y
no hay nada que exponer, y el sistema queda con todas sus capacidades.

Lo que sí merece la pena hacer en la nube es lo que **ya estás haciendo**:
escribir y probar el código. Eso es lo que se ha hecho aquí. El despliegue es
un `docker compose up -d` cuando decidas la máquina, y la decisión no bloquea
nada porque el código no cambia entre opciones.

Si acabas eligiendo B o C, dilo y se ajusta la configuración: son los mismos
ficheros.

---

## Comprobar que has elegido bien

En la máquina donde vaya a vivir, antes de nada:

```bash
python -m casa_ai.descubrir
```

- Encuentra el inversor, los BluOS y el UniFi → estás en la red correcta,
  vas de opción A.
- No encuentra nada → esa máquina no ve tus aparatos. O la cambias, o te vas a
  la opción B o C.

Y después:

```bash
python -m casa_ai.verificar
```

Te dice, subsistema por subsistema, qué responde de verdad desde ahí. El
`/estado` del bot de Telegram y el `resumen_configuracion` del backend también
indican por qué camino está leyendo la energía en cada momento.
