# La red: lo que Jarvis no puede proteger

Jarvis decide quién puede pedirle qué. Pero el inversor, la batería, el bus
KNX de ONNA, las cámaras y los reproductores están en la red de casa y
**ninguno pide contraseña**: Modbus TCP y KNX/IP se diseñaron para redes
industriales cerradas. Cualquier equipo conectado a la misma red puede
subir una persiana o cambiar la batería hablando directamente con el
router KNX o con el Logger, sin pasar por Jarvis. Por eso «nadie puede hacer
nada en mi casa» empieza por la red, no por el software.

Esto es lo que hay que tener, en orden de importancia.

## 1. Quién entra en la red

- **WiFi con WPA3** (o WPA2/WPA3 mixto si algún aparato antiguo no puede) y
  una contraseña larga que no se dé a nadie. En UniFi: WiFi → tu red →
  Seguridad.
- **Red de invitados separada** para visitas, con «aislamiento de clientes»
  activado: ven internet y nada más. Toda visita, a la de invitados.
- **Los aparatos de la casa en su propia red (VLAN «IoT»)**: cámaras,
  Logger1000, router KNX, BluOS, TVs, enchufes, la descalcificadora. Sin
  salida a internet salvo los que la necesiten (iSolarCloud, HydroLink), y
  **sin acceso desde la red de invitados ni desde la WiFi normal**: solo el
  mini PC de Home Assistant habla con ellos. Esto es lo que hace que un
  portátil de un visitante, o un móvil infectado, no pueda hablar KNX.

  Hoy tu UniFi no tiene gateway propio: el router es el de Vodafone, y sin
  gateway UniFi no puede poner reglas entre redes. **Recomendación: un UniFi
  Cloud Gateway** (Ultra o Max, entre 120 y 250 €) detrás del router de
  Vodafone. Da las VLAN con cortafuegos entre ellas, VPN para entrar desde
  fuera, y deja de depender de que Vodafone tenga bien configurado el suyo.
  Sin él, el aislamiento entre redes no es real.
- **WPS desactivado** y **UPnP desactivado** en el router de Vodafone (pide
  a Vodafone que lo confirme: es su router). UPnP deja que cualquier
  aparato abra puertos hacia internet por su cuenta.
- **Ningún puerto abierto desde internet**. Ni para Home Assistant, ni
  para las cámaras, ni para el panel. Para entrar desde fuera: la VPN del
  gateway UniFi o el acceso remoto de Home Assistant (Nabu Casa), que no
  abre puertos.

**Cómo comprobarlo**: con Jarvis en marcha, `/verificar` en Telegram lee
la configuración de UniFi y dice qué queda abierto: puertos hacia internet,
UPnP, wifi sin contraseña o con WPA antiguo, invitados sin aislar, una sola
red para todo, o falta de gateway. Cuando todo esté cerrado lo dirá con un
✅ en «Seguridad de la red».

## 2. Las cuentas

- **UniFi**: cuenta de administrador con segundo factor activado. Para
  Jarvis, un usuario **local** de solo lectura (Network) y visualización
  (Protect), nunca la cuenta de administrador.
- **Home Assistant**: una cuenta por persona, cada una con segundo factor
  (Ajustes → Perfil → Autenticación en dos pasos). La de Eros sin permisos
  de administrador. Nadie más tiene cuenta.
- **Telegram**: el bot solo atiende a los `chat_id` de la lista, y cualquier
  desconocido que le escriba te llega como aviso. Activa el código de
  bloqueo de Telegram en el iPhone de cada uno (Ajustes de Telegram →
  Privacidad → Código de acceso).
- **Los móviles**: son la llave. Face ID, y si uno se pierde, `/bloquear`
  desde el otro deja la casa en solo lectura hasta que lo recuperes o
  cambies el token del bot en @BotFather (`/revoke`).

## 3. Lo que hace Jarvis por su parte

- Ninguna acción con consecuencias sin que un humano la confirme con un
  botón o con un «sí» que reconoce el código, no el modelo.
- El API del backend no existe para la red de casa: el complemento lo
  limita a Home Assistant, y el panel se abre desde dentro de Home Assistant
  tras su login.
- Eros solo ve luces, música y persianas, nunca cámaras ni puertas; el
  garaje y el portón son riesgo alto para todos.
- Cada acción ejecutada queda en la auditoría con quién la pidió y por dónde.
- `/verificar` y `/estado` te dicen qué está expuesto: si el sistema ve una
  configuración insegura, lo avisa.

## 4. Lo que sale de casa

Solo tres cosas hablan con internet: las conversaciones con Claude (el
modelo), la voz con ElevenLabs (el texto que se va a decir) y, si se usa,
WhatsApp con Meta. Ni las cámaras, ni las claves de la casa, ni la auditoría
salen. Las claves viven en el formulario del complemento, dentro de tu Home
Assistant.
