# Cambios

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
