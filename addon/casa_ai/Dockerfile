ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM $BUILD_FROM

# El codigo se instala desde el propio repositorio en GitHub. Asi el
# complemento no lleva una copia del fuente que pueda quedarse atras del
# resto del repo: la version de config.yaml es la etiqueta git que se instala.
ARG BUILD_VERSION
ARG CASA_AI_REPO=https://github.com/josuepascual-pixel/casa-ai

ENV LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip git ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Primero la etiqueta de la version; si aun no existe (recien fusionado y sin
# publicar la release), la rama principal, que es lo mismo hasta que haya
# una version siguiente.
RUN python3 -m venv /opt/casa \
    && /opt/casa/bin/pip install --upgrade pip \
    && ( /opt/casa/bin/pip install \
            "casa-ai[voz] @ git+${CASA_AI_REPO}@v${BUILD_VERSION}" \
         || /opt/casa/bin/pip install \
            "casa-ai[voz] @ git+${CASA_AI_REPO}@main" )

COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
