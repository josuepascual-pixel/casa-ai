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

1. **Dictar texto** (idioma español).
2. **Obtener contenido de URL**: `http://<ip-de-home-assistant>:8099/chat`,
   método POST, cabecera `Authorization: Bearer <API_TOKEN>`, cuerpo JSON
   `{"mensaje": <Texto dictado>, "hilo": "siri-laura"}`.
3. **Obtener valor de diccionario** `respuesta`.
4. **Hablar texto**.

Desde entonces: «Oye Siri, Jarvis» → dicta → responde en voz alta. Cada
persona pone su propio `hilo` para que las conversaciones no se mezclen. Este
camino cuenta como dueño (usa el token del API), así que es solo para los
adultos y el token no se comparte.

Fuera de casa hace falta llegar al backend: VPN de UniFi, o el acceso remoto
de Home Assistant (Nabu Casa) con el complemento expuesto por ingress.

## 3. Leo, desde su cuarto: un satélite de voz sin teléfono

Un niño de ocho años no tiene Telegram ni token. Tiene un aparato en su
habitación que escucha una palabra de activación, y ese aparato es su
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
  backend y el token del API, y manda cada frase a `/voz` con el identificador
  del satélite que la oyó.
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

Un satélite en la cocina para los adultos se declara igual, con su
`device_id` bajo la persona que corresponda, y entonces las confirmaciones
de riesgo se hacen de palabra en la conversación siguiente («sí, hazlo»).

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

Un HomePod no puede escuchar para Jarvis (solo escucha para Siri); sí puede
hablar por él. Por eso escuchar es cosa del iPhone o del satélite, y hablar,
de los HomePods.
