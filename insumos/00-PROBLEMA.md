# Problema que se deduce del codigo

Advertencia de metodo: este documento se escribe hacia atras, desde lo que hay
construido hacia lo que probablemente estaba roto. La cadena de inferencia es
debil y cada eslabon va marcado. Que el sistema haga algo no prueba que ese
algo fuera el problema.

## La inferencia central

[INFERIDO] El sistema chequea de forma periodica si un conjunto de dominios
responde, distingue en que capa falla y avisa a alguien cuando cambia de
estado: hay un modulo de chequeos que resuelve nombres, revisa el certificado y
pide la pagina [coipo_monitoreo/chequeos.py], un motor que compara con el
estado anterior y decide si corresponde alertar [coipo_monitoreo/alertas.py] y
tres formas de avisar [coipo_monitoreo/notificadores.py]. Luego probablemente
habia un problema de enterarse tarde de que una aplicacion se cayo.

[INFERIDO] El problema no era solo detectar la caida sino saber a quien
pedirle el arreglo: el modelo de datos define un nivel y una funcion que dice
de quien es la responsabilidad [coipo_monitoreo/modelos.py], y hay pruebas
escritas alrededor de esa idea, entre ellas una que exige que la alerta diga
quien lo arregla y desde cuando [tests/test_alertas.py] y otra que exige que el
responsable lo defina el nivel y no el dominio [tests/test_clasificacion.py].

[INFERIDO] Un caso concreto pesa en el diseno: una respuesta que parece correcta
pero llega vacia. Hay una prueba dedicada a que eso se cuente como caida
[tests/test_clasificacion.py] y otra a que una redireccion aceptada no se
confunda con ese caso. Que ese escenario tuviera prueba propia sugiere que
habia ocurrido.

[INFERIDO] El propio README del repositorio afirma que en una revision manual
de agosto de 2026 varios de los dominios revisados estaban degradados sin que
nadie lo supiera [README.md]. Es una afirmacion escrita en el repositorio, no
un dato validado por el area usuaria: [PENDIENTE] confirmarla o corregirla.

## Quien sufre el problema

[INFERIDO] El codigo nombra responsables de arreglar, no roles de acceso. El
modelo distingue niveles y asigna a cada uno un responsable
[coipo_monitoreo/modelos.py], y la configuracion de dominios es el lugar donde
eso se declara [config/dominios.yml]. Los nombres de esos responsables no se
transcriben aca: son datos de la configuracion, no del codigo.

[INFERIDO] No hay control de acceso: en las cinco rutas detectadas no aparece
ningun guard, decorador de autorizacion ni middleware
[coipo_monitoreo/web.py:73], [coipo_monitoreo/web.py:93],
[coipo_monitoreo/web.py:123], [coipo_monitoreo/web.py:142],
[coipo_monitoreo/web.py:154]. Quien alcance la direccion, ve todo.

[PENDIENTE] Cuantas personas reciben las alertas y cuantas consultan el estado.
Una lista de destinatarios en una variable de entorno
[coipo_monitoreo/notificadores.py:54] no es un dato de dotacion.

[PENDIENTE] Quien es el area duena del monitoreo y a quien le rinde cuentas.

## Como lo resolvian antes

[INFERIDO] Antes se revisaba a mano. El README lo dice de forma explicita al
referirse a una revision manual [README.md], y el sistema no importa ningun
historial previo: no hay lector de planillas, ni endpoint de carga, ni semilla
de datos en la evidencia. El historial empieza cuando empieza a correr.

[PENDIENTE] Quien hacia esa revision manual, con que frecuencia y cuanto
tardaba.

## Volumen

[INFERIDO] El orden de magnitud es chico. El historial se guarda en un archivo
local de SQLite [coipo_monitoreo/almacenamiento.py:17], los chequeos se lanzan
con un grupo acotado de hilos [coipo_monitoreo/runner.py] y hay una operacion
de purga en el almacen [coipo_monitoreo/almacenamiento.py], lo que indica que
la retencion se acota en vez de crecer sin limite. Ninguna de esas senales es
una cifra.

[INFERIDO] La cantidad de dominios vigilados esta en la configuracion
[config/dominios.yml] y no en el codigo, de modo que cambia sin tocar el
programa; hay una prueba que lo exige [tests/test_config_historial.py].

[PENDIENTE] Cuantos dominios se vigilan hoy, cada cuanto y cuanta historia se
guarda.

## Que pasa si no se hace nada

[PENDIENTE] El codigo no lo responde, sin excepcion. No se deduce de que el
sistema exista.

## Quien decide que esta terminado

[PENDIENTE] No hay criterio de aceptacion del negocio en la evidencia. Hay una
bateria de pruebas [tests/test_alertas.py], [tests/test_clasificacion.py],
[tests/test_config_historial.py], [tests/test_web.py], pero que las pruebas
pasen es un criterio tecnico, no la conformidad de quien encargo el trabajo.

## Marco normativo

[VERIFICAR] Las alertas salen por correo con remitente y destinatarios tomados
del entorno [coipo_monitoreo/notificadores.py:53],
[coipo_monitoreo/notificadores.py:54], y opcionalmente a una direccion de
webhook [coipo_monitoreo/notificadores.py:82]. Que se envia hacia afuera y a
quien es una pregunta de tratamiento de datos y de seguridad que este documento
no cierra.
