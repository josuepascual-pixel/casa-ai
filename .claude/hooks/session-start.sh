#!/bin/bash
# Prepara el entorno para que los tests y el linter funcionen en una sesion
# de Claude Code en la web, donde el contenedor arranca limpio.
set -euo pipefail

# En local no hace falta: cada uno tiene su propio venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Sin el extra `voz`: son cientos de MB entre ctranslate2 y los modelos, y
# ningun test lo necesita (de hecho hay uno que comprueba que el nucleo no lo
# arrastra). Si hace falta trabajar en la transcripcion:
#   pip install -e ".[voz]"
# En imagenes con paquetes gestionados por el sistema (Debian), pip no puede
# desinstalar para actualizar y aborta con "RECORD file not found". El
# reintento con --ignore-installed instala copias propias por delante sin
# tocar las del sistema.
pip install --quiet -e ".[dev]" \
  || pip install --quiet -e ".[dev]" --ignore-installed

# xknx importa `cryptography`, y en algunas imagenes el paquete del sistema
# choca con el de pip y falla por `_cffi_backend`. Reinstalar cffi por pip lo
# arregla. No es critico: si falla, el resto del entorno sigue sirviendo.
pip install --quiet --upgrade cffi || true

# Layout src sin instalar en modo editable para los tests.
echo 'export PYTHONPATH="src"' >> "${CLAUDE_ENV_FILE:-/dev/null}"

echo "Entorno listo. Tests: python -m pytest tests -q | Lint: ruff check src tests"
