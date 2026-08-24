# Desplegar en el ambiente de test (vm3 · 172.31.2.42)

Sigue la convención de `COIPO_DOCUMENTO/ambiente_test`: **la rama decide el servidor**.
Un push a `uat` despliega en vm3 a través del runner con etiqueta `conaf-uat`.

## Lo que cambia respecto de las otras apps

| | Las demás apps | coipo_monitoreo |
|---|---|---|
| Paso 1 de la guía 05 (rol y base en PostgreSQL) | obligatorio | **no aplica** — el historial va en SQLite dentro de un volumen |
| Servicios en `docker-compose.yml` | `app` (nginx+build) + `backend` | solo `backend`: no hay frontend que construir |
| Dónde viven los datos | volumen o base compartida | volumen nombrado `coipo_monitoreo_datos` |
| `/health` | salud de la app | **salud del monitoreo, no del parque** — ver abajo |

### Por qué `/health` devuelve 200 aunque haya dominios caídos

Es deliberado y hay que entenderlo antes de "arreglarlo": `/health` informa si el
**monitoreo** está vivo. Si devolviera error cuando una aplicación se cae, el smoke test
del despliegue fallaría justo cuando el monitoreo está haciendo bien su trabajo, y el
healthcheck de Docker reiniciaría el contenedor en bucle.

Para saber si el parque está sano están `/estado` (JSON) y `/` (el reporte). Y para saber
si el monitoreo está al día está `/listo`, que sí devuelve **503** cuando hace demasiado
que no completa una corrida.

### El detalle que habría borrado el historial

El rsync del pipeline corre con `--delete` y solo preserva `.env` y **`data/`** — en
inglés. Este repositorio usa `datos/`. Una base guardada en
`/opt/apps/coipo_monitoreo/datos/` se habría borrado en **cada despliegue**, y la
pregunta "¿desde cuándo está caído?" habría quedado sin respuesta justo cuando importa.

Por eso el `docker-compose.yml` usa un **volumen nombrado**, que sobrevive al rsync, al
`--build` y al redespliegue.

---

## Paso 1 · Puerto

El `APP_PORT` lo asigna el **registro institucional**. No se deriva ni se inventa: hay que
consultarlo. En vm3 hoy solo está ocupado el 8117.

Cuando lo tengas asignado, comprobalo libre en el servidor:

```bash
PUERTO=
[ -n "$PUERTO" ] || echo "FALTA: asignar PUERTO desde el registro institucional"
ss -tlnp | grep ":$PUERTO " && echo "OCUPADO, pedir otro" || echo "libre en vm3"
```

## Paso 2 · Preparar vm3

El pipeline **falla en el segundo paso** si `/opt/apps/coipo_monitoreo/.env` no existe.
Hay que crearlo antes del primer push.

```bash
APP=/opt/apps/coipo_monitoreo
PUERTO=

install -d -o deploy -g deploy -m 755 "$APP"
umask 177
cat > "$APP/.env" <<EOF
APP_PORT=$PUERTO
COIPO_INTERVALO_MIN=15
TZ=America/Santiago
ALERTA_DE=monitoreo-uat@conaf.cl
ALERTA_PARA=
EOF
chown deploy:deploy "$APP/.env"
chmod 600 "$APP/.env"
ls -l "$APP/.env"
```

No hay variables de base de datos y eso es correcto: este servicio no usa el PostgreSQL
de 172.31.2.40. La plantilla completa está en [../deploy/env-uat.ejemplo](../deploy/env-uat.ejemplo).

## Paso 3 · Comprobar el alcance de red desde vm3

**Este paso es el que decide si el monitoreo sirve desde ahí.** vm3 tiene que alcanzar el
proxy y el DNS interno; si no, los 14 dominios darán `DNS_TIMEOUT` a la vez.

```bash
# ¿responde el resolver interno?
getent hosts 172.16.1.120 >/dev/null && echo "ruta al DNS interno OK"
docker run --rm python:3.12-slim sh -c "pip -q install dnspython >/dev/null 2>&1; python -c \"
import dns.resolver
r = dns.resolver.Resolver(configure=False); r.nameservers=['172.16.1.120']; r.timeout=r.lifetime=5
print('iam.conaf.cl ->', [x.address for x in r.resolve('iam.conaf.cl','A')])\""

# ¿responde el proxy?
curl -sk -o /dev/null -w 'proxy: HTTP %{http_code}, %{size_download} bytes\n' \
     -H 'Host: iam.conaf.cl' https://172.31.2.100/
```

Lo esperado: `iam.conaf.cl -> ['172.31.2.100']` y `HTTP 200, 749 bytes`. Si el DNS interno
no responde desde vm3, el monitoreo igual arranca, pero avisará una sola vez con la guarda
anti-tormenta diciendo que revises la red antes que las aplicaciones.

## Paso 4 · Vhost en Nginx (opcional)

Solo hace falta si querés entrar por nombre. Sin esto se llega igual por
`http://172.31.2.42:PUERTO` desde el propio servidor.

```bash
PUERTO=
sed "s|127.0.0.1:8000|127.0.0.1:$PUERTO|" \
    /opt/apps/coipo_monitoreo/deploy/vhost-monitoreo-uat.conf \
    > /etc/nginx/sites-available/monitoreo-uat.conf
ln -sfn /etc/nginx/sites-available/monitoreo-uat.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

El vhost **no** lleva `default_server`: en vm3 ya lo tiene `tv-uat.conaf.cl.conf`, y un
segundo hace fallar `nginx -t` con *duplicate default server*. Mientras no exista el DNS
de `monitoreo-uat.conaf.cl`, se prueba con la cabecera `Host`.

## Paso 5 · Rama `uat` y push

El workflow va **solo en la rama `uat`**, como indica la guía 04.

```bash
cd /c/Users/luis.monsalve/Documents/GitHub/coipo_monitoreo
git checkout -b uat
git add .github/workflows/deploy-uat.yml
git commit -m "Rama uat: despliegue automatico al servidor de pruebas"
git push -u origin uat
```

Ese push despliega solo. Si el job queda en *Queued* y no arranca, casi siempre es que el
runner `conaf-uat` está detenido o la etiqueta quedó mal escrita.

## Paso 6 · Verificar

```bash
PUERTO=

docker ps --filter name=coipo_monitoreo --format '{{.Names}}\t{{.Status}}'
curl -s "http://127.0.0.1:$PUERTO/health"            # debe dar 200 aunque haya caidos
curl -s "http://127.0.0.1:$PUERTO/listo"             # 503 hasta la primera corrida
curl -s "http://127.0.0.1:$PUERTO/estado" | head -c 400
curl -sI -H 'Host: monitoreo-uat.conaf.cl' http://172.31.2.42/ | head -1

docker compose -f /opt/apps/coipo_monitoreo/docker-compose.yml logs -f backend
```

La primera corrida arranca sola al levantar el contenedor y tarda menos de un segundo, así
que `/estado` ya tiene datos apenas termina el despliegue.

---

## Operación

```bash
cd /opt/apps/coipo_monitoreo

docker compose logs -f backend            # incluye las alertas del canal log
docker compose restart backend
docker compose up -d --build              # redesplegar a mano, sin pasar por GitHub

# El historial, desde afuera del contenedor
docker compose exec backend python -m coipo_monitoreo estado
docker compose exec backend python -m coipo_monitoreo historial --dominio archivo.conaf.cl
```

**Respaldo del historial** (el volumen no se va con `docker compose down`, pero sí con
`down -v`):

```bash
docker run --rm -v coipo_monitoreo_datos:/datos -v "$PWD":/respaldo alpine \
    cp /datos/monitoreo.db /respaldo/monitoreo-respaldo.db
```

**Cambiar la lista de dominios**: editar `config/dominios.yml` en la rama `uat` y hacer
push. No hace falta reconstruir nada a mano — y el proceso relee el YAML en cada corrida,
así que un cambio aplicado en caliente tampoco exige reiniciar. Si el YAML queda inválido,
conserva el anterior y lo avisa en el log en vez de quedarse apagado en silencio.

---

## Qué NO valida este ambiente

Igual que para el resto de las apps, conviene tenerlo explícito:

- **No valida la decisión de arquitectura para producción.** En test el contenedor está
  bien; en producción el monitoreo debería correr con systemd sobre el host, porque
  dentro de Docker depende del demonio Docker, que es una de las cosas que tumban
  aplicaciones. Si Docker se cae, se caen las apps *y* el aviso.
- **Sí valida algo valioso e inesperado**: vm3 es una máquina **distinta** de vm2, donde
  viven las aplicaciones. Un monitoreo en vm3 vigilando el parque de vm2 no comparte
  destino con lo que vigila, que es justamente lo que se busca. Si desde vm3 el alcance
  de red del paso 3 funciona, vm3 es un candidato serio a ser el hogar definitivo —
  corriendo con systemd, no en Docker.
