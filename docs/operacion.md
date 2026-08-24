# Runbook: qué hacer con cada alerta

Cada alerta trae el estado, el nivel y el responsable. Esta tabla dice qué pedir y con qué
evidencia, para que el pedido sea concreto y no "el sitio no anda".

Antes de escalar cualquier cosa, sacar la fecha del corte:

```bash
python -m coipo_monitoreo historial --dominio archivo.conaf.cl --dias 90
```

Esa fecha es lo que convierte un reclamo en un dato. Sin ella, el pedido es "yo me acuerdo
que antes andaba".

---

## Antes que nada: ¿es una falla masiva?

Si llegó **una sola alerta que dice "falla masiva en nivel X: N de 14 dominios"**, no
escales a los equipos de las aplicaciones. Catorce aplicaciones no se caen juntas: se cayó
algo compartido, o el propio monitoreo.

Verificar en este orden, desde el host donde corre el monitoreo:

```bash
# 1. ¿el host tiene red?
ping -c 2 172.31.2.100

# 2. ¿responde el resolver interno?
dig @172.16.1.120 iam.conaf.cl +short

# 3. ¿responde el proxy?
curl -sk -o /dev/null -w '%{http_code}\n' -H 'Host: iam.conaf.cl' https://172.31.2.100/

# 4. ¿qué vio el monitoreo?
python -m coipo_monitoreo chequear --sin-guardar --sin-alertas
```

Si 1 o 2 fallan, el problema es del host de monitoreo o de la red, no de los dominios.

---

## `DNS_NXDOMAIN` — el nombre no existe

**Nivel:** dns · **Responsable:** Informática

El registro A del dominio desapareció del DNS consultado. Le pasó a `archivo.conaf.cl`,
que funcionaba el 30 de julio de 2026 y dejó de resolver sin que nadie lo notara.

**Comprobar a mano:**

```bash
DOMINIO=archivo.conaf.cl
dig @172.16.1.120 "$DOMINIO" +short          # interno: vacío = NXDOMAIN
dig @200.27.2.65  "$DOMINIO" +short          # público
```

**Qué pedir:** restaurar el registro A. Adjuntar la fecha exacta que da `historial`.

**Ojo con esto:** un dominio puede no resolver *y además* no tener vhost en el proxy. El
monitoreo lo detecta y lo reporta como hallazgo adicional en la misma alerta ("además se
detectó: contenido: HTTP 200 con 0 bytes"). **Si aparece esa línea, hay que pedir las dos
cosas**: si solo se restaura el DNS, el sitio va a seguir apareciendo en blanco y va a
parecer que Informática no hizo su trabajo.

Comprobarlo:

```bash
DOMINIO=archivo.conaf.cl
curl -sk -o /dev/null -w '%{http_code} %{size_download}\n' \
     -H "Host: $DOMINIO" "https://172.31.2.100/"
# "200 0" = llega al proxy pero no tiene vhost propio
```

---

## `DNS_TIMEOUT` / `DNS_SIN_RESPUESTA`

**Nivel:** dns · **Responsable:** Informática

`DNS_TIMEOUT`: el servidor DNS no contestó. Si le pasa a **todos** los dominios a la vez,
es el resolver o la red del host de monitoreo — ver "falla masiva" arriba.

`DNS_SIN_RESPUESTA`: la zona existe pero el nombre no tiene registro A. Es distinto de
NXDOMAIN y se pide distinto: no hay que restaurar nada, hay que **agregar** el registro.

---

## `DNS_IP_INESPERADA` — resuelve, pero a otra IP

**Nivel:** dns · **Severidad:** aviso, no caída

El dominio resuelve a una IP distinta de la declarada en `ips_esperadas`. Puede ser un
cambio de DNS no avisado, o que el monitoreo esté consultando el horizonte equivocado.

El sitio puede estar funcionando perfectamente. Confirmar si el cambio fue intencional y,
si lo fue, actualizar `ips_esperadas` en la configuración.

---

## `PROXY_RECHAZADO` / `PROXY_TIMEOUT`

**Nivel:** transporte · **Responsable:** administrador del proxy

El equipo destino no acepta la conexión TCP en el puerto 443. nginx caído, no escuchando,
o un firewall en el medio.

```bash
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/172.31.2.100/443' && echo abierto || echo cerrado
```

**Qué pedir:** verificar que nginx esté corriendo y escuchando, y que no haya un firewall
bloqueando desde el host de monitoreo.

---

## `TLS_FALLA` — abre TCP pero falla el handshake

**Nivel:** transporte · **Responsable:** administrador del proxy

```bash
DOMINIO=iam.conaf.cl
openssl s_client -connect 172.31.2.100:443 -servername "$DOMINIO" </dev/null 2>&1 | head -20
```

Certificado vencido, mal cargado, o una versión de TLS no soportada.

---

## `UPSTREAM_SIN_RESPUESTA` — TLS completa y corta sin responder

**Nivel:** upstream · **Responsable:** administrador del proxy + equipo de la aplicación

**Es la falla más común hoy**: cuatro de los seis dominios degradados el 12 de agosto de
2026 están así. El proxy acepta la conexión TLS y después no entrega nada. Casi siempre es
el contenedor de la aplicación caído, o un bloque `server` que apunta a un upstream que ya
no existe.

Desde el navegador se ve igual que un problema de DNS. No lo es, y no lo arregla la misma
persona.

```bash
DOMINIO=chat-normativa.conaf.cl
curl -skv -H "Host: $DOMINIO" "https://172.31.2.100/" 2>&1 | tail -20
# "Empty reply from server" tras el handshake = este caso
```

**Qué pedir:**
1. Al equipo de la aplicación: `docker ps` en la VM, ver si el contenedor está arriba.
2. Al administrador del proxy: `error.log` de nginx y el bloque `server` del dominio.

---

## `HTTP_TIMEOUT` — abre la conexión y la respuesta nunca llega

**Nivel:** upstream · **Responsable:** equipo de la aplicación

La aplicación acepta la conexión pero no responde: proceso trabado, sin conexión a base de
datos, o sin workers disponibles. Revisar los logs de la aplicación.

---

## `CONTENIDO_VACIO` — HTTP 200 con 0 bytes

**Nivel:** contenido · **Responsable:** administrador del proxy (falta el vhost) o equipo
de la aplicación

**Este es el caso que un monitoreo mal hecho da por bueno.** El código HTTP dice 200 y no
hay página. Es la firma del catch-all de nginx: el dominio llega al proxy pero no tiene un
bloque `server` propio. Le pasa hoy a `interno-sidco.conaf.cl`.

```bash
DOMINIO=interno-sidco.conaf.cl
curl -sk -o /dev/null -w '%{http_code} %{size_download} bytes\n' \
     -H "Host: $DOMINIO" "https://172.31.2.100/"
```

**Qué pedir:** si son exactamente 0 bytes, pedir al administrador del proxy que cree el
vhost para el dominio. Si son pocos bytes pero no cero, es la aplicación devolviendo una
página incompleta: es del equipo de la aplicación.

---

## `CONTENIDO_SIN_TEXTO` — responde, pero no es lo que corresponde

**Nivel:** contenido · **Responsable:** equipo de la aplicación

Hay bytes y el código es correcto, pero falta el texto declarado en `texto_esperado`.
Puede ser una página de error, un placeholder, o directamente otra aplicación servida en
ese nombre.

Antes de escalar, descartar que sea un cambio legítimo: si la aplicación cambió su título
o su portada, hay que actualizar `texto_esperado` en la configuración, no abrir un
incidente.

---

## `HTTP_5XX` / `HTTP_ESTADO_INESPERADO`

**Nivel:** contenido · **Responsable:** equipo de la aplicación

`HTTP_5XX`: la aplicación responde con error de servidor. La red y el proxy están bien; el
error es de la aplicación. Revisar sus logs.

`HTTP_ESTADO_INESPERADO`: el código no está entre los aceptados para ese dominio.
Comparar contra `estados_ok`. Si el cambio es legítimo —por ejemplo, la aplicación ahora
redirige al login y devuelve 303— hay que actualizar la configuración:

```yaml
  - nombre: academia.conaf.cl
    estados_ok: [200, 303]
```

---

## `ERROR_MONITOR`

**Nivel:** monitor · **Responsable:** quien opera el monitoreo

No es una falla del dominio: falló el chequeo. Revisar el detalle y el log en
`datos/coipo_monitoreo.log`.

---

## Aviso de certificado por vencer

**Severidad:** aviso, no caída. El sitio sigue respondiendo.

Se avisa a los 30 días y no se repite antes de 7 días. Se agrupa **por certificado**, no
por dominio: el proxy sirve los catorce dominios con un mismo comodín `*.conaf.cl`, y
avisar por dominio serían catorce correos idénticos.

Medido el 12 de agosto de 2026: `*.conaf.cl` vence el 16 de febrero de 2027.

---

## Mantenimiento del propio monitoreo

**¿Sigue corriendo?** `estado` avisa si la última corrida tiene más de 45 minutos:

```bash
python -m coipo_monitoreo estado
systemctl list-timers coipo-monitoreo.timer
journalctl -u coipo-monitoreo.service -n 50
```

**Silenciar un dominio temporalmente** (por ejemplo, uno dado de baja) — mejor que
borrarlo, porque conserva el historial:

```yaml
  - nombre: dominio-a-silenciar.conaf.cl
    habilitado: false
```

Un dominio sacado de la configuración se olvida de `estado_actual` en la corrida
siguiente, pero sus eventos quedan en el historial.

**Respaldo.** Todo el historial es un archivo:

```bash
cp /opt/coipo_monitoreo/datos/monitoreo.db ~/monitoreo-respaldo.db
```
