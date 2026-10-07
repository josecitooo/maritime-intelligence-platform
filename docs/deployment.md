# Despliegue y operación (FASE 13)

La plataforma está pensada para desplegarse con Docker (backend + worker +
Postgres + frontend) o, en entornos minimalistas, `uvicorn` (API) + `python -m
app.workers.ais_consumer` (consumidor) + base PostGIS. No hay dependencias
ocultas y **todos los secretos viven exclusivamente en `.env`**.

## 1. Requisitos

- Docker Engine + Docker Compose (recomendado)
- Python 3.13 + `pip` + `pip-tools` (modo dev)
- Node.js LTS + `npm` (frontend)
- Una base PostGIS 16 accesible (para producción, usar el mismo motor)
- Clave de `aisstream.io` (obligatoria para ingesta, opcional para demo sin
  worker activo)

## 2. Variables de entorno

Copiar `.env.example` → `.env` en la raíz del repo. Nunca commitear `.env`.

### Backend (raíz `.env`)
| Variable | Default | Uso |
|---|---|---|
| `ENVIRONMENT` | `development` | `development/test/production` |
| `LOG_LEVEL` | `INFO` | Nivel de logs |
| `LOG_FORMAT` | `pretty` | `pretty/json` |
| `DATABASE_URL` | *(oblig.)* | Postgres/SSL según entorno |
| `AISSTREAM_API_KEY` | *(oblig. si worker activo)* | clave aisstream.io |
| `AISSTREAM_ENDPOINT` | `wss://...` | raramente necesario cambiarlo |
| `MESSAGE_TYPES` | `PositionReport,...` | tipos AIS a consumir |
| `MIN_LAT/MAX_LAT/MIN_LON/MAX_LON` | `8/31/-98/-59` | región activa por defecto |
| `BUFFER_MAX_MESSAGES` | `200000` | buffer antes de volcado |
| `POSITION_INTERVAL_MINUTES` | `10` | throttle de posiciones por MMSI |
| `RETENTION_DAYS` | `7` | retención posiciones |
| `EXPORT_INTERVAL_HOURS` | `24` | exportación Parquet a OneDrive |
| `ONEDRIVE_DIR` | `Maritime` | carpeta destino |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | orígenes permitidos |
| `API_READ_KEY` | *(vacío)* | si vacío, lectura abierta; si no, exigir `X-API-Key` |
| `API_WRITE_KEY` | *(vacío)* | si vacío, `PUT /regions` responde 403; si no, exigir `X-Write-Key` |
| `PORT_CONGESTION_RADIUS_KM` | `50` | radio por defecto para `/ports/congestion` (1–500) |
| `PORT_CONGESTION_RECENCY_HOURS` | `12` | ventana temporal por defecto (1–168) |
| `REGION_REFRESH_SECONDS` | `30` | refresco de regiones del worker |

### Frontend (`frontend/.env.example`)
| Variable | Default | Uso |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | URL de la API (accesible desde navegador) |
| `VITE_API_KEY` | *(vacío)* | `X-API-Key` de lectura; debe coincidir con `API_READ_KEY` |
| `VITE_API_WRITE_KEY` | *(vacío)* | `X-Write-Key` para `PUT /regions` |

> Estos valores **viajan en el bundle**: no son secretos. La protección real es
> `API_READ_KEY`/`API_WRITE_KEY` en backend.

## 3. Despliegue con Docker Compose (recomendado)

### Desarrollo (con worker + frontend)
```bash
docker compose up --build -d
```
Servicios: `db` (PostGIS), `api`, `worker`, `frontend` (Vite dev), `tests-db`
efímero para CI/local tests. La API queda en `http://localhost:8000/docs`
(si habilitado) y el frontend en `http://localhost:5173`.

### Sólo API + DB (sin ingesta activa)
```bash
docker compose up --build -d db api
```
El `worker` no arranca; la interfaz mostrará las ventanas ya volcadas.

## 4. Despliegue manual (producción minimalista)

1. **Base de datos**
   - Crear PostGIS 16. Aplicar migraciones: `cd backend && alembic upgrade head`
   - Verificar: `alembic current` y `alembic check`

2. **Backend (API)**
   ```bash
   cd backend
   pip install -e .
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   Asegurar `CORS_ORIGINS` correcto (sin `*` si hay credenciales). Logs con
   `LOG_FORMAT=json` en producción.

3. **Worker (ingesta)**
   ```bash
   cd backend
   python -m app.workers.ais_consumer
   ```
   Requiere `AISSTREAM_API_KEY`. Es tolerante a desconexiones: reintenta con
   backoff y no pierde el buffer.

4. **Frontend (build estático)**
   ```bash
   cd frontend
   npm ci
   npm run build
   ```
   Servir `dist/` con cualquier servidor estático (nginx, Caddy). Configurar
   `VITE_API_URL` apuntando a la API pública.

## 5. Puertos y congestión: verificación tras despliegue

- Migración `c1142f77a9e3` crea 59 puertos reales (Natural Earth 1:10m).
- `GET /health` responde OK (sin clave).
- Con posiciones dentro de 12 h y ≤50 km, `GET /ports/congestion` devuelve
  lecturas con `total/waiting/moving/sampled_at`. En UI aparecen marcadores
  con semáforo.
- `GET /ports/{id}/congestion` pide 404 si id inexistente.
- Si `API_READ_KEY` está activa, las rutas de lectura protegidas exigen
  `X-API-Key` correcto (401 en caso contrario).

## 6. CI

`.github/workflows/ci.yml` ejecuta: `ruff check`, `alembic check`, `pytest` (con
PostGIS efímero `docker compose -f docker-compose.test.yml`) y `npm run build`
en frontend. Añadir `npm test` (vitest) como paso explícito — ya presente en
la mentalidad, documentado aquí.

## 7. Seguridad y cumplimiento

- **Sin secretos en repo.** Sólo `.env.example`. Logs nunca imprimen claves,
  tokens ni URLs sensibles.
- **Principio de mínimo privilegio.** `CORS` acotado. API key opcional. Write
  key opcional.
- **Sin datos inventados.** Todo lo derivado está etiquetado como tal (UI
  nunca dice «en vivo», dice «Actualizado cada X minutos»).
- **Idioma.** UI/docs español; código/commits inglés.

## 8. Mantenimiento

- **Retención.** `RETENTION_DAYS=7` purga `vessel_positions` antiguas.
- **Regiones.** `PUT /regions` reprograma el worker sin reconectar (aisstream
  reemplaza la suscripción). Cambio visible en ≤ `REGION_REFRESH_SECONDS`.
- **Puertos.** Catálogo estable. Si se añade un puerto, hacerlo vía migración
  verificable (con `ne_id`).
- **Recuperación.** Worker reintenta; API devuelve 503 `degraded` cuando la
  base cae (`/health`). Cliente muestra «DEGRADADO/SIN CONEXIÓN» sin falsear
  estado.