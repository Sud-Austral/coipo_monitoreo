#!/usr/bin/env bash
# Una corrida de chequeos. Es lo que invoca el timer de systemd o el cron.
#
# Se puede correr a mano sin problema: si ya hay otra corrida en curso, esta se omite
# en vez de pisarla.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

if [ -f "$RAIZ/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$RAIZ/.venv/bin/activate"
fi

BLOQUEO="${COIPO_BLOQUEO:-/tmp/coipo_monitoreo.lock}"
exec 9>"$BLOQUEO"
if ! flock -n 9; then
  echo "coipo_monitoreo: ya hay una corrida en curso, se omite esta" >&2
  exit 0
fi

# El codigo de salida de 'chequear' es 1 cuando hay algun dominio caido. Eso es
# informacion, no un fallo del script: si se dejara propagar, systemd marcaria la
# unidad como fallida cada vez que una aplicacion se cae y el estado del timer
# dejaria de significar "el monitoreo esta corriendo".
set +e
python -m coipo_monitoreo chequear "$@"
CODIGO=$?
set -e

if [ "$CODIGO" -ge 2 ]; then
  echo "coipo_monitoreo: error del monitoreo (codigo $CODIGO)" >&2
  exit "$CODIGO"
fi
exit 0
