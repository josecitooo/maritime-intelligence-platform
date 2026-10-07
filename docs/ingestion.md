# Ingesta

Cómo entran los datos AIS en la plataforma, qué se rechaza y qué está garantizado.

---

## 1. Fuentes consideradas

### AISHub — no utilizable

De los [términos de uso de AISHub](https://www.aishub.net/join-us):

> *"Every AISHub contributor is required to provide at least one raw AIS feed
> in NMEA format."*
>
> *"Applications without an operational AIS station and feed will not be
> approved."*
>
> Prohibido: *"Synthesized or artificially generated NMEA data"* y *"Data
> from publicly available AIS sources or services."*

El acceso a la API se gana operando un receptor AIS físico y transmitiendo NMEA
por UDP, y después superando umbrales de calidad (media ≥10 buques, ≥90 % de
disponibilidad, muestreo reducido a ≤60 s, retardo ≤10 s) sobre una ventana
rodante de 7 días. No existe nivel de pago.

El propio endpoint REST (`https://data.aishub.net/ws.php`) admite filtrado por
caja delimitada, un `interval` (edad máxima en minutos) y salida
`json`/`xml`/`csv` con un máximo de una petición por minuto — encaja bien con un
sondeo cada 30 minutos. El bloqueo es el acceso, no la capacidad.

**No se implementa ningún adaptador de AISHub en V1.** No podría probarse contra
integración, así que sería código muerto presentado como una característica. Ver
`AISProvider` más abajo para el punto de extensión.

### aisstream.io — seleccionada

| Propiedad | Valor |
|---|---|
| Transporte | `wss://stream.aisstream.io/v0/stream` |
| Autenticación | clave de API (gratuita, login con GitHub) |
| Suscripción | `APIKey`, `BoundingBoxes` (obligatorio), `FiltersShipMMSI`, `FilterMessageTypes` |
| Plazo | la suscripción debe enviarse en **3 s** desde la conexión |
| Compresión | `permessage-deflate` **obligatorio** — desde septiembre de 2026 las conexiones sin comprimir tienen límite de ancho de banda y pueden perder mensajes |
| Conexiones | 3 suscritas por cuenta, 3 por IP de origen |
| Actualizaciones de suscripción | 1 por segundo; **sustituyen** en vez de fusionarse |
| Filtros MMSI | máximo 200 valores de nueve dígitos |
| Presión de retorno | si el cliente no lee lo bastante rápido, se descartan mensajes |
| Garantías | **sin SLA, sin repetición, sin durabilidad** — documentado por el servicio |

Tipos de mensaje usados en V1 (suscripción por defecto):

| Tipo | Aporta |
|---|---|
| `PositionReport` | lat/lon, SOG, COG, rumbo, ROT, estado de navegación |
| `StandardClassBPositionReport` | los mismos campos, transmisores AIS Clase B |
| `ExtendedClassBPositionReport` | Clase B + nombre secundario del buque |
| `ShipStaticData` | nombre, indicativo, IMO, tipo de nave, dimensiones A/B/C/D, calado, destino, ETA |

Los tipos Clase B están **activados porque la medición lo justificó**: llevaban
el 37,8 % de los frames en la sonda del Caribe. Descartarlos descartaría más de
un tercio del tráfico.

---

## 2. La sonda de cobertura es una puerta de paso

Antes de construir la tubería, `tools/probe_coverage.py` mide si la caja
delimitada configurada recibe de verdad datos utilizables:

```bash
cd backend
python tools/probe_coverage.py --duration 600
```

Informa de la aceptación de la suscripción, buques únicos, ritmo de mensajes,
cobertura de datos estáticos, cuota Clase B y huecos temporales. Códigos de
salida:

| Código | Significado |
|---|---|
| 0 | datos utilizables — continuar |
| 1 | datos insuficientes — reevaluar la fuente antes de seguir |
| 2 | error de configuración (clave ausente, suscripción rechazada) |

Los frames en bruto se escriben en `.captures/` (ignorado por git). Un subconjunto
seleccionado se commitea en `backend/tests/fixtures/probe_sample.jsonl` para que
la suite de tests reproduzca **frames reales grabados** en vez de inventados.

### Resultado de la ejecución de 600 s (2026-10-06, caja del Caribe)

| Métrica | Valor | Puerta |
|---|---|---|
| Frames capturados | 222 | — |
| Ritmo de mensajes | 0,4 msg/s | — |
| Buques únicos | **89** | ≥ 5 → **pasa** |
| Frames estáticos | 26 | ≥ 1 → **pasa** |
| Cuota Clase B | 37,8 % | justifica suscribirse |
| Huecos > 15 s | 2 (máximo 17,1 s) | < 3 → **pasa** |
| Errores de parseo | 0 | — |

Veredicto: **salida 0, utilizable**. El flujo es continuo y los datos estáticos
son ricos; solo la *densidad* es baja, que es una propiedad de la región (tabla
siguiente) y no del cliente.

### La cobertura es regional — medida, no supuesta

60 s por caja, misma suscripción, mismo cliente:

| Región | Frames | Buques únicos | Ritmo |
|---|---|---|---|
| Caribe (`8.0..18.2`, `-72.0..-59.0`) | 24 | 22 | 0,4/s |
| **Golfo + Caribe (`8.0..31.0`, `-98.0..-59.0`)** | **355** | **344** | **5,9/s** |
| Este de EE. UU. + Caribe (`8.0..42.0`, `-82.0..-59.0`) | 507 | 484 | 8,5/s |
| NW de Europa (`48.0..51.5`, `-6.0..8.0`) | 871 | 790 | 14,5/s |

Las redes al estilo AISHub son terrestres: la cobertura sigue las costas donde
hay receptores desplegados. La cuenca del Caribe está poco cubierta, mientras el
Golfo de México y la costa este de EE. UU. son densos.

**La caja configurada es la segunda fila.** Se eligió por dos razones: 15× la
densidad de buques de la cuenca por sí sola, y todavía contiene la República
Dominicana y todo el Caribe. Ampliar a la tercera fila se rechazó — añade volumen
y desvía la atención de la región de la que trata el producto, a cambio de una
ganancia para la demo que no lo compensa. El compromiso queda registrado en
`app.config.bbox` y `.env.example` para que el siguiente lector vea el *por qué*,
no solo el qué.

### Resultado de la configuración final (600 s, Golfo + Caribe, 5 tipos)

La puerta anterior usaba la caja estrecha original y cuatro tipos de mensaje. La
configuración realmente desplegada añade `StaticDataReport`, así que se volvió a
ejecutar de extremo a extremo en vez de asumir equivalencia:

| Métrica | Valor |
|---|---|
| Frames capturados | **4 283** |
| Ritmo de mensajes | **7,1 msg/s** |
| Buques únicos | **1 583** (1 422 posicionados, 750 con identidad) |
| Cobertura de identidad de los buques posicionados | **52,7 %** |
| Huecos > 15 s | **0** (máximo 0,0 s) |
| Errores de parseo | **0** |
| Frames duplicados | 0 |

Mezcla de mensajes: `PositionReport` 58,5 %, `StandardClassBPositionReport`
19,5 %, `StaticDataReport` 11,1 %, `ShipStaticData` 11,0 %.

Veredicto: **salida 0, utilizable**. Suscribirse al tipo 24 de AIS casi duplicó
la proporción de buques que llevan identidad — ver §9.

---

## 3. El contrato del provider

```python
class AISProvider(Protocol):
    name: ClassVar[str]
    def samples(self) -> AsyncIterator[Sample]: ...
```

Los adaptadores producen dataclasses ya decodificadas (`PositionSample` /
`StaticSample`). **No** validan, limitan ni persisten — eso vive aguas abajo en
`app.ingestion`, de modo que toda fuente pasa por reglas idénticas.

`None` en una muestra significa *la fuente no reportó este valor*. Nunca se
convierte en cero, y nunca se fabrica.

---

## 4. Buffer, limitación y volcado

```
frame ─► decode ─► throttle(mmsi, POSITION_INTERVAL_MINUTES) ─► buffer
                                        │
                     cada 30 min ───────┘
                                        ▼
              validar ► transformar ► dedup ► insert ► ingestion_runs
```

* La tarea consumidora nunca hace I/O de base de datos — aisstream descarta
  mensajes si las lecturas se estancan, así que decodificar debe seguir siendo
  no bloqueante.
* El límite es **continuo, no por ventana**: su mapa por buque sobrevive a un
  volcado. Borrarlo cuando se vacía el buffer aceptaría el primer mensaje de cada
  buque después de cada volcado sin importar lo reciente que fuera el anterior,
  dando dos puntos más cerca de lo que `POSITION_INTERVAL_MINUTES` permite y
  empujando el recuento de filas por encima del modelo de 144/buque/día
  (`architecture.md` §4). Las entradas de buques en silencio durante el doble del
  intervalo se depuran para que el mapa no crezca sin límite a medida que los
  barcos salen de la región.
* El buffer está limitado por `BUFFER_MAX_MESSAGES`; el desbordamiento expulsa el
  más antiguo con una advertencia registrada en vez de agotar la memoria.
* Los datos estáticos se bufferizan aparte y se hacen *upsert* en `vessels`.

### Ejecución real, antes de que existiera la persistencia (2026-10-06, 150 s)

| | |
|---|---|
| frames decodificados | 1 001 — **0** errores de decodificación, **0** inutilizables, **0** reconexiones |
| posiciones aceptadas por ventana de 30 s | 179, 124, 135, 114, 98 |
| limitadas por ventana | 1, 5, 42, 34, 41 |
| rechazadas / marcadas por la validación | **0 / 0** |

La columna `throttled` ascendente es el límite de tasa arrastrándose entre
volcados. Una ejecución anterior contra un buffer que lo reiniciaba por ventana
informó de `1, 4, 4, 1, 2` y aceptó 856 filas en vez de 665 — tráfico idéntico,
22 % más de filas, y todas las extras dentro de un solo intervalo prometido.

La validación no encontró nada que rechazar ni marcar en tráfico real, que es el
punto: se comprobó contra frames imposibles sintéticos *y* contra el mar. El único
contador que se movió alguna vez fue `unusable`, una vez en una ejecución anterior
— y la suscripción se midió en 0 frames fuera de suscripción sobre 1 260, así que
el nombre no puede achacarse a un desajuste de configuración. Su razón se registra
ahora en DEBUG (§7).

---

## 5. Validación — cuatro clases distintas

Los datos nunca se descartan por estar incompletos.

| Clase | Ejemplos | Acción | Dónde |
|---|---|---|---|
| **inválidos** | latitud fuera de ±90, longitud fuera de ±180, MMSI que no tiene 9 dígitos | rechazado, contado | `ingestion.pipeline`, en el volcado |
| **ausentes** | campo que no aparece en el mensaje (los reportes Clase B no traen `NAVSTAT`) | almacenado como `NULL` | adaptador, al decodificar |
| **desconocidos** | la fuente reportó explícitamente «sin valor» | ver abajo | adaptador, al decodificar |
| **anómalos** | SOG > 60 nudos; un desplazamiento que implica más de 60 nudos en el tiempo disponible | **almacenado**, marcado | `ingestion.pipeline`, en el volcado |

**La clase de valores desconocidos se divide según si el centinela encaja en la
columna.** Solo
`NAVSTAT = 15` («no definido») es un código de estado real, así que se **almacena
como `15`** y la API lo muestra como `UNKNOWN`. Todo lo demás de esta clase
codifica «no disponible» con un valor *fuera* del dominio válido del campo —
`COG = 360`, `TrueHeading = 511`, `SOG = 102.3`, `ROT = -128`, `IMO = 0`,
`Type = 0`, calado `0`, `DEST` vacío — y almacenarlos corrompería los agregados
(`AVG(cog)` arrastrado hacia 360), así que van a **`NULL`**.

Esa conversión ocurre en el adaptador en el momento de la **decodificación**, no
en el volcado: `None` es lo que las dataclasses de muestra ya significan, de modo
que la manera en que el cable escribe «nada» se normaliza antes de que nada
aguas abajo pueda confundirla con datos. Las clases de arriba son lo que queda
para que la validación juzgue.

Un rechazo es absoluto: la fila nunca llega a la base de datos. Una marca señala
datos que se almacenan **completos** — una velocidad implausible sigue siendo
evidencia de que un buque estaba en algún sitio, y ocultarla descartaría
justamente los datos que muestran que el sensor se equivocó.

### Dos entradas que el diseño original listaba

**Rechazo por caer fuera de la caja delimitada** — eliminado. aisstream filtra
sobre las mismas coordenadas que trae el *payload*, de modo que las 200 posiciones
capturadas ya estaban dentro; una comprobación que no puede dispararse es código
muerto, y una que se disparase en el límite descartaría datos legítimos sin
ningún beneficio. La suscripción *es* el filtro de región, y se configura con
`MIN_LAT` … `MAX_LON` en vez de volver a comprobarse aguas abajo.

**Salto de posición > 50 km entre muestras** — implementado, pero no tal y como
se escribió. Un umbral fijo de 50 km es erróneo en ambas direcciones: sobre una
brecha de reporte de seis horas equivale a 4,5 nudos, tráfico normal que se alejó
del alcance de la radio, mientras un teletransporte genuino de 20 km en diez
minutos (65 nudos) cae por debajo. La regla es **velocidad implícita** — distancia
sobre la brecha, medida contra `MAX_PLAUSIBLE_SOG_KNOTS`, el mismo límite físico
que usa la regla de SOG, de modo que la tubería mantiene un número en vez de dos
que hay que mantener en sincronía.

Dos detalles la mantienen honesta:

* **El ancla es la base de datos, no la memoria.** La posición anterior viene de
  `vessel_positions` (`load_previous_positions`), leída una vez por volcado antes
  de juzgar la ventana. Una copia en memoria se reiniciaría en cada arranque, de
  modo que el mismo vuelo anómalo se marcaría o se pasaría por alto según cuándo
  se hubiera redesplegado por última vez el proceso — una marca que miente a
  veces es peor que ninguna. Como el ancla y la primera muestra de la ventana
  están ambas almacenadas, un buque que reporta tres veces en una ventana se
  comprueba tres veces, cada una contra su propio predecesor.
* **Las parejas separadas menos de un minuto no se juzgan.** Las posiciones se
  limitan al llegar, de modo que una pareja por debajo del minuto es sesgo entre
  reporte y llegada y no un viaje; dividir por ella convierte ruido de posición a
  escala de metros en una afirmación sobre nudos en vez de una sobre el buque.

El veredicto aterriza en la fila (`vessel_positions.flags`) y se agrega en
`ingestion_runs.flagged`. Su distancia es la única medida del codebase que PostGIS
no hace: un salto es a menudo entre una posición almacenada y otra que todavía
está en memoria, a lo que ningún SQL alcanza. `haversine_km` la calcula, y un test
de integración la fija a `ST_Distance` para que las dos respuestas no puedan
divergir.

Los recuentos de rechazo se agregan por motivo en `ingestion_runs.rejected` como
JSONB, y las marcas igual en `ingestion_runs.flagged`; ambos van también al log
estructurado al cerrar cada ventana. En la ejecución real de §4 ambos estaban
vacíos.

---

## 6. Idempotencia

`vessel_positions` tiene clave primaria `(mmsi, timestamp)` y los inserts usan
`ON CONFLICT DO NOTHING`. Reproducir una ventana, o reiniciar a mitad de un
volcado, no puede crear duplicados.

`ingestion_runs` **no** se deduplica deliberadamente: dos volcados son dos
eventos, y la segunda fila es la prueba de que hubo un reintento. La escritura
precede a `buffer.clear()`, de modo que un fallo deja la ventana en su sitio para
el siguiente intento en vez de descartarla. Detalle completo en
`docs/data-model.md` §3.

---

## 7. Manejo de errores

| Fallo | Comportamiento |
|---|---|
| Conexión rechazada / fallo del *handshake* | registrado, retroceso exponencial con *jitter*, tope 5 min, reintento infinito |
| Suscripción rechazada | error duro, salida — una clave inválida no debe reintentar en silencio |
| El socket se cierra a mitad de flujo | registrado con motivo, reconectar y resuscribir en 3 s |
| Frame mal formado | contado como `decode_errors`, registrado en DEBUG, no aborta la ventana |
| Frame bien formado sin nada utilizable | contado como `unusable` y registrado en DEBUG con su motivo — un tipo de mensaje fuera del conjunto de decodificación, un tipo AIS 24 parte B con `Valid: false`, o una parte A sin nombre. No es un error: el frame era legible, simplemente no traía nada para nosotros |
| Base de datos no disponible en el volcado (la lectura del ancla o la escritura) | la ventana queda bufferizada y se reintenta, así que el lote nunca se pierde en silencio. **No se escribe fila de `ingestion_runs`**: la fila de la ejecución y las posiciones que cuenta comparten una transacción, y un volcado fallido no debe dejar un registro afirmando que ocurrió. El fallo es el hueco en `window_end` más el registro de error |
| Falla la exportación o la subida en un turno de mantenimiento | registrado, nada borrado, sin fila de `export_runs`, se reintenta en el siguiente turno — deliberadamente **sin** el retroceso del volcado. Un turno de mantenimiento perdido no cuesta nada porque las filas siguen en la tabla, mientras martillear un fallo permanente cada pocos minutos sería ruido y no resiliencia (`architecture.md` §8) |

`/health` expone `last_flush`, `last_ais_message` y `data_freshness_minutes` para
que una tubería parada sea diagnosticable desde fuera.

---

## 8. Redacción

La fuente se consume de forma continua, pero los datos solo son *duraderos* en la
cadencia del volcado. Por eso el producto dice:

* ✅ **Tiempo casi real** (*Near Real-Time*)
* ✅ **Actualizado cada 30 minutos** (*Updated every 30 minutes*)
* ❌ tiempo real, en vivo, instantáneo (*real-time*, *live*, *instant*)

---

## 9. Referencia de campos (verificada contra tráfico real)

Todo lo que sigue se leyó de frames reales en `.captures/`, no de la
documentación. Los nombres difieren de los de AISHub, así que el mapeo aguas
abajo no se debe adivinar.

**Las unidades llegan ya convertidas.** La codificación AIS del cable usa
decímetros para el calado y décimas de nudos para la velocidad; aisstream entrega
metros y nudos (`MaximumStaticDraught = 13.9` en un portacontenedores de 293 m,
`Sog = 9.0` en una región donde 90 nudos serían imposibles). Dividir otra vez por
diez informaría de un calado de 1,4 m en un Panamax — comprobado, no supuesto.

`MetaData` (presente en todo tipo de mensaje):

| Campo | Notas |
|---|---|
| `MMSI` / `MMSI_String` | formas numérica y de cadena del mismo identificador |
| `ShipName` | rellenado con espacios a 20 caracteres, puede venir en blanco |
| `latitude`, `longitude` | posición del buque en la hora del mensaje |
| `time_utc` | marca de tiempo ISO con nanosegundos |

`PositionReport`:

| Campo | Centinela observado | Manejo |
|---|---|---|
| `NavigationalStatus` | vistos `0`, `1`, `3`, `5`; `15` = desconocido | almacenar, mostrar como `UNKNOWN` |
| `Sog` | — | nudos; `> 60` es anómalo |
| `Cog` | **`360` = no disponible** | → `NULL` |
| `TrueHeading` | **`511` = no disponible** | → `NULL` |
| `RateOfTurn` | — | no se muestra, ver §5 |
| `Latitude`, `Longitude` | — | se rechazan si caen fuera de la caja |

`ShipStaticData`:

| Campo | Notas |
|---|---|
| `Name`, `CallSign` | cadenas rellenadas con espacios |
| `ImoNumber` | **no `IMO`**; `0` significa desconocido → `NULL` |
| `Type` | **no `ShipType`**; código numérico AIS de tipo de nave, `0` = no disponible |
| `Dimension` | **anidado** `{A, B, C, D}` en metros — `length = A + B`, `width = C + D`. Un lado a **`0` es significativo** (antena en ese extremo), así que solo un bloque todo a cero significa «sin tamaño» |
| `MaximumStaticDraught` | metros |
| `Destination` | rellenado con espacios; en blanco → `NULL` |
| `Eta` | anidado `{Month, Day, Hour, Minute}` — **no existe campo de año** |

Presencia medida sobre los **470** frames `ShipStaticData` de la ejecución de
configuración final:

```
Name                    470/470     ImoNumber               327/470
CallSign                459/470     Destination             433/470
Type                    462/470     MaximumStaticDraught    419/470
Dimension               455/470
```

`StaticDataReport` (tipo AIS 24 — identidad Clase B):

| Campo | Notas |
|---|---|
| `PartNumber` | `false` = parte A, `true` = parte B — **frames mutuamente excluyentes** |
| `ReportA.Name` / `ReportA.Valid` | la parte A trae *solo* el nombre |
| `ReportB.ShipType` | **no `Type`**, como en el tipo 5; `0` = no disponible → `NULL` |
| `ReportB.CallSign` | rellenado con espacios |
| `ReportB.Dimension` | el mismo `{A,B,C,D}` anidado; un lado a cero se conserva, todo a cero → `NULL` |
| `ReportB.Valid` | `false` = el frame no trae nada utilizable → **ignorarlo**, no fusionarlo |

Este tipo no estaba en la suscripción original. `ShipStaticData` (tipo 5) solo lo
envían los buques Clase A, de modo que sin el tipo 24 todo buque
Clase B quedaría sin nombre y sin tipo — y el Clase B es el 19,5 % del flujo. La
ejecución de arriba mostró 474 frames de tipo 24 frente a 470 de tipo 5, y la
cobertura de identidad de los buques posicionados subiendo del **24,7 % al
52,7 %** una vez añadido. Medido sobre una muestra de 120 s, la parte B (la mitad
que trae tipo y dimensiones) llegó en 16 de 96 frames, con `Valid` en los 16, un
`ShipType` distinto de cero en 16 y dimensiones distintas de cero en 14.

Consecuencias para el producto:

* **El tipo de nave y las dimensiones están disponibles** — se pueden mostrar.
* **La identidad Clase B llega en dos mitades que nunca coexisten**, así que
  `vessels` debe recibir un *upsert* que preserve los nulos
  (`app.ingestion.buffer.merge_static`): un frame de parte A no conoce el tipo de
  nave y no debe borrar uno conocido.
* **La ETA no lleva año.** Solo puede mostrarse como `Month/Day HH:MM` y jamás
  debe promocionarse a fecha inventando el año. V1 la muestra tal cual se reportó
  o no la muestra.
* **No existe ningún evento de llegada o salida en el flujo.** Las escalas de
  puerto se derivan de la traza — ver `docs/congestion.md` — y nunca se leen de un
  campo.
