#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
#
# Traduce el formulario del complemento a las variables de entorno que lee
# `casa_ai.settings.Settings`. Es generico a proposito: cada clave de
# /data/options.json se exporta en mayusculas, asi que anadir una opcion es
# tocar config.yaml y nada mas.

set -euo pipefail

while IFS= read -r clave; do
    valor="$(jq --raw-output --arg k "${clave}" '.[$k]' /data/options.json)"
    # Una opcion vacia no se exporta: Settings tiene su propio valor por
    # defecto y una cadena vacia lo pisaria.
    if [ -n "${valor}" ] && [ "${valor}" != "null" ]; then
        export "${clave^^}=${valor}"
    fi
done < <(jq --raw-output 'keys[]' /data/options.json)

# Lo que el complemento resuelve por su cuenta.
# Home Assistant: por el proxy del Supervisor, con su token. Es la unica
# credencial que el usuario no tiene que crear.
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"
# /data persiste entre actualizaciones; /config es la carpeta del complemento
# que el usuario ve desde el editor de ficheros o Samba.
export DB_PATH="/data/casa_ai.sqlite3"
export CONFIG_PATH="/config/config.yaml"
# Con host_network no hay NAT: el tunel KNX no necesita ruta de vuelta, y el
# API tiene que escuchar en todas las interfaces. Sin API_TOKEN los endpoints
# devuelven 503 a proposito, asi que abrirlo no relaja nada.
export KNX_ROUTE_BACK="false"
export API_HOST="0.0.0.0"
# Escucha en todo para que el Supervisor (ingress) y Home Assistant lleguen,
# pero solo se les atiende a ellos: localhost y la red interna de HA. Ningun
# equipo de la casa ve el API, con o sin token.
export API_CLIENTES="127.0.0.0/8,::1/128,172.30.32.2/32"
# El panel se abre desde la barra lateral de HA: el Supervisor reenvia la
# peticion con el usuario que ha hecho login, y con eso basta.
export API_CONFIAR_EN_INGRESS="true"
export TZ="${ZONA_HORARIA:-Europe/Madrid}"

if [ ! -f /config/config.yaml ]; then
    bashio::log.info "No hay /config/config.yaml: se deja el ejemplo para rellenar."
    /opt/casa/bin/python - <<'PY'
import pathlib, shutil
import casa_ai
ejemplo = pathlib.Path(casa_ai.__file__).parent / "config.example.yaml"
shutil.copy(ejemplo, "/config/config.yaml")
PY
fi

bashio::log.info "Arrancando Casa AI"
exec /opt/casa/bin/casa-ai
