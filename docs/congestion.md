# Congestión portuaria

La congestión de cada puerto se **deriva en tiempo de lectura** a partir de las
posiciones almacenadas en `vessel_positions`. **No existe** una tabla
`port_activity`: conservar instantáneas de «ocupación» que envejecen tan rápido
como el propio muestreo habría sido incorrecto. La decisión está recogida en
[`docs/architecture.md`](architecture.md) §15 y, a nivel de datos, en
[`docs/data-model.md`](data-model.md).

## Semántica

Para cada puerto `p` se aplica:

1. **Ventana temporal.** Se consideran posiciones con
   `timestamp >= now(UTC) - recency_hours`. Valor por defecto **12 h**,
   configurable (`PORT_CONGESTION_RECENCY_HOURS`, rango 1–168).
2. **Radio.** Una posición cuenta como «cerca del puerto» si
   `ST_DWithin(geom, p.geom, radius_km * 1000)`. Valor por defecto **50 km**,
   configurable (`PORT_CONGESTION_RADIUS_KM`, rango 1–500).
3. **La posición actual decide.** Para cada MMSI, se toma su **última posición
   almacenada dentro de la ventana de recencia**. **Sólo** esa posición —si
   cae dentro del radio— se contabiliza. No basta con haber fondeado antes ni
   con una visita antigua: si el buque navegó lejos, ya no está «en el puerto».
4. **Clasificación.** Un buque está *esperando* si
   `sog < 0.5` (kn) **o** `nav_status` es `1` (fondeado) **o** `5` (atracado).
   *En movimiento* = `total − esperando`. Los casos con `sog` nulo y
   `nav_status` nulo cuentan como *en movimiento* en la fórmula, pero la
   verificación de evidencia prioriza la lógica: la categoría «sin dato de
   movimiento» del KPI es una lectura del propio renglón, no de la agregación
   del puerto.
5. **Límite del detalle.** `GET /ports/{id}/congestion` devuelve las naves
   contadas (ordenadas por `timestamp` descendente), con un **tope de 200**.
   Los conteos (`total`, `waiting`, `moving`) no están truncados por ese límite.

## Endpoints

- `GET /ports/congestion` — lista para el mapa. Retorna un `PortCongestionSummary`
  por puerto, con el renglón completo del puerto incrustado (`port.id`,
  `port.ne_id`, `port.name`, `port.country`, `port.latitude`,
  `port.longitude`). La respuesta incluye `radius_km`, `since`, `sampled_at`
  (más reciente entre los contados) o `null` cuando `total == 0`.
- `GET /ports/{id}/congestion` — detalle de un puerto. Añade `vessels` (≤200),
  con `mmsi`, `ship_name`, `sog`, `nav_status`, `timestamp`. Devuelve `404`
  si el `id` no existe en el catálogo.

Ambos están **protegidos** por la clave de lectura (`X-API-Key`) cuando
`API_READ_KEY` está configurada, igual que `/positions/latest`.

## Implementación

- **Catálogo.** `app/models/port.py` + `app/schemas/ports.py` + migración
  `c1142f77a9e3`. Las coordenadas y `ne_id` provienen de *Natural Earth 1:10m
  ports* (dominio público), alineadas al resto de la cartografía. `geom` es
  `ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography`, con índice
  GiST.
- **Consulta.** `app/ports.py` calcula por lectura con PostGIS: para el
  resumen, **LATERAL** por puerto + `ST_DWithin` sobre `vessel_positions.geom`;
  para el detalle, `NOT EXISTS` asegura que la última posición *dentro del
  radio* es también la última del buque en la ventana (evita contar un fondeo
  antiguo cuando el buque ya se alejó). `COUNT(latest.mmsi)` —no `COUNT(*)`—
  preserva ceros para puertos sin coincidencias.
- **Sin sobre-ingeniería.** No hay `materialized view`, no hay caché: el
  costo está acotado (ventana 12 h + hasta ~2000 buques) y la cadencia de
  refresco del frontend es la propia ventana, no un cron oculto.

## Notas de operativa

- **Congestión ≠ tiempo real.** La interfaz muestra la ventana: `since` es un
  instante UTC. Los marcadores en el globo usan la misma lectura.
- **Regiones ≠ puertos.** El catálogo es global (59 puertos verificados). Una
  región sin buques lee 0; la existencia del puerto no implica cobertura AIS.
- **Salida en UI.** En español. Código/commits, en inglés. Jamás se imprime
  secretos en logs ni en respuestas.
- **Semántica observable.** Si un MMSI tiene una posición reciente fuera del
  radio y una antigua dentro, **no** aparece: el endpoint responde «actual», no
  «última vez visto cerca». That matches the test
  `test_the_latest_position_decides_not_the_most_recent_visit`.