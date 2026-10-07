# Modelo de datos

El esquema que escribe la tubería de ingesta. Las migraciones viven en
`backend/alembic/versions/`, los modelos en `backend/app/models/` — y
`alembic check` (un test) falla si alguna vez divergen.

Levanta antes la base de datos de tests: `docker compose -f
docker-compose.test.yml up -d --wait`.

---

## 1. Las tablas

| Tabla | Una fila por | Crecimiento |
|---|---|---|
| `vessels` | MMSI que ha reportado datos estáticos | ≈ buques distintos en la caja delimitada |
| `vessel_positions` | MMSI × hora del mensaje | ≈ buques × 144/día (`POSITION_INTERVAL_MINUTES` = 10) |
| `ingestion_runs` | volcado cerrado | 48/día, ventanas vacías incluidas |
| `export_runs` | periodo archivado | 1/día con `MAINTENANCE_INTERVAL_MINUTES` = 1440 |

`ports` y `port_activity` llegan con la FASE 10.

### `vessels`

| Columna | Tipo | Notas |
|---|---|---|
| `mmsi` | int, PK | asignado por AIS, nunca por una secuencia (`autoincrement=False`) |
| `name`, `callsign` | text | `NULL` cuando la fuente no lo reportó |
| `imo` | int | `0` se decodifica a `NULL` en el adaptador |
| `ship_type` | smallint | AIS `Type`; `0` → `NULL` |
| `length_m`, `width_m` | smallint | `A + B` y `C + D`; se requieren **ambas** mitades |
| `draught_m` | float | `draught` de AIS en metros; `0` → `NULL` |
| `destination` | text | `DEST` de AIS; en blanco se decodifica a `NULL`, y la fuente lo deja vacío con sorprendente frecuencia |
| `eta` | text | `MM-DD HH:MM` — **sin año**, por eso no se parsea |
| `source` | text | not null |
| `updated_at` | timestamptz | `GREATEST` de las dos observaciones, de modo que un frame fuera de orden no puede envejecerlo hacia atrás |

### `vessel_positions`

Clave primaria `(mmsi, timestamp)`.

| Columna | Tipo | Notas |
|---|---|---|
| `mmsi`, `timestamp` | int, timestamptz | la clave |
| `latitude`, `longitude` | float | los valores del decodificador — la única fuente de verdad de la geometría |
| `sog`, `cog` | float | nudos / grados; los centinelas ya están a `NULL` |
| `heading`, `rot`, `nav_status` | smallint | centinelas ya a `NULL`; `nav_status = 15` se **conserva** y aparece como desconocido |
| `ship_name` | text | del mensaje de posición cuando el tipo lo trae |
| `flags` | text[] | not null, **sin default** — `[]` cuando la fila se da por buena, ver §2 |
| `geom` | geography(point, 4326) | **generada**, ver §2 |

### `ingestion_runs`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | int, PK | `Integer`, no `BigInteger` |
| `window_start` | timestamptz, nullable | `NULL` marca la primera ventana tras un arranque |
| `window_end` | timestamptz | cuándo se ejecutó el volcado |
| `reason` | text | `scheduled` / `shutdown` |
| `positions`, `statics`, `vessels` | int | lo que se escribió |
| `throttled`, `evicted` | int | los contadores del buffer para esa misma ventana |
| `rejected`, `flagged` | jsonb | `reason -> count` (`docs/ingestion.md` §5) |

### `export_runs`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | int, PK | |
| `started_at`, `finished_at` | timestamptz | se escriben solo si la exportación tuvo éxito, así que el par nunca encierra un fallo |
| `period_start` | timestamptz | `MIN(timestamp)` de lo exportado — dónde empieza el archivo |
| `period_end` | timestamptz | el corte vigente, `now - RETENTION_DAYS`; por debajo de él se fue todo |
| `row_count` | int | filas del archivo, y el recuento que el `DELETE` tenía que igualar |
| `file` | text | nombre del archivo relativo a `EXPORT_DIR`; el Parquet es el archivo de referencia. Un CSV, cuando `EXPORT_CSV` está activo, comparte el nombre base y no necesita columna propia |
| `byte_size` | bigint | tamaño de ese Parquet, para que una subida truncada sea visible sin abrirlo |

La tabla son unas pocas filas al año, así que no lleva índice más allá de su
clave primaria ni clave foránea a `vessel_positions` — apuntaría a filas que el
propósito de la tabla es eliminar.

---

## 2. Decisiones

### Sin clave foránea de `vessel_positions.mmsi` a `vessels.mmsi`

**Decisión** — `mmsi` es un entero sin restricción.

**Motivo** — los datos estáticos no están garantizados. La suscripción al tipo 24
elevó la cobertura de identidad de los buques posicionados del 24,7 % al 52,7 %
(`docs/ingestion.md` §2), de modo que cerca de la mitad de los buques del mapa no
tiene fila en `vessels`. Una FK rechazaría exactamente las posiciones que el
producto existe para dibujar.

**Alternativa** — insertar una fila *stub* en `vessels` para cada MMSI visto.

**Rechazada** — un *stub* es un registro inventado: una fila sin nombre, sin tipo
y sin dimensiones, indistinguible de un buque cuyos datos estáticos aún no han
llegado. Duplicaría el recuento de filas de la tabla a cambio de integridad
sobre datos que sabemos que a menudo faltan.

### `geom` es una columna generada

**Decisión** —
`GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED`,
indexada con GiST.

**Motivo** — los dos flotantes son la única fuente de verdad, así que el punto no
puede desviarse de la fila que describe, y `persist_window` nunca necesita saber
que PostGIS existe. Su `INSERT` compilado omite `geom`; Postgres rechaza
cualquier intento de escribirlo.

**Alternativa** — construir el WKT en la aplicación e insertarlo.

**Rechazada** — más código, un fallo más (el punto en desacuerdo con las
coordenadas), y ningún beneficio.

`persisted=True` no es cosmético: sin él SQLAlchemy no emite ninguna palabra
clave en PostgreSQL 18+, donde `VIRTUAL` pasa a ser el valor por defecto — y una
columna generada virtual no puede llevar el índice GiST.

### Sin restricciones CHECK

**Decisión** — la validación ocurre en `process_window`, que cuenta los
rechazos en vez de abortar. El esquema no declara comprobaciones de rango.

**Motivo** — la misma regla que eliminó `outside_bbox` (`docs/ingestion.md` §5):
una comprobación que no puede dispararse es código muerto. La validación corre
primero, de modo que una `latitude` fuera de rango nunca llega al `INSERT`.

**Alternativa** — `CHECK (latitude BETWEEN -90 AND 90)`.

**Rechazada** — si alguna vez *se* disparara, abortaría la transacción de la
ventana entera, perdiendo cada buena fila por culpa de una mala. Contar el
rechazo en `ingestion_runs.rejected` informa del mismo hecho sin ese radio de
acción.

### Sin columna `status` en `ingestion_runs`

**Decisión** — la fila de la ejecución y las filas que cuenta se escriben en una
misma transacción.

**Motivo** — una escritura fallida no deja fila alguna. No hay nada que afirme
éxito, y nada que mantener en paso. Un fallo es un hueco en `window_end` más un
error en el log.

**Alternativa** — capturar el error e insertar `status = 'failed'`.

**Rechazada** — eso necesita una segunda transacción para registrar el fallo de
la primera, e invita a la mentira que el diseño está construido para evitar: una
fila diciendo `failed` para una ventana cuyas posiciones sí se almacenaron, o
`success` para una que hizo *commit* sin ellas. «Lo contado y su recuento hacen
*commit* juntos» no puede ser falso.

### Sin columna `status` en `export_runs`

**Decisión** — la fila del libro y el `DELETE` que autoriza se escriben en una
misma transacción, así que no hay columna que registre cómo fue una exportación.

**Motivo** — una exportación que falla no deja ni lo uno ni lo otro: ni fila que
afirme éxito, ni filas retiradas, ni nada a medio hacer que reparar. La fila se
escribe solo después de que el archivo exista y se haya releído, lo que convierte
a `export_runs` en una afirmación sobre un periodo que *fue* archivado y no en un
registro de intentos. `architecture.md` §8 tiene el orden y la guarda por
recuento que lo hacen cumplir.

**Alternativa** — registrar `status = 'failed'` en las exportaciones que no lo
consiguieron.

**Rechazada** — registrar un fallo necesita una segunda transacción para
informar del destino de la primera, y la pregunta que cualquiera se hace
de verdad («¿siguen estas filas en la tabla?») se contesta buscándolas. Un
enum que puede divergir de la realidad es peor que un hueco en los datos.

### El archivo lleva `vessel_positions` sin `geom`, declarado una sola vez

**Decisión** — los esquemas Parquet y CSV omiten `geom`, y se declaran como un
único esquema Arrow explícito en `app/maintenance/export.py` del que se derivan
tanto el `SELECT` como la cabecera del CSV.

**Motivo** — `geom` se genera a partir de `latitude` y `longitude` (§2), así que
el archivo ya contiene todo lo necesario para reconstruirla, y copiar el valor
es una oportunidad para que las dos diverjan. Derivar la lista de columnas de
una única declaración significa que no se puede añadir una columna al `SELECT` y
olvidarla en el archivo, ni al revés.

**Alternativa** — dejar que pyarrow infiera el esquema a partir del primer lote
de filas.

**Rechazada** — una columna toda a `NULL` se infiere como tipo `null`, y el
archivo del día siguiente, donde esa misma columna sí tiene valores, inferiría
otra cosa. Dos archivos de la misma tabla que no se pueden concatenar es un bug
de calidad de datos esperando un día tranquilo.

### Sin columna `gap_seconds`

Es `window_end - window_start`. Guardar una resta duplica datos que se pueden
leer, y un trigger o una comprobación para mantener la copia en paso es la regla
de código muerto otra vez.

### `flags` por fila, sin `DEFAULT`

**Decisión** — `vessel_positions.flags` es `text[] NOT NULL` sin default. Un
array vacío significa que la fila se da por buena; `sog_implausible` y
`position_jump` registran qué estaba mal pero se almacenó. La agregación por
ventana en `ingestion_runs.flagged` se deriva de estas filas en vez de contarse
una segunda vez, de modo que las dos no pueden divergir.

**Motivo** — una marca que nadie puede localizar es media marca. `flagged` dice
que tres posiciones de esta ventana no se creyeron; la columna dice *cuáles* tres,
que es sobre lo que filtrará la vista de trazas. Llegó con `position_jump`
(`docs/ingestion.md` §5) — el momento en que la decisión anterior estaba
esperando.

**Alternativa** — quedarse solo con el agregado, como antes.

**Rechazada** — era la decisión correcta mientras `sog_implausible` era la única
marca: una columna escrita por una instrucción y leída por nadie. Con una segunda
marca y un filtro en camino, se lee.

**También rechazada** — un `DEFAULT '{}'` permanente. La migración toma prestado
uno para el `ADD COLUMN` de modo que las filas escritas antes de la regla puedan
existir sin veredicto, y después lo elimina. Un default fijo permitiría que un
`INSERT` que olvidara `flags` tuviera éxito en silencio con un array vacío, y una
columna de veredictos solo es fiable si omitir uno falla.

### Los contadores de transporte tampoco son columnas

`frames`, `decode_errors`, `unusable` y `reconnects` son acumulativos durante la
vida del proceso. En una tabla por ventana serían la única columna que vuelve a
cero en cada reinicio, y sus deltas serían erróneos a través de la frontera del
reinicio. Se informan en el log estructurado al cerrar cada ventana, y `/health`
expone `last_flush` y `data_freshness_minutes` para que la salud del flujo sea
diagnosticable desde fuera.

---

## 3. Cómo se escribe una ventana

```
load_previous_positions(mmsis) # una lectura: dónde estaba cada buque por última vez
        │
        ▼
process_window(batch, previous)  # pura: rechaza y marca, no toca nada
        │
        ▼
persist_window(result, …)     # una transacción
        ├── INSERT … ON CONFLICT DO NOTHING  (mmsi, timestamp)
        ├── INSERT … ON CONFLICT DO UPDATE   (mmsi)
        └── INSERT ingestion_runs
        │
        ▼
buffer.clear()                # solo después de que la escritura haya aterrizado
```

* **La escritura precede al vaciado.** Si lanza excepción, las muestras quedan en
  el buffer y el siguiente intento reintenta la ventana entera — la validación es
  pura, así que el reintento alcanza el mismo veredicto. La lectura del ancla se
  repite también en ese reintento, de modo que no puede derivarse mientras una
  ventana espera.
* **La lectura del ancla es lo único que `process_window` no puede aportar.** Una
  ventana contiene lo que llegó desde el último volcado; `position_jump` es
  precisamente la afirmación de que las dos discrepan, así que necesita la fila
  que vino antes. La lectura corre en un hilo por la misma razón que la escritura.
* **Reproducir es un no-op.** `ON CONFLICT DO NOTHING` sobre la clave primaria
  significa que una ventana reproducida o un fallo entre la escritura y el
  vaciado no pueden duplicar una traza.
* **La fila de ejecución *no* se deduplica.** Dos volcados son dos eventos; la
  segunda fila es la prueba de que hubo un reintento.
* **Una ventana vacía escribe fila igualmente.** Ese es el *keepalive* que evita
  que un proyecto gratuito de Supabase inactivo se pause.
* **La escritura corre en un hilo** (`asyncio.to_thread`) para que el consumidor
  siga leyendo el socket mientras tanto — aisstream descarta mensajes cuando las
  lecturas se estancan.

### La fusión de identidad

```sql
SET name       = COALESCE(EXCLUDED.name,       vessels.name),
    ship_type  = COALESCE(EXCLUDED.ship_type,  vessels.ship_type),
    …
    updated_at = GREATEST(EXCLUDED.updated_at, vessels.updated_at)
```

Dentro de una misma ventana el buffer solo rellena huecos (`merge_static`),
porque una parte A del tipo 24 que trae un nombre no debe borrar un tipo de
nave aprendido de un tipo 5. Entre ventanas la regla tiene que admitir el
cambio: `destination` y `draught` pertenecen al viaje actual, y congelarlos en
la primera detección convertiría `vessels` en un museo. Así que un campo que el
mensaje **omitó** conserva el valor almacenado, y un campo que **trajo** lo
reemplaza.

### Cómo se escribe una exportación

```
SELECT … WHERE timestamp < cutoff        # el periodo, en orden de traza
        │
        ▼
write Parquet (+ CSV cuando EXPORT_CSV)  # en EXPORT_DIR
        │
        ▼
releerlo y contar las filas                      # un archivo que no se puede leer no es un archivo
        │
        ▼
DELETE … WHERE timestamp < cutoff        # el recuento debe igualar ese número, si no ↓
INSERT export_runs                       #   rollback: las filas quedan, el archivo se elimina
        │
        ▼
rclone copy EXPORT_DIR <remote>          # solo cuando RCLONE_REMOTE está definido
```

* **El archivo precede al borrado y se elimina si el borrado no ocurre**
  (`architecture.md` §8).
* **El nombre del archivo es el periodo** —
  `vessel_positions_<period_start>_<period_end>.parquet`, en UTC, con los dos
  puntos sustituidos por guiones porque un nombre que contiene `:` es inutilizable
  en Windows.
* **`vessels` e `ingestion_runs` nunca se depuran.** Solo `vessel_positions` crece
  sin límite, y una fila de `vessels` es de lo que tratan las posiciones.
* **`export_runs` enumera el archivo; el directorio no.** Un archivo que ninguna
  fila nombra es el resto de una ejecución muerta antes de poder confirmar: sus
  filas siguen en la tabla y la siguiente las archiva de nuevo, de modo que ese
  archivo es el duplicado y no el más reciente.

---

## 4. Índices

| Índice | Sobre | Sirve para |
|---|---|---|
| `vessel_positions_pkey` | `(mmsi, timestamp)` | consultas puntuales y la traza de un buque, ya en orden temporal — lo que también convierte el `DISTINCT ON (mmsi)` de `load_previous_positions` en una propiedad del índice y no en una ordenación |
| `ix_vessel_positions_timestamp` | `(timestamp)` | borrados de retención y «todo lo de los últimos N minutos» de todos los buques — algo que la PK no puede servir |
| `idx_vessel_positions_geom` | `gist (geom)` | proximidad, p. ej. «buques a menos de 50 km de un puerto» (`architecture.md` §7) |

---

## 5. Cómo se demuestra

```bash
docker compose -f docker-compose.test.yml up -d --wait
cd backend
alembic upgrade head           # o deja que lo haga la fixture
pytest -m integration          # los tests que necesitan PostGIS
```

Afirman lo que un test unitario no puede: cada campo de `PositionSample`
sobreviviendo el viaje de ida y vuelta, el punto derivado cayendo a menos de un
metro de las coordenadas almacenadas, una reproducción colapsando en vez de
duplicar, un campo omitido sin borrar el almacenado, un casco a medio conocer
almacenando `NULL`, la migración sin haberse desviado de los modelos, y — contra
la retención — que el periodo archivado es el periodo depurado, que una
exportación fallida no retira nada y que lo escrito sigue pudiéndose leer.

**Nada se omite cuando falta la base de datos.** CI aprovisiona el mismo
contenedor, y una suite que pasa discretamente sin él ha dejado discretamente de
probar la ruta de escritura.
