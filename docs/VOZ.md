# Hablarle a Jarvis por voz

Tres caminos, porque en casa hay tres situaciones distintas. En los tres, la
identidad es el canal (el chat, el número o el aparato), nunca la voz: una
voz se clona en minutos, un aparato en un cuarto no.

## 1. Los adultos, desde el iPhone: nota de voz por Telegram

Ya funciona. Se manda una nota de voz al bot, se transcribe en casa (sin salir
a ninguna nube) y Jarvis responde por texto. Las acciones con consecuencias
salen con dos botones, y el botón solo lo puede pulsar quien tiene ese chat.

## 2. Los adultos, con «Oye Siri»: un atajo que habla con Jarvis

Siri no puede pasarle una frase a otro asistente por sí sola, pero un Atajo
sí. Se crea una vez en el iPhone (Atajos → + → nombre «Jarvis»):

1. **Pedir entrada** (texto, pregunta «¿Qué le digo a Jarvis?»). Con Siri
   se responde hablando, y esta acción funciona también en el HomePod;
   «Dictar texto» solo en el iPhone.
2. **Obtener contenido de URL**:
   `http://<ip-de-home-assistant>:8123/api/conversation/process`, método
   POST, cabecera `Authorization: Bearer <token de Home Assistant de esa
   persona>`, cuerpo JSON
   `{"text": <Texto dictado>, "agent_id": "conversation.casa_ai", "language": "es"}`.
3. **Obtener valor de diccionario** `response` → `speech` → `plain` → `speech`.
4. **Hablar texto**.

El token de Home Assistant se crea en el perfil de cada uno (Ajustes →
Perfil → Seguridad → Tokens de acceso de larga duración): así cada persona
entra con su propia identidad (`usuario-<id>` en `personas:`), no con un
token compartido, y el backend no tiene que estar abierto a la red: la
frase entra por Home Assistant, que es quien la autentica.

Fuera de casa hace falta llegar al backend: VPN de UniFi, o el acceso remoto
de Home Assistant (Nabu Casa) con el complemento expuesto por ingress.

**El mismo atajo en los HomePod.** Con las *peticiones personales* activadas
en la app Casa (HomePod → ajustes → Reconocer mi voz y Peticiones personales),
«Oye Siri, Jarvis» en la cocina lanza el atajo de quien habla, en su iPhone,
con su token: la identidad sigue siendo la persona, y el HomePod solo lo hace
para las voces que tiene registradas. Son dos pasos («Oye Siri, Jarvis» y
la frase); un satélite de Assist lo hace en uno, y es la única diferencia.

## 3. Leo, desde su cuarto: un satélite de voz sin teléfono (opcional)

Con HomePods en casa este paso no hace falta para los adultos; es para que
un niño sin teléfono, sin Telegram y sin token pueda hablarle desde su
cuarto. Mientras no haya satélite, Leo tiene el panel en su tablet (con lo
que un niño puede ver). Si se quiere, el aparato en su habitación escucha
una palabra de activación (de serie trae «Hey Jarvis»), y ese aparato es su
identidad:

- **Aparato**: un *Home Assistant Voice Preview Edition* (unos 60 €), o un
  ESP32-S3 con micrófono. Se conecta al WiFi de casa y aparece en Home
  Assistant como un satélite de Assist.
- **Pipeline de Assist** en Home Assistant: voz a texto con el complemento
  Whisper (local), agente de conversación **Casa AI**, texto a voz con la
  integración de ElevenLabs y la voz de Jarvis (ver abajo).
- **La integración Casa AI** (carpeta `custom_components/casa_ai`
  de este repositorio; en HACS: Integraciones → menú → Repositorios
  personalizados → esta URL, tipo «Integration»; o copiando la carpeta a
  `config/custom_components/` de Home Assistant). Se le da la URL del
  backend y el token del API, y manda cada frase a `/voz` con una identidad:
  el usuario de Home Assistant que habló si lo hay (cualquier cuenta de HA
  puede inventarse un `device_id` por la API, así que el aparato solo cuenta
  cuando no hay usuario detrás, como en un satélite cuyo pipeline lanza el
  propio HA).
- **La persona** se declara en `config.yaml`:

  ```yaml
  personas:
    - nombre: Leo
      nivel: nino
      dispositivos: ["<device_id del satélite en Home Assistant>"]
  ```

  El `device_id` sale en Ajustes → Dispositivos → el satélite → la URL termina
  en él. Con eso, lo que se diga a ese aparato solo puede encender luces,
  poner música, mover persianas y preguntar cuánto sol hay. Nada de cámaras,
  energía, red ni acciones con consecuencias, y el prompt le dice a Jarvis
  que lo tutee y que lo que no pueda se lo pida a sus padres.

Un adulto que hable a Jarvis desde la app de Home Assistant o desde el atajo
de Siri se declara con su usuario de Home Assistant: `dispositivos:
["usuario-<id de usuario de HA>"]` (el id sale en Ajustes → Personas →
Usuarios). Un satélite en la cocina se declara con su `device_id`. En los
dos casos las confirmaciones de riesgo se hacen de palabra en la frase
siguiente («sí»), y quien no esté declarado es niño.

## La voz de Jarvis

No se clona la de ningún actor. Se diseña con Voice Design de ElevenLabs a
partir de una descripción (`python -m casa_ai.voz disenar`, ver README), se
elige una de las tres candidatas y se guarda como voz «Jarvis». Su `voice_id`
se usa en dos sitios:

- en el pipeline de Assist, con la integración **ElevenLabs** de Home
  Assistant, para los satélites;
- en las notificaciones habladas por los HomePods y Apple TV: Home Assistant
  los ve por la integración **Apple TV** (AirPlay), y `tts.speak` con esa voz
  sale por el altavoz que se le diga.

Con eso, dos cosas hablan solas:

- **La herramienta `voz_hablar`**: «avisa en la cocina de que la lavadora ha
  terminado» sale por el HomePod de la cocina con la voz de Jarvis. Necesita
  `TTS_ENTIDAD` (la entidad de texto a voz de Home Assistant) y un alias del
  altavoz en `alias_entidades` («cocina: media_player.homepod_cocina»).
- **El informe de la mañana**, si `RUTINAS_ALTAVOZ` apunta a un altavoz: a
  las 8 se lee en voz alta además de llegar por Telegram. Solo el informe;
  los avisos de vigilancia no despiertan a nadie.

Un HomePod escucha para Siri, y por Siri (el atajo del punto 2, con
peticiones personales) llega a Jarvis; y habla por él con `tts.speak`.
Con los HomePod y los iPhone queda cubierta toda la casa sin comprar nada.
