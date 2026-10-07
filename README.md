# Maritime Intelligence Platform

Inteligencia de tráfico marítimo en tiempo casi real construida sobre datos AIS
en vivo: una tubería de ingesta automatizada, un almacén operativo
PostgreSQL/PostGIS, una capa REST con FastAPI y una visualización 3D en el
navegador de buques, puertos y rutas.

> **Estado: FASE 5 completa** — ingesta, validación, esquema PostGIS y retención
> de 7 días con archivo diario en Parquet (con subida opcional a OneDrive a
> través de `rclone`), funcionando contra datos AIS en vivo. Fuente de datos:
> `aisstream.io`. Ver [hoja de ruta](#hoja-de-ruta).

---

## Problema

Los datos AIS (Automatic Identification System) abundan pero son incómodos:
llegan como un flujo de mensajes de posición de alta frecuencia, son ruidosos, y
solo son útiles cuando se han validado, deduplicado, almacenado con los índices
correctos y convertidos en métricas. Los paneles existentes muestran
*posiciones*; rara vez muestran el *pipeline*.

## Objetivos

1. Consumir AIS en vivo de forma automática — sin `python ingest.py` a mano.
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
                            │ /health (FASE 1)    │
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

### 4. BD de tests (tests de integración)

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
│   │   ├── schemas/              FASE 6
│   │   ├── api/routers/         FASE 6
│   │   ├── ingestion/           FASE 2-4
│   │   ├── maintenance/         FASE 5
│   │   └── metrics/             FASE 10-11
│   ├── alembic/                 migraciones (URL inyectada desde app.config)
│   ├── tests/                   unit + integración
│   └── tools/probe_coverage.py  sonda de cobertura de la fuente
├── frontend/                    FASE 7
├── docs/
├── docker-compose.yml           api (el worker se suma en FASE 2)
├── docker-compose.test.yml      PostGIS efímero para tests
└── .github/workflows/ci.yml     ruff + pytest en cada push/PR
```

---

## Tests

```bash
cd backend
ruff check .
pytest                    # unit + integración
pytest -m integration     # solo lo que necesita PostGIS
```

Los tests son reales: validación de configuración, invariantes de la caja
delimitada, redacción de logs y — contra un contenedor PostGIS vivo — la ruta
de escritura (`tests/integration/`). Las fixtures de la tubería de ingesta son
**frames grabados del flujo en vivo**, no datos inventados.

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
