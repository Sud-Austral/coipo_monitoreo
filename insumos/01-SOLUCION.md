# Lo que se construyo

## Que hace, en dos parrafos

Permite revisar de forma periodica una lista de dominios declarados en un
archivo de configuracion [config/dominios.yml], resolver su nombre, verificar
su certificado y pedir su contenido [coipo_monitoreo/chequeos.py], clasificar
el resultado en un estado y una severidad [coipo_monitoreo/modelos.py] y
guardar cada corrida con su evidencia cruda [coipo_monitoreo/almacenamiento.py].
Permite ademas comparar la corrida con la anterior, detectar los cambios de
estado y avisar solo cuando algo cambia [coipo_monitoreo/alertas.py], agrupando
las fallas masivas en un aviso unico y avisando tambien las recuperaciones;
ambas conductas estan fijadas en pruebas [tests/test_alertas.py].

Permite consultar el resultado de tres maneras. Por linea de comandos, con
ordenes separadas para chequear, ver el estado, ver el historial, emitir el
reporte y probar la configuracion y las alertas [coipo_monitoreo/cli.py]. Por
un reporte en un archivo que se abre sin instalar nada
[coipo_monitoreo/reporte.py]. Y por una consulta en linea, con rutas para el
estado completo del parque y para los eventos [coipo_monitoreo/web.py:123],
[coipo_monitoreo/web.py:142], mas una portada que entrega el mismo reporte
[coipo_monitoreo/web.py:154]. Cuando corre como servicio, un planificador
propio dispara las corridas y recarga la configuracion sin reiniciar
[coipo_monitoreo/planificador.py].

## Capacidades, una por una

[INFERIDO] Revisa el nombre, el transporte cifrado y el contenido por separado,
en vez de dar una sola respuesta de si o no: el modulo de chequeos tiene
funciones distintas para resolver el nombre, revisar el certificado y pedir la
pagina [coipo_monitoreo/chequeos.py], y el modelo guarda el resultado de la
resolucion de nombre aparte [coipo_monitoreo/modelos.py].

[INFERIDO] Cuenta como falla una respuesta formalmente correcta pero sin
contenido: hay una prueba dedicada a ese caso [tests/test_clasificacion.py].

[INFERIDO] No alerta con un solo dato: exige confirmacion antes de dar por
caido, y cuando alerta fecha el problema en la primera observacion y no en la
confirmacion [tests/test_alertas.py].

[INFERIDO] Avisa por tres vias, que se eligen segun lo que este configurado:
bitacora, correo y una direccion de webhook
[coipo_monitoreo/notificadores.py], [coipo_monitoreo/notificadores.py:48],
[coipo_monitoreo/notificadores.py:82].

[INFERIDO] Avisa el vencimiento proximo de certificados y agrupa ese aviso por
certificado en vez de repetirlo por dominio [tests/test_alertas.py],
[coipo_monitoreo/almacenamiento.py].

[INFERIDO] Guarda desde cuando esta cada dominio en su estado actual y lo
expone: hay una funcion de estado y una de eventos en el almacen
[coipo_monitoreo/almacenamiento.py] y una prueba que exige que la consulta de
eventos conteste desde cuando [tests/test_web.py].

[INFERIDO] Olvida un dominio cuando se lo saca de la configuracion, en vez de
dejarlo colgado en el historial [tests/test_config_historial.py],
[coipo_monitoreo/almacenamiento.py].

[INFERIDO] Valida la configuracion antes de correr y falla temprano con un
mensaje, en vez de pasar por alto una clave mal escrita
[coipo_monitoreo/config.py], [tests/test_config_historial.py].

[INFERIDO] Trae un modo de demostracion que levanta un servidor falso y permite
ver el comportamiento sin tocar nada real [coipo_monitoreo/demo.py],
[coipo_monitoreo/cli.py].

## Roles: quien ve que

[INFERIDO] No hay autenticacion ni autorizacion. Las cinco rutas detectadas son
de lectura y ninguna aparece protegida por un guard, un decorador o un
middleware [coipo_monitoreo/web.py:73], [coipo_monitoreo/web.py:93],
[coipo_monitoreo/web.py:123], [coipo_monitoreo/web.py:142],
[coipo_monitoreo/web.py:154].

[INFERIDO] Lo que si existe es una nocion de responsable de la falla, distinta
de un rol de acceso: el modelo define niveles y de cada nivel se deriva quien
responde [coipo_monitoreo/modelos.py], y una prueba exige que el responsable lo
determine el nivel y no el dominio [tests/test_clasificacion.py]. Es una
clasificacion de a quien derivar el arreglo, no un permiso.

[PENDIENTE] Quienes son esas personas o equipos en la practica, y si la
asignacion vigente en [config/dominios.yml] esta al dia.

## De donde salen los datos

[INFERIDO] La fuente de lo que se vigila es un unico archivo de configuracion
[config/dominios.yml], leido y validado por el modulo de configuracion
[coipo_monitoreo/config.py], con su ubicacion tomable del entorno
[coipo_monitoreo/cli.py:25].

[INFERIDO] Los datos observados los produce el propio sistema al consultar cada
dominio [coipo_monitoreo/chequeos.py]; no se importan de ningun otro sistema.

[INFERIDO] El historial vive en un archivo local de SQLite
[coipo_monitoreo/almacenamiento.py:17], cuya ruta se toma del entorno
[coipo_monitoreo/config.py:256]. El README explica que se eligio local a
proposito para no depender de infraestructura compartida [README.md].

[PENDIENTE] Quien es dueno de la lista de dominios, quien la actualiza cuando
nace o muere una aplicacion y con que autorizacion.

## Que no hace

Solo se afirman ausencias que el extractor busco de forma exhaustiva, y van
marcadas igual.

[INFERIDO] No tiene escritura por la via web: las cinco rutas detectadas son
todas de lectura [coipo_monitoreo/web.py:73], [coipo_monitoreo/web.py:93],
[coipo_monitoreo/web.py:123], [coipo_monitoreo/web.py:142],
[coipo_monitoreo/web.py:154]. Los dominios se agregan editando el archivo de
configuracion, no desde una pantalla.

[INFERIDO] No expone ninguna ruta cuyo camino contenga export, descarga o
informe. La entrega de resultados en un archivo va por la linea de comandos
[coipo_monitoreo/cli.py], [coipo_monitoreo/reporte.py].

[INFERIDO] No usa una base de datos compartida: la lista de tablas detectadas
por el extractor esta vacia y el unico motor importado es el archivo local
[coipo_monitoreo/almacenamiento.py:17]. El esquema esta escrito dentro de ese
mismo modulo y por eso el extractor no lo ve como tabla; [PENDIENTE]
documentarlo.

[INFERIDO] No repara nada: todas las funciones detectadas observan, clasifican,
guardan y avisan. No hay ninguna que actue sobre el sistema vigilado.

## Iteraciones

[INFERIDO] Hay dos flujos de trabajo automatizados en el repositorio, uno de
ellos de despliegue a un ambiente de prueba
[.github/workflows/deploy-uat.yml], [.github/workflows/readme.yml], mas
material de instalacion y operacion para tres formas de correrlo: contenedor
[Dockerfile], [docker-compose.yml], tarea programada en servidor
[deploy/crontab.ejemplo] y tarea programada en Windows
[deploy/tarea-windows.ps1], ademas de guiones de instalacion
[scripts/instalar.sh], [scripts/chequear.sh].

[INFERIDO] La documentacion de operacion y de ambiente de prueba esta escrita
[docs/operacion.md], [docs/ambiente-test.md], lo que sugiere que el sistema ya
paso de prototipo a algo que se opera. Confirmarlo es [PENDIENTE].

[INFERIDO] Los requisitos estan separados en tres archivos: los del programa,
los de la via web y los de desarrollo [requirements.txt],
[requirements-web.txt], [requirements-dev.txt]. Eso indica que la via web se
agrego como opcional sobre un nucleo que corre sin ella.

[PENDIENTE] No hay CHANGELOG ni migraciones numeradas en la evidencia, asi que
la historia de versiones no se puede reconstruir desde aca.
