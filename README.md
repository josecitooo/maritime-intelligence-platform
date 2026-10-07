# Maritime Intelligence Platform

[![CI](https://github.com/josecitooo/maritime-intelligence-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/josecitooo/maritime-intelligence-platform/actions/workflows/ci.yml)

Inteligencia de tráfico marítimo en tiempo casi real: una tubería de ingesta
automatizada sobre la corriente AIS, un almacén operativo PostgreSQL/PostGIS,
una capa REST con FastAPI y una visualización 3D en el navegador de buques,
puertos y rutas.

> **Estado: FASE 8 completa** — ingesta, validación, esquema PostGIS, retención
> de 7 días con archivo diario en Parquet (con subida opcional a OneDrive a
> través de `rclone`), API REST de solo lectura y sala de control con el mundo
> en 3D (Three.js) sobre datos AIS reales de `aisstream.io`. Ver
> [hoja de ruta](#hoja-de-ruta).

---

## Problema

Los datos AIS (Automatic Identification System) abundan pero son incómodos:
llegan como un flujo de mensajes de posición de alta frecuencia, son ruidosos, y
solo son útiles cuando se han validado, deduplicado, almacenado con los índices
correctos y convertidos en métricas. Los paneles existentes muestran
*posiciones*; rara vez muestran el *pipeline*.

## Objetivos

1. Consumir la corriente AIS de forma automática — sin `python ingest.py` a mano.
2. Validar y clasificar cada registro antes de almacenarlo.
3. Mantener 7 días de datos operativos en Supabase, archivando lo anterior como
   Parquet.
4. Servir una API REST documentada.
5. Hacer que un mundo marítimo 3D sea la experiencia principal, con los KPIs a
   su servicio — no un muro de tarjetas.

---

## Arquitectura

```
                       ┌────────────────────────────────────────────────┐
                       │          WORKER DE INGESTA (asyncio)           │
  wss://stream.        │                                                │
  aisstream.io ────────┤► consumidor ─► buffer (throttle por buque)     │
  bbox = Golfo/Caribe  │         │                                      │
                       │         ▼ cada 30 min (flush)                  │
                       │   validar ► transformar ► dedup ► insertar     │
                       │         ├─► vessels · vessel_positions         │
                       │         ├─► port_activity (congestión)         │
                       │         └─► ingestion_runs                     │
                       │                                                │
                       │   mantenimiento (diario)                       │
                       │     exportar Parquet/CSV ► rclone ► OneDrive   │
                       │     verificar ► borrar filas de > 7 días       │
                       └───────────────┬────────────────────────────────┘
                                       │ PostGIS / Supabase
                                       ▼
                            ┌─────────────────────┐
                            │      FastAPI        │
                            │     5 endpoints     │
                            └──────────┬──────────┘
                                       ▼
                    React + TypeScript + Three.js
                   globo 3D  ◄──────────────────►  mapa isométrico 3D
```

Las decisiones de diseño que hay detrás están registradas en
[`docs/architecture.md`](docs/architecture.md).

## Fuente de datos

[`aisstream.io`](https://aisstream.io/) — un flujo WebSocket gratuito de
mensajes AIS decodificados, suscrito por una caja delimitada.

**¿Por qué no AISHub?** AISHub no es una API pública gratuita. Sus
[términos](https://www.aishub.net/join-us) exigen operar un receptor AIS
físico y transmitir un feed NMEA crudo por UDP, sujeto a umbrales de cobertura
y disponibilidad. `AISProvider` es el punto de extensión que alojaría un adaptador
de AISHub cuando exista una credencial — ver
[`docs/ingestion.md`](docs/ingestion.md).

El servicio **no publica SLA ni permite repetición de mensajes**, así que el
worker se reconecta con backoff y la API expone la frescura de los datos en vez
de reclamar tiempo real.

---

## API

La API es de solo lectura y las respuestas llevan la frescura encima: además
del estado, `/health` devuelve `last_flush` y `last_ais_message`, de modo que
un cliente sabe qué tan viejo es lo que mira en lugar de fiarse de que la hora
del reloj coincida con la última ingesta.

| Método y ruta | Contenido |
|---|---|
| `GET /health` | Liveness, estado de la base y frescura de los datos |
| `GET /positions/latest` | Última posición de cada buque, de más reciente a menos |
| `GET /vessels` | Directorio de identidad estática, ordenado por recencia |
| `GET /vessels/{mmsi}` | Identidad + última posición; 404 si ninguna tabla lo conoce |
| `GET /vessels/{mmsi}/track` | Trayectoria de más antigua a más reciente (`[]` si no hay) |

Los tres endpoints que devuelven listas aceptan un `limit` acotado — 2000, 1000
y 5000 por defecto según el recurso. El esquema OpenAPI completo se sirve en
`/docs`.

**Clave de lectura** — si `API_READ_KEY` está definida, las rutas de datos
exigen la cabecera `X-API-Key` y responden `401` sin ella; `/health` queda
siempre accesible porque el healthcheck del contenedor no puede llevar un
secreto. Sin clave configurada la API es abierta, que es el caso por defecto en
desarrollo.

Los datos son **near real-time**: la API los sirve cada ventana de ingesta
(«actualizado cada 30 minutos»), no en el instante en que el transpondedor
emite. Las decisiones y sus alternativas están en
[`docs/architecture.md`](docs/architecture.md) §11.

---

## Puesta en marcha

### Requisitos previos

* Python 3.13, Node 24, Docker + Compose
* Una clave gratuita de AISStream en <https://aisstream.io/account>
  (login con GitHub)

### 1. Configurar

```bash
cp .env.example .env
# edita .env — como mínimo AISSTREAM_API_KEY y DATABASE_URL
```

### 2. Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ".[dev]"
alembic upgrade head                     # crea el esquema en DATABASE_URL
pytest                                   # necesita la BD de tests — ver 4
uvicorn app.main:app --reload                        # http://localhost:8000/docs
```

### 3. Docker

```bash
docker compose up --build                            # API en :8000
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

Necesita la API en marcha (paso 2 o 3). Por defecto habla con
`http://localhost:8000`; la dirección se cambia en `frontend/.env`.

### 5. BD de tests (tests de integración)

Desde la raíz del repositorio:

```bash
docker compose -f docker-compose.test.yml up -d --wait
cd backend && pytest                              # unit + integración
cd .. && docker compose -f docker-compose.test.yml down -v
```

La suite **no se omite** cuando este contenedor falta — CI aprovisiona el
mismo, y una suite que pasa en silencio sin él ha dejado en silencio de
probar la ruta de escritura.

---

## Variables de entorno

Plantilla completa en [`.env.example`](.env.example). Nunca se commitea `.env`.

| Variable | Por defecto | Propósito |
|---|---|---|
| `AISSTREAM_API_KEY` | — | Credencial del flujo (obligatoria en producción) |
| `MIN_LAT` / `MAX_LAT` / `MIN_LON` / `MAX_LON` | `8.0` / `31.0` / `-98.0` / `-59.0` | Caja delimitada Golfo + Caribe |
| `DATABASE_URL` | Postgres local | Conexión directa a Supabase, `sslmode=require` |
| `RETENTION_DAYS` | `7` | Ventana operativa antes de exportar + borrar |
| `INGESTION_INTERVAL_MINUTES` | `30` | Cadencia del flush = «actualizado cada N minutos» |
| `POSITION_INTERVAL_MINUTES` | `10` | Throttle por buque; controla el crecimiento de la tabla |
| `MAINTENANCE_INTERVAL_MINUTES` | `1440` | Cada cuánto se escribe el archivo y se depuran filas viejas |
| `RCLONE_REMOTE` | — | Habilita la subida a OneDrive |

Cambiar la caja delimitada cambia la región bajo análisis — sin tocar código.

El frontend tiene su propia plantilla en
[`frontend/.env.example`](frontend/.env.example): `VITE_API_URL` (dirección de
la API) y `VITE_API_KEY` (clave de lectura opcional). Se leen en el navegador,
así que van incluidas en el bundle y **ninguna es un secreto**: la clave
filtra clientes, no protege datos.

---

## Estructura del proyecto

```
├── backend/
│   ├── app/
│   │   ├── config.py            ajustes validados (único sitio que lee env)
│   │   ├── logging.py           logging JSON/pretty con redacción de secretos
│   │   ├── main.py              punto de entrada FastAPI
│   │   ├── db/                  engine, session, declarative base
│   │   ├── providers/base.py    protocolo AISProvider + dataclasses de muestra
│   │   ├── models/               vessels · vessel_positions · ingestion_runs
│   │   ├── schemas/             contratos de respuesta (Pydantic)
│   │   ├── api/routers/         health · positions · vessels
│   │   ├── ingestion/           FASE 2-4
│   │   ├── maintenance/         FASE 5
│   │   └── metrics/             FASE 10-11
│   ├── alembic/                 migraciones (URL inyectada desde app.config)
│   ├── tests/                   unit + integración
│   └── tools/probe_coverage.py  sonda de cobertura de la fuente
├── frontend/                    FASE 8 · React + Vite + Three.js
│   ├── src/api/                 cliente y espejo de los contratos
│   ├── src/components/          cabecera · mundo 3D · barra de estado
│   ├── src/data/                costa Natural Earth 110m (dominio público)
│   └── src/hooks/               sondeo de /health · detección de ventana
├── docs/
├── docker-compose.yml           api (el worker se suma en FASE 2)
├── docker-compose.test.yml      PostGIS efímero para tests
└── .github/workflows/ci.yml     ruff · alembic check · pytest · build
```

---

## Tests

```bash
cd backend
ruff check .
pytest                    # unit + integración
pytest -m integration     # solo lo que necesita PostGIS

cd ../frontend
npm run typecheck         # tsc --noEmit
npm run build             # tsc + vite build
```

Los tests son reales: validación de configuración, invariantes de la caja
delimitada, redacción de logs y — contra un contenedor PostGIS vivo — la ruta
de escritura (`tests/integration/`). Las fixtures de la tubería de ingesta son
**frames grabados del flujo de `aisstream.io`**, no datos inventados.

El cliente se verifica hoy con `typecheck` y `build`; sus tests unitarios
llegan en FASE 12.

---

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Decisiones, alternativas, por qué |
| [`docs/data-model.md`](docs/data-model.md) | Tablas, claves, reglas de fusión y lo que deliberadamente no se guarda |
| `docs/ingestion.md` | Fuentes, cobertura, buffer y flush, validación, errores |
| `docs/congestion.md` | FASE 10 |
| `docs/deployment.md` | FASE 13 |

> Los documentos están **en español**; los identificadores, comentarios y
> mensajes de commit del código, en inglés, que es el estándar del sector.

---

## Hoja de ruta

```
V1  ingesta · validación · Supabase · retención de 7 días · exportación Parquet
    archivo en OneDrive · FastAPI · React · Three.js (globo + mapa)
    buques · puertos · rastros · filtros · KPIs · congestión · Docker
    tests · despliegue · documentación

V2  capa meteorológica · ML (ETA, detección de anomalías) · lago de datos AWS

V3  analítica avanzada · optimización de rutas · alertas
```

AWS queda explícitamente fuera del alcance de V1.

---

## Limitaciones

* **No es tiempo real.** El flujo se lee y se vuelca según un calendario; la
  interfaz dirá *«Actualizado cada 30 minutos»*, nunca *«en vivo»*.
* **AISHub no está disponible** sin operar un receptor AIS.
* **aisstream.io no publica SLA**; la cobertura varía con la densidad de
  receptores.
* **La congestión portuaria es una heurística derivada**, documentada en
  `docs/congestion.md` — no es un valor que ninguna fuente AIS reporte.
* **La ETA no lleva año** y `DEST` suele venir vacío, así que ninguno de los
  dos alimenta métricas.
