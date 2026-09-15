FROM python:3.12-slim

# La transcripcion de voz se instala solo si se pide: son cientos de MB entre
# ctranslate2 y ffmpeg, y en un equipo pequeno se nota.
#   docker build --build-arg CON_VOZ=false .   -> imagen ligera, sin notas de voz
ARG CON_VOZ=true

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN if [ "$CON_VOZ" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ffmpeg \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir ".[voz]"; \
    else \
        pip install --no-cache-dir .; \
    fi

RUN mkdir -p /app/data /app/config
VOLUME ["/app/data", "/app/config"]

EXPOSE 8099

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8099/salud', timeout=3).status==200 else 1)"

CMD ["casa-ai"]
