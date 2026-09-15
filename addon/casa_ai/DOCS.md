# Casa AI como complemento de Home Assistant

El agente corre dentro de Home Assistant, al lado de las integraciones, en la
misma maquina. No hay que instalar Docker ni editar ficheros: las claves se
rellenan en un formulario y Home Assistant le da al agente acceso a si mismo
sin que tengas que crear ningun token.

## Instalar

1. **Ajustes → Complementos → Tienda de complementos.**
2. Arriba a la derecha, el menu de tres puntos → **Repositorios**.
3. Pega `https://github.com/josuepascual-pixel/casa-ai` y **Anadir**.
4. Cierra, actualiza la tienda si hace falta, y busca **Casa AI**. **Instalar.**
   Tarda unos minutos: construye la imagen en tu maquina.
5. Pestana **Configuracion**: rellena, como minimo, la clave de Anthropic, el
   token de Telegram, tu chat de Telegram y un token para el API (una
   contrasena larga que te inventes).
6. **Iniciar.** En la pestana **Registro** ves arrancar cada subsistema.

## Lo que el complemento resuelve solo

- **Home Assistant.** No hay que crear un token de larga duracion: el
  complemento habla con Home Assistant por el proxy del Supervisor.
- **Datos.** El historial de conversaciones y la auditoria estan en `/data`,
  que sobrevive a las actualizaciones y entra en las copias de seguridad.
- **Red.** Va en la red de la maquina, sin NAT. El bus KNX, el Modbus del
  registrador Sungrow y los reproductores BluOS se alcanzan directamente.
- **Panel.** En la barra lateral de Home Assistant («Jarvis»), tras el login
  de HA. El puerto del API no se atiende desde la red de casa: solo desde
  Home Assistant.

## El inventario de la casa

Los aparatos, las camaras, los reproductores y las direcciones KNX se declaran
en `config.yaml` dentro de la carpeta del complemento (`/addon_configs/…/casa_ai`
si entras por Samba o por el editor de ficheros). La primera vez se deja un
ejemplo comentado; edita y reinicia el complemento, o llama a
`POST /recargar-inventario`. Ahi tambien va el nombre del asistente (`Jarvis`
si no dices otra cosa) y si habla de usted o de tu.

## La planta fotovoltaica

Si tienes un inversor hibrido, basta la IP del registrador. Si tienes una
planta con Logger1000 (inversor de cadena, bateria aparte y contador),
declara la seccion `planta:` en `config.yaml` como explica el ejemplo, y si
el router no deja reservar la IP del Logger, deja la IP vacia: el agente
prueba por Modbus los equipos Sungrow que UniFi ve y se queda con el
registrador, y lo vuelve a buscar si cambia de IP.

## Puesta en marcha desde el movil

El complemento no tiene terminal, y no hace falta: en Telegram, `/verificar`
lee de verdad cada subsistema y dice que falla y como arreglarlo, y
`/descubrir` barre la red y escribe los bloques del inventario para pegar, y
`/sondear` explora la planta Sungrow tras el Logger1000. Lo mismo por el
API: `GET /verificar`, `POST /descubrir` y `POST /planta/sondear`.

## Chats autorizados de Telegram

El bot no atiende a nadie hasta que pongas tu `chat_id`. Para saberlo: crea el
bot en @BotFather, pon el token, inicia el complemento, escribele cualquier cosa
al bot, y mira el **Registro**: dice «Mensaje rechazado … Tu chat_id es NNN».
Ponlo en la configuracion y reinicia.

## Notas de voz

La transcripcion se hace en la propia maquina, sin salir de casa. El modelo
`small` tarda unos segundos por nota en un mini PC; `medium` entiende mejor y
tarda el doble. La primera nota descarga el modelo (unos cientos de MB).

## Actualizar

Las actualizaciones aparecen en la tienda como en cualquier complemento. Cada
version instala la etiqueta git correspondiente del repositorio (`v0.1.0`),
y si esa etiqueta aun no se ha publicado, la rama principal. Para publicarla:
en GitHub, Releases → Draft a new release → etiqueta `v<version>`.
