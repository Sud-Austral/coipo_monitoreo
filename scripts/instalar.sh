#!/usr/bin/env bash
# Instala el monitoreo en un host Linux: entorno virtual, dependencias y (opcional)
# el timer de systemd que lo corre cada 15 minutos.
#
#   ./scripts/instalar.sh              instala el entorno y valida la configuracion
#   ./scripts/instalar.sh --systemd    ademas instala y activa el timer (necesita sudo)
#
# No usa marcadores de posicion: toma las rutas y el usuario reales del sistema donde
# se ejecuta.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USUARIO="$(id -un)"
PYTHON="${PYTHON:-python3}"
cd "$RAIZ"

echo "==> raiz         : $RAIZ"
echo "==> usuario      : $USUARIO"
echo "==> interprete   : $($PYTHON --version)"

echo "==> creando entorno virtual"
[ -d .venv ] || "$PYTHON" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

mkdir -p "$RAIZ/datos"

echo "==> validando configuracion"
python -m coipo_monitoreo probar-config

if [ ! -f "$RAIZ/.env" ] && [ -f "$RAIZ/.env.ejemplo" ]; then
  cp "$RAIZ/.env.ejemplo" "$RAIZ/.env"
  echo "==> se creo .env a partir de .env.ejemplo (editalo si vas a mandar correos)"
fi

echo "==> primera corrida de prueba, sin guardar ni alertar"
python -m coipo_monitoreo chequear --sin-guardar --sin-alertas

if [ "${1:-}" != "--systemd" ]; then
  echo
  echo "Listo. Para dejarlo corriendo cada 15 minutos:"
  echo "    $RAIZ/scripts/instalar.sh --systemd"
  exit 0
fi

echo "==> instalando unidades de systemd"
UNIDADES=/etc/systemd/system

sudo tee "$UNIDADES/coipo-monitoreo.service" >/dev/null <<UNIDAD
[Unit]
Description=Monitoreo de disponibilidad de las aplicaciones web de CONAF
Documentation=file://$RAIZ/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USUARIO
WorkingDirectory=$RAIZ
ExecStart=$RAIZ/scripts/chequear.sh
TimeoutStartSec=300

# El monitoreo solo lee la red y escribe en su propia carpeta.
NoNewPrivileges=true
PrivateTmp=false
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$RAIZ/datos
UNIDAD

sudo tee "$UNIDADES/coipo-monitoreo.timer" >/dev/null <<UNIDAD
[Unit]
Description=Corre el monitoreo de disponibilidad cada 15 minutos

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
# Sin esto, todos los hosts que instalen el timer chequean en el mismo segundo.
RandomizedDelaySec=30
Persistent=true
Unit=coipo-monitoreo.service

[Install]
WantedBy=timers.target
UNIDAD

sudo systemctl daemon-reload
sudo systemctl enable --now coipo-monitoreo.timer

echo
echo "==> timer activo. Para ver cuando corre:"
echo "    systemctl list-timers coipo-monitoreo.timer"
echo "==> para ver la salida de la ultima corrida:"
echo "    journalctl -u coipo-monitoreo.service -n 50"
