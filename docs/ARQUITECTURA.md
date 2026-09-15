# Arquitectura

## Decisiones y por qué

### Home Assistant como frontal de la domótica ONNA

ONNA es un fabricante de pantallas y servidores IP-KNX: la instalación *es* el
bus KNX, no hay una API propietaria en la nube que consultar. Eso deja dos
caminos, y el sistema usa los dos con prioridades distintas:

1. **Home Assistant** con su integración `knx`. Expone las direcciones de
   grupo como entidades normales (`light.`, `cover.`, `climate.`), con nombres
   legibles y estado. Es el camino por defecto.
2. **Bus KNX directo** con `xknx`, para direcciones que Home Assistant no
   exponga. Es riesgo alto por definición: escribe telegramas crudos y una
   dirección equivocada puede accionar cualquier actuador. Solo se permiten
   direcciones declaradas en `config/config.yaml`.

El agente prefiere siempre (1). El prompt del sistema se lo dice explícitamente.

### Dos caminos para la energía y las cámaras

El adaptador de energía es intercambiable: `Sungrow` habla Modbus TCP con el
inversor, y `EnergiaHA` lee los mismos datos de sensores de Home Assistant.
Ambos producen el mismo `EstadoEnergia`. Lo elige `Aplicacion._elegir_energia`
según `ENERGIA_ORIGEN`; en `auto` gana Modbus cuando hay inversor configurado,
porque da más datos y permite controlar la batería.

Existe por dos razones concretas, ninguna teórica:

1. El dongle WiNet-S de Sungrow admite **una sola** conexión Modbus. Si Home
   Assistant ya la tiene, un segundo cliente no entra. Antes había que elegir;
   ahora conviven.
2. Modbus, BluOS y KNX solo funcionan en la LAN. Home Assistant se puede
   alcanzar por un túnel. Si la energía se lee vía HA, el backend puede vivir
   fuera de casa (ver `DESPLIEGUE.md`).

Las cámaras siguen el mismo patrón, y a la misma altura: `FuenteCamaras` en
`adapters/camaras_base.py`, con `CamarasProtect` y `CamarasHA`, elegidas en
`Aplicacion` igual que la fuente de energía. `camara_ver` pide `ctx.camaras` y
no sabe cuál hay. Así la visión —poder preguntar «¿hay alguien en la puerta?» y
que el modelo mire la imagen de verdad— sobrevive al despliegue en la nube.

Que cada camino nombre las cosas a su manera es parte del problema, no un
detalle: una cámara tiene un `id_protect` en UniFi y un `entity_id` en Home
Assistant, y son campos distintos del inventario (`id_protect`, `entidad_ha`),
los dos opcionales. Guardar los dos en uno y distinguirlos por el prefijo era
un sistema colándose en el campo de otro.

Dos consecuencias de diseño que merecen atención:

- **Los campos desconocidos son `None`, no cero.** Una fuente puede no conocer
  la salud de la batería, y `resumen()` omite lo que falta. Devolver `0.0`
  haría que el agente informase de una batería muerta.
- **Una herramienta puede depender de más que "estar configurado".** Leer la
  energía vía HA no implica poder controlarla: hace falta que el usuario
  declare las entidades de control. Eso es una **capacidad**, y la declara el
  propio adaptador (`FuenteEnergia.puede_controlar`) en vez de dejar que cada
  consumidor la adivine con un `getattr` y su propio valor por defecto. Para
  consumirla, `Herramienta.disponible_si`: `energia_modo_bateria` desaparece
  cuando solo se puede leer. Ofrecer una herramienta que siempre falla es peor
  que no tenerla.
- **Los requisitos de adaptador llevan el combinador escrito.** `Cualquiera` y
  `Todos`, no una tupla cuyo significado había que deducir de si más abajo
  había un `disponible_si`. Y con el eje nombrado el registro puede explicar
  una ausencia: `Registro.ausentes(ctx)` dice qué herramienta falta y por qué,
  que es la pregunta de quien está montando la casa.

### Bucle manual en vez del tool runner del SDK

El SDK trae un tool runner que cierra el bucle de llamadas a herramientas solo.
Aquí se usa un bucle manual por una razón concreta: **la confirmación cruza
turnos**.

El usuario dice «fuerza carga de batería». El sistema responde pidiendo
confirmación. El «sí» llega en un mensaje *posterior*, puede que minutos
después, desde el móvil. El tool runner termina cuando no hay más llamadas a
herramientas, así que ese «sí» tiene que entrar como un turno nuevo con todo el
historial detrás. El bucle manual permite eso, además de controlar la
persistencia del historial y la auditoría.

### La confirmación humana se cuenta en turnos, en el código

El mecanismo de confirmación es la defensa central del sistema, así que no
puede depender de que el modelo respete el prompt. El `Store` lleva un contador
de turnos **humanos** por conversación, una acción pendiente registra el turno
en que se propuso, y `tomar_pendiente` la rechaza si el turno actual no es
posterior.

Dos detalles no obvios:

- **No sirve contar mensajes de la tabla `mensajes`.** Los `tool_result` se
  guardan con rol `user`, así que el modelo podría "avanzar de turno" él solo
  simplemente llamando a una herramienta. De ahí una tabla `turnos` aparte, que
  solo incrementa `Agente.responder()`, una vez por entrada humana.
- **Esto importa porque entra texto ajeno en el contexto.** El hostname de
  cualquier equipo que se una al wifi llega al modelo vía `red_clientes`; los
  títulos de una emisora, vía `musica_estado`; los nombres de entidades, vía
  Home Assistant. Sin la comprobación de turno, una inyección ahí bastaba para
  que el modelo propusiera y confirmara una acción física en la misma vuelta
  del bucle. El detalle está en `SEGURIDAD.md`.

### Caché de prompt y orden estable de herramientas

El orden de renderizado de la petición es `tools` → `system` → `messages`. La
caché es una coincidencia de prefijo: cualquier byte que cambie invalida todo
lo que va detrás. De ahí dos decisiones:

- `Registro.disponibles()` devuelve las herramientas **ordenadas
  alfabéticamente**. Si el orden variase entre peticiones, la caché se
  invalidaría en cada mensaje.
- La **hora actual no va en el prompt del sistema**, va en el turno del
  usuario. Si estuviera en el prefijo cacheado, cada mensaje sería un fallo de
  caché garantizado. Hay un test que lo comprueba.

El prompt del sistema se cachea con TTL de 1 hora, porque una conversación por
Telegram puede tener huecos largos entre turnos.

### Signos en Modbus: derivados del estado, no del valor

El firmware de Sungrow no es homogéneo. El registro 13021 (potencia de batería)
unas veces viene firmado y otras como magnitud sin signo. El adaptador:

- respeta el valor si ya viene firmado;
- si viene positivo, deriva el signo de los bits del registro de estado 13000
  (`0x02` cargando, `0x04` descargando).

La potencia de red se expone con la convención «positivo = exportando» y se
puede invertir por configuración, porque también varía entre instalaciones. El
comando `python -m casa_ai.adapters.sungrow` volca los registros crudos para
verificarlo contra la app oficial.

Las direcciones del código están en **base 0** (la que usa pymodbus), no en la
base 1 del documento de protocolo de Sungrow. Por eso el comando de
carga/descarga aparece como 13050 y no 13051: es el mismo registro.

### Visión sobre las cámaras

`camara_ver` no devuelve metadatos: devuelve la captura JPEG como bloque de
imagen dentro del `tool_result`. El modelo la mira de verdad, así que
«¿hay alguien en la puerta?» se responde sobre la imagen real.

### Transcripción local

Las notas de voz se transcriben con faster-whisper **en la propia máquina**.
Dos motivos: las notas de voz de casa no salen de casa, y el sistema sigue
funcionando sin internet para todo lo que no necesite al modelo.

## Flujo de un turno

```
1. Llega un mensaje (Telegram / WhatsApp / HTTP / CLI)
2. El canal comprueba autorización (chat_id o número)
   └─ nota de voz → transcripción local → texto
3. Aplicacion.responder() crea un Contexto etiquetado con canal + usuario
4. El Agente carga el historial de esa conversación desde SQLite
5. Bucle (máx. 12 vueltas):
   a. Petición a la Messages API en streaming
      · system cacheado (1 h) + herramientas en orden estable
      · thinking adaptativo, effort configurable
      · fallbacks de servidor si la cuenta los tiene
   b. ¿stop_reason == refusal? → mensaje al usuario y fin
   c. ¿Hay tool_use? → el Ejecutor los procesa EN PARALELO
      · riesgo alto → no ejecuta: crea pendiente + token
      · resto → ejecuta, captura errores como texto, audita
   d. Todos los tool_result vuelven en UN solo mensaje de usuario
6. Se persiste cada mensaje y se devuelve el texto final
```

El paso 5.d importa: repartir los `tool_result` en varios mensajes le enseña al
modelo a dejar de pedir llamadas en paralelo, y entonces cada consulta múltiple
se vuelve secuencial y lenta.

## Ficheros

```
src/casa_ai/
├── settings.py           Config: secretos por entorno, inventario por YAML
├── store.py              SQLite: auditoría, pendientes, turnos, historial
├── app.py                Contenedor de dependencias
├── main.py               FastAPI + CLI
├── stt.py                Transcripción local de voz
├── adapters/
│   ├── homeassistant.py  REST, lista blanca de servicios, proxy de camaras
│   ├── energia_base.py   EstadoEnergia comun a las dos fuentes
│   ├── modbus.py         Conexion Modbus TCP compartida, decodificado
│   ├── sungrow.py        Inversor hibrido SH: signos, topes de potencia
│   ├── planta.py         Planta por Logger1000: mapa en YAML, sondeo
│   ├── energia_ha.py     La misma energia leida de sensores de HA
│   ├── knx_onna.py       Bus KNX directo (xknx), lista blanca
│   ├── bluos.py          HTTP:11000, XML
│   └── unifi.py          Network + Protect, sesión compartida
├── agent/
│   ├── registry.py       Herramienta = esquema + riesgo + handler
│   ├── safety.py         Clasificación, confirmación, auditoría
│   ├── prompts.py        Prompt del sistema (cacheable)
│   └── orchestrator.py   Bucle del agente
├── tools/                Las herramientas por dominio
├── channels/             Telegram (polling), WhatsApp (webhook)
├── automations/          Rutinas programadas
├── descubrir.py          Busca los aparatos en la red local
└── verificar.py          Comprueba cada subsistema y guia los arreglos
```

## Extender el sistema

**Una herramienta nueva**: un fichero en `tools/`, una `Herramienta` en su
lista `HERRAMIENTAS`, y `construir_registro()` la recoge. Los tests de
`test_registry.py` verifican que tenga descripción suficiente, esquema válido
para `strict: true` y, si es de riesgo alto, resumen de confirmación.

**Un sistema nuevo** (por ejemplo un aspirador o un riego propio): un adaptador
en `adapters/` con una propiedad `configurado` y un método `cerrar()`,
añadirlo al `Contexto`, y declarar `requiere="nombre"` en sus herramientas para
que desaparezcan solas cuando no esté configurado.

**Un canal nuevo** (Siri, Matrix, un panel web): llamar a
`Aplicacion.responder(canal=..., usuario=..., conversacion=..., entrada=...)`.
Todo lo demás —seguridad, auditoría, historial— ya está.
