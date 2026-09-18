# Cambios

## 0.1.3

- Musica en los HomePod y Apple TV: se permite `media_player.play_media`, asi
  que Jarvis puede mandar una radio por internet o un archivo a un altavoz de
  Apple, no solo hablar por el. `select_source` para los equipos con entradas.
- Jarvis ya no escribe asteriscos de markdown en Telegram.

## 0.1.2

- Arreglo: en el primer arranque, Jarvis avisaba de que `HA_URL` iba por http
  sin cifrar. Dentro del complemento esa direccion (`http://supervisor/core`)
  es la red interna de Docker, no la de la casa: era un falso positivo. El
  aviso sigue para una direccion http de verdad, y solo menciona mDNS si el
  nombre es `.local`.

## 0.1.1

- Arreglo: todos los mensajes fallaban con «Too many strict tools (21)». La API
  admite 20 herramientas estrictas y el agente marcaba todas. Ahora solo las
  que actuan sobre la casa llevan validacion estricta (13), y hay una valvula
  que impide pasar del limite aunque se anadan mas.
- Jarvis como asistente completo: conversacion, programas y documentos
  entregados como archivo, busqueda web (opcion `busqueda_web`), y ordenes
  para mas tarde («sube la persiana a las 8») por Telegram y WhatsApp.

## 0.1.0

- Primera version del complemento.
