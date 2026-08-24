# coipo_monitoreo

Monitoreo de disponibilidad de las aplicaciones web de CONAF.

Hoy, cuando una aplicación se cae, nadie se entera hasta que un usuario reclama. En la
revisión manual del 12 de agosto de 2026, **seis de catorce dominios estaban degradados**
sin que nadie lo supiera, alguno posiblemente por semanas.

El problema no es solo detectar la caída. Es que las tres causas posibles **se ven
idénticas desde el navegador** ("no se puede acceder al sitio") y **las resuelven equipos
distintos**. Un monitoreo que responda sí/no no sirve: hay que saber a quién pedirle el
arreglo.

## Los tres niveles, y por qué van separados

| Nivel | Pregunta | Falla típica | Quién lo resuelve |
|---|---|---|---|
| 1 · DNS | ¿el nombre resuelve? | `NXDOMAIN` | Informática |
| 2 · Transporte | ¿el proxy acepta TCP+TLS? | conexión rechazada, timeout | Administrador del proxy |
| 2b · Upstream | ¿llega a contestar HTTP? | corta sin responder | Proxy + equipo de la aplicación |
| 3 · Contenido | ¿devuelve algo real? | **HTTP 200 con 0 bytes** | Equipo de la aplicación |

El nivel 3 es el que más se pasa por alto. Cuando un dominio llega al proxy pero no tiene
`server` propio, nginx contesta con el catch-all: **HTTP 200 y cuerpo vacío**. Un chequeo
que solo mire el código HTTP da ese caso por bueno. Por eso acá se mira el tamaño del
cuerpo y, cuando se puede, un texto que deba aparecer.

## Por qué es un script y no una aplicación web

**El monitoreo no puede compartir destino con lo que monitorea.** Una aplicación web para
esto viviría detrás del mismo proxy `172.31.2.100` que hoy es la causa de cuatro de las
seis fallas, y necesitaría un nombre nuevo en el DNS, que es el otro sistema averiado. El
tablero se caería justo cuando hace falta.

Además, una aplicación web necesita —**antes del primer chequeo**— un APP_PORT del
registro institucional, `/opt/apps/...` con dueño `deploy`, un `.env` creado a mano en el
servidor, un nombre DNS, un vhost en el proxy y pasar el smoke test de `/health`. Son seis
dependencias externas, dos de ellas en los sistemas rotos. Este script no tiene ninguna:
corre hoy.

Tampoco usa el PostgreSQL compartido de `172.31.2.40`, por el mismo motivo: el monitoreo
no debe depender de infraestructura compartida que podría ser justamente lo caído. El
historial va en SQLite local.

Lo que se pierde —un tablero consultable por terceros— se compensa con `estado`,
`historial` y un **reporte HTML de un solo archivo** que se abre con doble clic o se
adjunta a un correo, sin puerto ni despliegue.

## Dos modos de ejecución

| | **Script** (recomendado para producción) | **Contenedor** (ambiente de test) |
|---|---|---|
| Quién lo despierta | systemd o cron, cada 15 min | un planificador dentro del propio proceso |
| Puerto | ninguno | `APP_PORT`, solo en loopback |
| Historial | `datos/monitoreo.db` | volumen `coipo_monitoreo_datos` |
| Dependencias | Python y 6 librerías | además Docker, FastAPI y uvicorn |
| Se instala con | `./scripts/instalar.sh --systemd` | `git push origin uat` |

El modo contenedor existe porque el pipeline de CONAF es el que es, y para poder validar
en vm3 antes de tocar producción. Trae de yapa una interfaz web (`/`, `/estado`,
`/eventos`) sobre la misma base. Pero **para producción sigo recomendando el modo script**:
dentro de Docker, el monitoreo pasa a depender del demonio Docker, que es una de las cosas
que tumban aplicaciones — si Docker se cae, se caen las apps *y* el aviso.

Despliegue al ambiente de test, paso a paso: **[docs/ambiente-test.md](docs/ambiente-test.md)**.

---

## Verificarlo funcionando, en tres comandos

No hace falta leer el código para saber si sirve.

```bash
pip install -r requirements.txt

# 1. Demostración autocontenida: levanta un servidor HTTPS falso en 127.0.0.1 que
#    reproduce las fallas reales y corre 8 rondas. No toca la red de CONAF.
python -m coipo_monitoreo demo

# 2. Chequeo real contra los 14 dominios, sin guardar ni alertar.
python -m coipo_monitoreo chequear --sin-guardar --sin-alertas

# 3. Pruebas automatizadas.
python -m pytest -q
```

El demo es lo que más conviene mirar: muestra las ocho rondas y **en cuáles manda alerta y
en cuáles no**, que es el comportamiento que no se ve en una sola corrida.

```
Ronda 1  primera corrida            -> avisa el inventario de lo que ya estaba roto
Ronda 2  nada cambió                -> SILENCIO
Ronda 3  se cae un dominio          -> silencio: una sola observación no confirma
Ronda 4  se confirma                -> UNA alerta, con nivel, responsable y desde cuándo
Ronda 5  se repara                  -> silencio
Ronda 6  se confirma la mejora      -> UNA alerta de recuperación
Ronda 7  se cae el DNS entero       -> silencio
Ronda 8  ocho dominios fallan igual -> UNA sola alerta agregada, no ocho
```

---

## Instalación en el servidor

Recomendado: la VM de aplicación `172.31.2.41`, o cualquier host Linux que esté siempre
encendido y alcance `172.31.2.100` y el DNS interno `172.16.1.120`.

```bash
RAIZ=/opt/coipo_monitoreo
sudo mkdir -p "$RAIZ"
sudo chown "$(id -un)": "$RAIZ"
git clone https://github.com/Sud-Austral/coipo_monitoreo.git "$RAIZ"
cd "$RAIZ"
./scripts/instalar.sh --systemd
```

`instalar.sh` crea el entorno virtual, instala las dependencias, valida la configuración,
hace una corrida de prueba y deja el timer de systemd corriendo cada 15 minutos. No usa
rutas ni usuarios inventados: los toma del sistema donde se ejecuta.

Comprobar que quedó andando:

```bash
systemctl list-timers coipo-monitoreo.timer
journalctl -u coipo-monitoreo.service -n 50
cd /opt/coipo_monitoreo && .venv/bin/python -m coipo_monitoreo estado
```

Se instala en `/opt/coipo_monitoreo` y **no** en `/opt/apps/`, a propósito: `/opt/apps/`
lo administra el pipeline de despliegue de Docker, que espera un `.env` previo, un puerto
publicado y un `/health`. Este servicio no es una aplicación desplegada y no debe quedar
bajo esa convención.

Si el host no usa systemd, está `deploy/crontab.ejemplo`. Para arrancar hoy mismo desde
una estación Windows mientras se gestiona el acceso a la VM, está
`deploy/tarea-windows.ps1` — sirve como puente, no como destino: una estación se apaga y
se lleva el monitoreo con ella.

### Primera corrida en la VM: qué comprobar

La VM usa el DNS **público**, así que por nombre los dominios resuelven a `200.29.173.42`,
que desde adentro no se alcanza. El monitoreo no depende de eso: consulta el resolver
interno `172.16.1.120` de forma explícita, y llega al proxy **por IP mandando la cabecera
`Host` y el SNI**. Verificado: da el mismo resultado byte a byte que el acceso por nombre.

Lo único que hay que confirmar en la primera corrida es que la VM alcance el resolver
interno. Si no lo alcanza, los catorce dominios darán `DNS_TIMEOUT` a la vez y saltará la
guarda anti-tormenta con una sola alerta que dice justamente eso: revisar primero el
monitoreo y la red, no las aplicaciones.

---

## Configuración

Todo en [config/dominios.yml](config/dominios.yml). Agregar un dominio es editar el
archivo; no se toca código ni se vuelve a desplegar nada.

```yaml
dominios:
  - nombre: iam.conaf.cl
    equipo: IAM                       # dueño de la aplicación, para saber a quién llamar
    texto_esperado: "COIPO IAM"       # tiene que aparecer en la respuesta
    notas: "medido 2026-08-12: HTTP 200, 749 bytes"

  - nombre: academia.conaf.cl
    estados_ok: [200, 303]            # redirige al login: el 303 es correcto acá
```

Lo que puede declarar cada dominio: `min_bytes`, `estados_ok`, `texto_esperado`, `ruta`,
`puerto`, `timeout_s`, `ips_esperadas`, `resolver`, `resolvers_extra`, `via`, `ip_proxy`,
`equipo`, `habilitado`, `notas`. Lo que no se declara sale de `predeterminados`.

Los valores de `min_bytes` y `texto_esperado` **no son inventados**: salen de medir cada
dominio el 12 de agosto de 2026, y el tamaño real quedó anotado en `notas`.

Después de editar:

```bash
python -m coipo_monitoreo probar-config
```

Una clave mal escrita es un error, no algo que se ignora en silencio: si `min_bytes` se
escribiera `minimo_bytes`, el chequeo correría con el valor por defecto y nadie se
enteraría.

### Alertas

El canal `log` está siempre activo y no necesita configuración. Para correo, copiar
`.env.ejemplo` a `.env` en el servidor, completar las variables SMTP y agregar `email` a
`alertas.canales`. Probarlo sin esperar a que algo se caiga:

```bash
python -m coipo_monitoreo probar-alerta
```

Tres mecanismos evitan que el monitoreo se vuelva ruido de fondo:

1. **Solo transiciones.** "Sigue caído" no genera nada.
2. **Amortiguación de rebote.** Un cambio necesita 2 corridas consecutivas (30 min) para
   darse por cierto, así que un timeout aislado no despierta a nadie.
3. **Guarda anti-tormenta.** Si el 60% de los dominios falla en el mismo nivel a la vez,
   sale una sola alerta agregada: eso no son catorce incidentes, es la red o el propio
   monitoreo.

Y cada alerta dice **quién lo arregla**, según el nivel donde falló y no según el dominio:
si `iam.conaf.cl` deja de resolver, el arreglo es de Informática aunque el dueño de la
aplicación sea otro equipo.

---

## Comandos

```bash
python -m coipo_monitoreo chequear      # una ronda: mide, guarda y alerta lo que cambió
python -m coipo_monitoreo estado        # estado vigente de cada dominio y desde cuándo
python -m coipo_monitoreo historial --dominio archivo.conaf.cl --dias 30
python -m coipo_monitoreo reporte --salida datos/reporte.html
python -m coipo_monitoreo probar-config
python -m coipo_monitoreo probar-alerta
python -m coipo_monitoreo demo
```

Opciones útiles de `chequear`: `--dominio` (uno solo, repetible), `--sin-guardar`,
`--sin-alertas`, `--json`.

Códigos de salida: `0` todo en servicio · `1` hay al menos un dominio caído · `2` error de
configuración o del propio monitoreo.

## El historial

En SQLite (`datos/monitoreo.db`), tres tablas para tres preguntas distintas:

- `chequeos` — la evidencia cruda de cada corrida. Se purga a los 90 días.
- `estado_actual` — cómo está cada dominio **y desde cuándo**.
- `eventos` — solo los cambios de estado. **No se borra nunca.**

`eventos` es la razón de ser del historial. Sin fecha, un reclamo a Informática es "yo me
acuerdo que antes andaba", que no discute con nadie. Con fecha es "archivo.conaf.cl dejó
de resolver el martes a las 14:00".

```bash
sqlite3 datos/monitoreo.db "select ts, dominio, anterior, nuevo from eventos order by ts desc limit 20"
```

## Runbook

Qué hacer con cada alerta y a quién escalarla: [docs/operacion.md](docs/operacion.md).

---

## Estado medido el 12 de agosto de 2026

| Dominio | Estado | Nivel |
|---|---|---|
| prensa, dendroenergia, tv, nexe, reserva-bienestar, iam, usuario | en servicio | — |
| academia | en servicio (303 al login) | — |
| chat-normativa, evaluacion-prioritaria, interno-saff, documentacion | **caído** | upstream |
| interno-sidco | **caído** (200 con 0 bytes) | contenido |
| archivo | **caído** (NXDOMAIN) | dns |

`archivo.conaf.cl` tiene **dos problemas independientes**: no resuelve en el DNS *y*
tampoco tiene vhost en el proxy — consultado por IP con `Host` devuelve el catch-all de 0
bytes. Si Informática restaura el registro DNS, el sitio va a seguir apareciendo en
blanco. Hay que pedir las dos cosas. Por eso el monitoreo sigue midiendo todas las capas
aunque la primera ya haya fallado, y reporta los hallazgos de las demás.

## Notas

- Requiere Python 3.10 o superior. Sin instalación: `python -m coipo_monitoreo` funciona
  desde la carpeta del repositorio con las dependencias puestas.
- En Windows, una conexión rechazada en loopback tarda ~2 s por el stack TCP del sistema
  operativo; por eso el demo y las pruebas se ven más lentos ahí que en Linux.
