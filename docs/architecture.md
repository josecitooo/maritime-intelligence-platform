# Arquitectura

Decisiones de diseño de V1, cada una con la alternativa que se consideró y por
qué se descartó. Las decisiones se registran aquí en vez de inferirse del código.

---

## 1. Forma general

```
flujo AIS ─► worker (asyncio) ─► validar ► transformar ► dedup ► persistir
                                     │
                    ┌────────────────┴───────────────┐
                    ▼                                ▼
              FastAPI (REST)                  mantenimiento diario
                    │                          exportar ► verificar ► borrar
                    ▼                          y subir con rclone
              React + Three.js
```

**Decisión** — una sola imagen Docker, dos comandos (`uvicorn app.main:app` y
`python -m app.worker`).

**Alternativa** — repos/servicios separados para la API y la tubería.

**Rechazada** — la tubería comparte modelos, configuración y logging con la
API. Separarlos duplicaría todo eso sin aportar aislamiento a este tamaño.

---

## 2. Fuente de datos

**Decisión** — `aisstream.io`, un flujo WebSocket suscrito por caja delimitada.

**Alternativa** — AISHub.

**Rechazada** — los [términos de uso](https://www.aishub.net/join-us) de AISHub
exigen operar un receptor AIS físico y transmitir un feed NMEA crudo por UDP, con
umbrales de calidad (≥10 buques de media en 7 días, ≥90 % de disponibilidad,
muestreo reducido a ≤60 s). No es una API pública gratuita, y los datos
sintetizados están explícitamente prohibidos. El acceso no está disponible,
independientemente del mérito.

**Consecuencia** — `AISProvider` (`app/providers/base.py`) es un Protocol, de
modo que un adaptador de AISHub está a un archivo de distancia si algún día
existe una credencial. No se escribe ningún adaptador ahora: código sin probar
sería código muerto.

---

## 3. Ventana de lote frente a escrituras por mensaje

**Decisión** — el worker mantiene un socket abierto de forma continua, almacena
muestras en un buffer y vuelca un lote cada `INGESTION_INTERVAL_MINUTES` (30).

**Alternativa** — persistir cada mensaje al llegar.

**Rechazada** — las escrituras por mensaje acoplan la carga de la base de datos
al ritmo de mensajes y vuelven sin sentido la contabilidad de `ingestion_runs`.
Un lote da un lugar natural donde validar, deduplicar, contar e informar.

**Mitigación** — aisstream no permite repetición de mensajes, así que un fallo
pierde como mucho una ventana. El buffer está limitado (`BUFFER_MAX_MESSAGES`),
de modo que la memoria queda acotada.

---

## 4. Limitación por buque

**Decisión** — conservar como máximo una posición por buque cada
`POSITION_INTERVAL_MINUTES` (10 por defecto).

**Consecuencia** — el crecimiento de la tabla es una decisión de configuración,
no un cambio de código:

| Intervalo | filas/buque/día | 2 000 buques × 7 días | Tamaño aprox. |
|---|---|---|---|
| 5 min | 288 | 4,0 M | ~300 MB ⚠️ |
| **10 min** | **144** | **2,0 M** | **~150 MB ✓** |
| 30 min | 48 | 672 k | ~50 MB ✓ |

La caja delimitada decide qué buques se cuentan. La caja `Golfo + Caribe` por
defecto mide 344 buques distintos por minuto (`docs/ingestion.md` §2), de modo
que la columna de «2 000 buques» es del orden de magnitud correcto y no una
conjetura. La persistencia registra ya la cifra real en cada volcado
(`ingestion_runs.vessels`), así que esta tabla pasa a ser dato medido en cuanto
corran suficientes ventanas — y `POSITION_INTERVAL_MINUTES` sigue siendo la
única palanca si el crecimiento se dispara.

---

## 5. Programación

**Decisión** — tareas de `asyncio` dentro del worker: un consumidor perpetuo, un
bucle de volcado y un bucle de mantenimiento diario.

**Alternativas** — APScheduler, Celery, cron del host, GitHub Actions.

**Rechazada** — Celery necesita un broker (Redis), que aquí es coste puro;
APScheduler existe para expresar horarios tipo cron que no necesitamos (intervalos
fijos); GitHub Actions deja de programar en repositorios gratuitos tras 60 días
de inactividad, lo que mataría la ingesta en silencio.

---

## 6. Separación entre ORM y esquemas

**Decisión** — modelos tipados de SQLAlchemy 2.0 + esquemas Pydantic separados.

**Alternativa** — SQLModel.

**Rechazada** — SQLModel funde las capas de ORM y de esquema. Este codebase es a
la vez destino de un ETL y origen de una API; mantenerlas separadas significa que
renombrar una columna no puede cambiar en silencio el contrato público.

Las migraciones son Alembic, con la URL inyectada desde `app.config` para que el
mismo archivo de ajustes maneje local, tests y producción. `alembic check` corre
como test, de modo que un modelo que cambia sin su migración falla la suite y no
el primer despliegue.

---

## 7. PostGIS

**Decisión** — activar PostGIS (Supabase lo incluye) y guardar `geom` en
`vessel_positions`.

**Alternativa** — índices btree sobre `latitude`/`longitude` con aritmética de
caja delimitada hecha a mano.

**Rechazada** — «buques a menos de N km de un puerto» es una consulta de
proximidad. Hacerla bien con lat/lon planos obliga a re-derivar la matemática
esférica en cada llamante. PostGIS la expresa una vez y correctamente.

**Consecuencia** — `geom` es una *columna generada* derivada de `latitude` y
`longitude`, así que la aplicación nunca construye geometría y no puede poner un
punto desalineado con la fila que describe; `persist_window` ni sabe que PostGIS
está ahí. Ver `docs/data-model.md` §2.

**Una excepción, deliberada** — `position_jump` (`docs/ingestion.md` §5) mide la
distancia entre una posición almacenada y otra que todavía está en memoria, a lo
que ningún SQL alcanza porque la segunda fila aún no existe. `haversine_km` en
`app.ingestion.pipeline` es esa única medida, y un test de integración ejecuta
tanto esa como `ST_Distance` sobre los mismos pares para que no puedan
divergir. «En cada llamante» sigue siendo falso: hay una única función, y está
probada contra la base de datos.

---

## 8. Orden entre retención y exportación

**Decisión** — exportar primero, *releer el archivo* y solo entonces borrar, con
la fila del libro y el `DELETE` en una misma transacción.

**Alternativa** — el obvio `DELETE … WHERE timestamp < NOW() - INTERVAL '7 days'`.

**Rechazada por sí sola** — esa instrucción sola destruye datos que nunca se
archivaron.

**Cómo está construida realmente la guarda** — `app/maintenance/export.py`
selecciona el periodo, escribe
`EXPORT_DIR/vessel_positions_<inicio>_<fin>.parquet`, lo reabre y cuenta sus
filas, y solo entonces ejecuta el `DELETE` e inserta la fila de `export_runs`
para ese periodo. El recuento del `DELETE` tiene que ser igual al número de filas
del archivo, o todo se revierte y el archivo se elimina.

Esa comparación es la guarda, en vez de una consulta que busque una fila de
`export_runs` que cubra el periodo antes de borrar: dicha consulta prueba un
hecho que valió en algún instante anterior, mientras que compartir la transacción
vuelve la anotación del libro y la borrada un mismo evento. Que un volcado
inserte entre la lectura y el borrado una posición con marca de tiempo antigua
se manifiesta como discrepancia y aborta, en vez de convertirse en una fila que
nadie archivó. Un lock sostenido durante la escritura del archivo compraría la
misma garantía al precio de dejar la ingesta a merced de la latencia del disco.

**Consecuencia** — una exportación fallida no deja ni fila en el libro ni
borrado, así que el periodo se reintenta en el siguiente turno y no hay estado
parcial que reparar. `docs/data-model.md` §2 explica por qué, por tanto, no
existe una columna `status`.

**Un caso que conviene conocer** — un *kill* duro entre la escritura del archivo
y el commit deja un archivo que ninguna fila de `export_runs` menciona. Falla
seguro: sus filas siguen en la tabla, el siguiente turno las archiva de nuevo, y
el duplicado es visible porque el libro — no el listado del directorio — es lo
que enumera lo archivado. Un `docker stop` ordenado no llega a ese estado; la
cancelación revierte la transacción y elimina el archivo.

Los borrados se ejecutan como **una sola instrucción, no por trozos**. Trocear
solo libera locks si los trozos hacen commit por separado, y los commits
separados es exactamente lo que la guarda prohíbe: un fallo tras el primer trozo
dejaría un periodo a medio borrar sin fila en el libro. Dentro de una misma
transacción los locks se sostienen hasta el final de todos modos, así que
trocear añadiría un bucle de paginación sin comprar nada. El volumen está acotado
por §4 — un día de tráfico limitado, del orden de 10⁵ filas, contra el índice de
`timestamp` — y esa es la cifra a volver a medir antes de cambiarlo.

**No se usa** deliberadamente la partición: 7 días de datos limitados del
`Golfo + Caribe` no la justifican. Conviene revisarlo por encima de ~10 M de
filas.

**La subida es opcional y nunca se finje.** Cuando `RCLONE_REMOTE` está definido
el worker ejecuta `rclone copy EXPORT_DIR <remote>` tras cada turno de
mantenimiento; `rclone` compara tamaño y hora de modificación, de modo que la
misma llamada reintenta un fallo anterior. Cuando no está definido, el archivo
se queda en disco y ningún log dice lo contrario — el fallo que esta
funcionalidad existe para evitar es: filas borradas, una fila del libro diciendo
«archivado» y la única copia en un disco a punto de reconstruirse. Un binario
`rclone` ausente lanza excepción en vez de omitirse.

---

## 9. Fuente de verdad del frontend

**Decisión** — el navegador consulta `/health` (barato) y solo vuelve a pedir
`/positions/latest` cuando cambia `last_flush`.

**Alternativa** — volver a pedir las posiciones cada 30 minutos con un
temporizador.

**Rechazada** — un temporizador se desfasa respecto a la ingesta y devuelve
cargas obsoletas o duplicadas. La detección de cambios convierte la frescura en
una única fuente de verdad.

**Excepción** — una lectura que falló mientras el API estaba caído se repite en
cuanto `/health` vuelve a responder. Sin esa excepción, la primera caída dejaría
el mapa fijado en un error hasta recargar la página: ningún cambio de
`last_flush` puede llegar por una conexión que no se recupera sola.

---

## 10. No Objetivos explícitos de V1

| Excluido | Por qué |
|---|---|
| AWS (S3, Lambda, Glue, Athena, Kinesis, Redshift) | Fuera de alcance por requisito; la capa de exportación es donde se conecta el lago de datos de V2 |
| Celery / Redis / Kafka | Ningún problema de V1 los requiere |
| SSR | La app es un cliente WebGL; renderizar en servidor no aporta nada |
| Partición de tablas | No se justifica con el volumen actual (ver §8) |
| Capas meteorológicas y de ML | V2 — pero los límites de ingesta y de esquema son donde se acoplan |
| Estados de buque `arriving` / `departing` | No son inferibles de `NAVSTAT` + `SOG`; inventarlos violaría la regla de honestidad de datos |

---

## 11. Lecturas de la API

**Decisión** — la API es de solo lectura y sin estado: cada petición abre su
propia sesión con `session_scope()` y la cierra al salir, sobre manejadores
síncronos. No hay caché, ni vista materializada, ni capa de servicio entre el
router y el ORM.

**Alternativa** — mantener `/positions/latest` en memoria y refrescarlo con un
temporizador.

**Rechazada** — la caché sería una segunda fuente de verdad con su propio
reloj. §9 ya fija una única señal de frescura (`last_flush`); leer siempre de
la base mantiene el dato y la señal en el mismo sitio.

**La ruta caliente** — `/positions/latest` es lo que el mapa pide. Responde con
`DISTINCT ON (mmsi)` sobre la clave primaria `(mmsi, timestamp)`, una lista
explícita de columnas y un `ORDER BY timestamp DESC LIMIT`, de modo que «la
última posición de cada buque» es una propiedad del índice y el tamaño de la
respuesta lo fija `limit` (2000 por defecto, 10000 máximo). La proyección
explícita hace que renombrar una columna falle como validación de Pydantic
antes que vaciar un campo en silencio. El cliente solo la vuelve a pedir cuando
`/health` cambia `last_flush` (§9): una consulta por ventana de ingesta, no una
por sondeo del navegador.

**Dos edades, no una** — `/health` devuelve `last_flush` (¿sigue el worker
comprometiendo ventanas?) y `last_ais_message` (¿sigue llegando algo que
merezca comprometerse?). Un worker que vuelca ventanas vacías con regularidad
está vivo sin datos nuevos; uno detenido tiene un mensaje más nuevo que nunca
avanzará. Un solo número no distingue los dos. `data_freshness_minutes` es la
edad del segundo, acotada a cero porque el reloj del transpondedor puede ir por
delante del nuestro, y nulo mientras no haya datos: una base vacía no es una
base fresca.

**Clave de lectura opcional** — si `API_READ_KEY` está definida, las rutas de
datos exigen `X-API-Key`, comparada con `hmac.compare_digest`, y responden 401
con `WWW-Authenticate` en caso contrario. `/health` queda fuera a propósito: el
healthcheck del contenedor no puede llevar un secreto. Sin clave configurada la
API queda abierta, que es el caso por defecto del entorno de desarrollo.

**Consecuencia para el healthcheck** — una base inaccesible debe convertirse en
un 503 rápido y no en una petición que sobreviva al `--timeout=5s` del
Dockerfile. `build_engine` fija `connect_timeout` (2 s por dirección resuelta);
sin él el contenedor termina en *unhealthy* con «exceeded timeout» en el log en
vez del estado degradado que el endpoint está construido para informar.

**La regla sin clave foránea, vista desde el cliente** — `GET /vessels/{mmsi}`
devuelve 404 solo cuando ninguna de las dos tablas conoce el MMSI;
`GET /vessels/{mmsi}/track` devuelve `[]` y nunca 404, porque con la clave
foránea ausente el esquema no puede afirmar que el buque no existe; y
`GET /vessels` lista únicamente los buques con fila estática, ya que la
cobertura de identidad es del 52,7 % (`docs/ingestion.md` §2): es un directorio
de lo que AIS contó, no un censo de lo que se mueve. `VesselDetail.updated_at`
nulo significa exactamente «nunca reportó datos estáticos», no un hueco.

---

## 12. Presentación del mundo (FASE 8)

**Decisión** — el mundo se dibuja con Three.js: una esfera de mar y la costa de
Natural Earth 1:110m como segmentos de línea, encuadrada sobre la caja
consultada. La costa se versiona en `src/data/` (dominio público, sin atribución
obligatoria), así el build nunca depende de la red.

**Alternativa** — rellenar los polígonos de tierra (triangulación sobre la
esfera) o estampar una textura equirectangular.

**Rechazada** — rellenar exige triangulación geográfica que V1 no tiene razón de
cargar, y una textura es un mapa proyectado que hay que generar y mantener; la
línea sin relleno es lo que dibuja una sala de control, y la profundidad de la
esfera oculta lo que no mira a cámara. También se descartó OpenStreetMap como
fuente de costa: la licencia ODbL impone atribución y *share-alike* sobre los
datos en un repositorio público.

---

## Puntos de extensión

| Necesidad futura | Se conecta en |
|---|---|
| Segunda fuente AIS | implementar `AISProvider`, registrar en el worker |
| Lago de datos / AWS | `app/maintenance/export.py` ya produce Parquet |
| Capa meteorológica | un provider nuevo + una capa de frontend; sin cambios en la ruta del buque |
| Features de ML | tablas derivadas alimentadas desde `vessel_positions`, de solo lectura para la API |
