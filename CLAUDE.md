# CLAUDE.md — coipo_monitoreo

Guía para agentes. Uso y razones de diseño en [`README.md`](README.md); runbook en [`docs/operacion.md`](docs/operacion.md);
problema y solución en [`insumos/`](insumos/).

## Qué es
Monitoreo de disponibilidad de los dominios web de CONAF. Distingue fallas de DNS, de transporte/proxy, de upstream y de
contenido (p. ej. HTTP 200 con 0 bytes), guarda historial en SQLite y alerta sólo las transiciones confirmadas.
Dos modos: **script** (systemd/cron, recomendado para producción) y **contenedor** (ambiente de test en vm3, con FastAPI y
planificador interno).

## Estructura
- `coipo_monitoreo/`: `cli.py` (comandos), `config.py` + `modelos.py` (YAML validado con pydantic), `chequeos.py` (DNS/TLS/HTTP),
  `runner.py` (una ronda), `alertas.py` (transiciones, amortiguación, guarda anti-tormenta), `notificadores.py`,
  `almacenamiento.py` (SQLite), `reporte.py` (HTML), `web.py` + `planificador.py` (modo contenedor), `demo.py`.
- `config/dominios.yml`: 14 dominios y umbrales de alerta; **se edita sin tocar código**.
- `deploy/`, `scripts/`: crontab, tarea de Windows, vhost, `instalar.sh`, `chequear.sh`. `docs/`: `operacion.md`, `ambiente-test.md`.
- `tests/`: pytest. `.github/workflows/deploy-uat.yml`: despliega al hacer push a la rama `uat`.

## Comandos
```
pip install -r requirements.txt                 # modo script; requirements-web.txt para el contenedor; requirements-dev.txt para pytest
python -m coipo_monitoreo demo                  # demostración local sin red de CONAF
python -m coipo_monitoreo chequear --sin-guardar --sin-alertas
python -m coipo_monitoreo probar-config         # valida config/dominios.yml
python -m pytest -q                             # VERIFICADO: 30 pasan (~105 s en Windows)
```
Códigos de salida de `chequear`: 0 todo en servicio, 1 hay caídos, 2 error de configuración.
No hay linter configurado.

## Convenciones
- Python >= 3.10, español en mensajes y comentarios. Las claves desconocidas del YAML son error, no se ignoran.
- Los `min_bytes` / `texto_esperado` de cada dominio salen de una medición, y la nota del dominio la registra: no inventarlos.
- Probar antes de cambiar alertas: el comportamiento por ronda está fijado en `tests/test_alertas.py` y en `demo`.

## Qué no tocar / reglas
- No hacer que el monitoreo dependa de infraestructura compartida (PostgreSQL de `172.31.2.40`, el mismo proxy): por eso el historial es SQLite local.
- `eventos` (cambios de estado) no se purga nunca; `chequeos` se purga a los 90 días.
- Secretos SMTP/webhook sólo en el `.env` del servidor (`.env.ejemplo` trae los nombres: `SMTP_*`, `ALERTA_DE`, `ALERTA_PARA`, `WEBHOOK_URL`). `datos/` está ignorado por git.
- No instalar en `/opt/apps/` (lo administra el pipeline Docker); el modo script va en `/opt/coipo_monitoreo`.

## Trampas conocidas
- Los tests y el demo son lentos en Windows (conexión rechazada en loopback ~2 s).
- Si los 14 dominios fallan a la vez con `DNS_TIMEOUT`, sospechar primero del resolver interno `172.16.1.120` o de la red del monitoreo, no de las aplicaciones.
- Tres dominios se renombraron el 2026-09-24 (`tv`->`glt`, `nexe`->`n8n`, `documentacion`->`prueba4`); `tv.conaf.cl` sigue respondiendo como alias.
- El `Dockerfile` usa `python:3.12-slim`; `pyproject.toml` pide `>=3.10`.
