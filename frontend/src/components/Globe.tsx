import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import coastline from '../data/ne_110m_coastline.json'
import type { PositionLatest, Region } from '../api/types'

/**
 * FASE 8 + 9: the world itself (sphere + Natural Earth coastlines) with the
 * fleet rendered on it.
 *
 * The globe is framed on the *enabled region set* — which is any set the
 * operator picked, anywhere on Earth — and the camera flies to a region when
 * the selector asks it to. The ocean sphere hides the far side, so the whole
 * world is browseable just by dragging.
 *
 * Vessels are one `InstancedMesh` (one draw call for the fleet): each hull is
 * an elongated box oriented by its reported heading and placed at its
 * reported position, slightly above the coastlines. Between data windows the
 * fleet glides from the previous snapshot to the new one — a short visual
 * ease, not an extrapolation, and never a claim of live tracking. Clicking a
 * hull picks the vessel for the inspection panel.
 *
 * Data: positions from `/positions/latest`; coastlines Natural Earth 1:110m,
 * public domain (naturalearthdata.com), versioned in `src/data/`.
 */

/** A camera flight request issued by the region selector. */
export interface GlobeFocus {
  /** Changes on every flight so the effect can retrigger. */
  key: string
  kind: 'world' | 'region'
  /** The region to frame when `kind` is `'region'`. */
  region?: Region
}

export interface GlobeProps {
  /** The whole region catalog; enabled rows decide the initial framing. */
  regions: Region[] | undefined
  /** The vessels to draw. */
  rows: PositionLatest[]
  /** MMSI of the vessel the inspection panel shows; highlighted on the globe. */
  selectedMmsi: number | null
  onSelectVessel: (mmsi: number | null) => void
  /** A flight request; flies the camera to it when it changes. */
  focus: GlobeFocus | null
}

const OCEAN = 0x0e1620
const COAST = 0x4fa3b0
const SHIP = 0x9fd4dc
const SHIP_SELECTED = 0xffd27a

// Shared `Color` instances: `InstancedMesh.setColorAt` takes a `Color`, and
// the per-frame colour pass must not allocate one per hull.
const SHIP_COLOR = new THREE.Color(SHIP)
const SHIP_SELECTED_COLOR = new THREE.Color(SHIP_SELECTED)

const RADIUS = 1
const COAST_RADIUS = RADIUS * 1.003
const SHIP_RADIUS = RADIUS * 1.006

/** How many hulls the instanced mesh is allocated for — the API's own cap. */
const MAX_SHIPS = 2000

/** How long a snapshot glides into its successor, in milliseconds. */
const CONVERGE_MS = 2200

/** Camera flight duration in seconds. */
const FLY_SECONDS = 1.4

/** The farthest useful frame — the whole globe in view. */
const WORLD_DISTANCE = 3.4

/** Where a world overview looks: mid-Atlantic, enough of every coastline. */
const WORLD_LON = -40
const WORLD_LAT = 15

const clamp = (value: number, low: number, high: number): number =>
  Math.min(high, Math.max(low, value))

/**
 * Camera distance that frames a box with the given angular span (degrees).
 * Logarithmic in area: a harbour-scale box lands close, a continent lands far.
 */
function fitDistance(latSpanDeg: number, lonSpanDeg: number): number {
  const area = Math.max(1, latSpanDeg * lonSpanDeg)
  return clamp(2.15 - Math.log10(area) * 0.42, 1.55, WORLD_DISTANCE)
}

function enabledExtent(regions: Region[]): { lon: number; lat: number; distance: number } | null {
  const on = regions.filter((region) => region.enabled)
  if (on.length === 0) return null
  const minLat = Math.min(...on.map((r) => r.min_lat))
  const maxLat = Math.max(...on.map((r) => r.max_lat))
  const minLon = Math.min(...on.map((r) => r.min_lon))
  const maxLon = Math.max(...on.map((r) => r.max_lon))
  return {
    lon: (minLon + maxLon) / 2,
    lat: (minLat + maxLat) / 2,
    distance: fitDistance(maxLat - minLat, maxLon - minLon),
  }
}

/** Geographic coordinates onto the sphere, equirectangular → xyz. */
function toXYZ(lon: number, lat: number, radius: number): [number, number, number] {
  const latR = (lat * Math.PI) / 180
  const lonR = (lon * Math.PI) / 180
  const ring = Math.cos(latR)
  return [radius * ring * Math.cos(lonR), radius * Math.sin(latR), -radius * ring * Math.sin(lonR)]
}

/** Every segment of every coastline as one buffer: one draw call for all of it. */
function buildCoastline(): THREE.BufferGeometry {
  const positions: number[] = []
  for (const feature of coastline.features) {
    const ring = feature.geometry.coordinates
    for (let i = 1; i < ring.length; i++) {
      const from = toXYZ(ring[i - 1][0], ring[i - 1][1], COAST_RADIUS)
      const to = toXYZ(ring[i][0], ring[i][1], COAST_RADIUS)
      positions.push(from[0], from[1], from[2], to[0], to[1], to[2])
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  return geometry
}

// Scratch vectors — the per-frame matrix builder must not allocate.
const _up = new THREE.Vector3()
const _east = new THREE.Vector3()
const _north = new THREE.Vector3()
const _forward = new THREE.Vector3()
const _xAxis = new THREE.Vector3()
const _basis = new THREE.Matrix4()
const _quat = new THREE.Quaternion()
const _pos = new THREE.Vector3()
const _scale = new THREE.Vector3()
const _Z = new THREE.Vector3(0, 0, 1)

/**
 * Write the instance matrix for one hull: placed at the fix, "up" along the
 * sphere's normal, "forward" along the heading (degrees clockwise from north).
 */
function setVesselMatrix(out: THREE.Matrix4, lon: number, lat: number, headingDeg: number, scale: number): void {
  _up.set(...toXYZ(lon, lat, 1)).normalize()
  _east.crossVectors(_Z, _up)
  if (_east.lengthSq() < 1e-8) _east.set(0, -1, 0) // exactly at a pole
  _east.normalize()
  _north.crossVectors(_up, _east).normalize()
  const heading = (headingDeg * Math.PI) / 180
  _forward.copy(_north).multiplyScalar(Math.cos(heading)).addScaledVector(_east, Math.sin(heading))
  // Right-handed basis with +z = forward: x = forward × up.
  _xAxis.crossVectors(_forward, _up).normalize()
  _basis.makeBasis(_xAxis, _up, _forward)
  _quat.setFromRotationMatrix(_basis)
  _pos.set(...toXYZ(lon, lat, SHIP_RADIUS))
  _scale.set(scale, scale, scale)
  out.compose(_pos, _quat, _scale)
}

const smoothstep = (t: number): number => t * t * (3 - 2 * t)

type ShipFix = { mmsi: number; latitude: number; longitude: number; heading: number }

export function Globe({ regions, rows, selectedMmsi, onSelectVessel, focus }: GlobeProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const rowsRef = useRef(rows)
  const selectedRef = useRef<number | null>(selectedMmsi)
  const onSelectRef = useRef(onSelectVessel)

  rowsRef.current = rows
  selectedRef.current = selectedMmsi
  onSelectRef.current = onSelectVessel

  // The setup effect runs once; the imperative bits it owns (camera flights,
  // initial framing) are reached through this handle so prop effects can
  // trigger them without rebuilding the scene. `regions` changes identity on
  // every poll, so the effect must not depend on it.
  const apiRef = useRef<{
    frameInitial: (regions: Region[] | undefined) => void
    flyTo: (lon: number, lat: number, distance: number) => void
  } | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
    camera.position.set(...toXYZ(WORLD_LON, WORLD_LAT, WORLD_DISTANCE))

    const oceanGeometry = new THREE.SphereGeometry(RADIUS, 64, 48)
    const oceanMaterial = new THREE.MeshLambertMaterial({ color: OCEAN })
    scene.add(new THREE.Mesh(oceanGeometry, oceanMaterial))

    const coastGeometry = buildCoastline()
    const coastMaterial = new THREE.LineBasicMaterial({ color: COAST })
    scene.add(new THREE.LineSegments(coastGeometry, coastMaterial))

    const hullGeometry = new THREE.BoxGeometry(0.02, 0.0045, 0.034)
    const hullMaterial = new THREE.MeshLambertMaterial({ color: SHIP })
    const vessels = new THREE.InstancedMesh(hullGeometry, hullMaterial, MAX_SHIPS)
    vessels.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    vessels.count = 0
    scene.add(vessels)

    scene.add(new THREE.AmbientLight(0xffffff, 0.65))
    const sun = new THREE.DirectionalLight(0xffffff, 1.3)
    sun.position.set(30, 20, -25)
    scene.add(sun)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.enablePan = false
    controls.rotateSpeed = 0.45
    controls.minDistance = 1.35
    controls.maxDistance = WORLD_DISTANCE + 0.2
    controls.update()

    // ── Initial frame: the enabled region set, or the world if none. ──
    let framedOnce = false
    const frameInitial = (catalog: Region[] | undefined) => {
      if (framedOnce || !catalog) return
      framedOnce = true
      const extent = enabledExtent(catalog)
      if (extent) camera.position.set(...toXYZ(extent.lon, extent.lat, extent.distance))
      controls.update()
    }

    // ── Camera flights. A later request retargets one still in flight. ──
    let tween: { from: THREE.Vector3; to: THREE.Vector3; start: number } | null = null
    const flyTo = (lon: number, lat: number, distance: number) => {
      const to = new THREE.Vector3(...toXYZ(lon, lat, distance))
      if (tween) {
        tween.from.copy(camera.position)
        tween.to.copy(to)
        tween.start = performance.now()
        return
      }
      tween = { from: camera.position.clone(), to, start: performance.now() }
    }

    apiRef.current = { frameInitial, flyTo }

    // ── The fleet: snapshots, glides, colour. ──
    const ships = {
      prev: new Map<number, ShipFix>(),
      curr: new Map<number, ShipFix>(),
      animStart: 0,
      animating: false,
    }
    let snapshot = rowsRef.current
    let colorDirty = true

    const setSnapshot = () => {
      const next = rowsRef.current
      if (next === snapshot) return
      ships.prev = ships.curr
      ships.curr = new Map(
        next.map((row) => [
          row.mmsi,
          {
            mmsi: row.mmsi,
            latitude: row.latitude,
            longitude: row.longitude,
            heading: row.heading ?? row.cog ?? 0,
          },
        ]),
      )
      ships.animStart = performance.now()
      ships.animating = ships.prev.size > 0 && ships.curr.size > 0
      snapshot = next
      colorDirty = true
    }

    const applyColors = () => {
      const current = rowsRef.current
      const selected = selectedRef.current
      for (let i = 0; i < Math.min(current.length, MAX_SHIPS); i++) {
        vessels.setColorAt(i, current[i].mmsi === selected ? SHIP_SELECTED_COLOR : SHIP_COLOR)
      }
      if (vessels.instanceColor) vessels.instanceColor.needsUpdate = true
      colorDirty = false
    }

    const matrix = new THREE.Matrix4()
    const renderShips = () => {
      const current = rowsRef.current
      const count = Math.min(current.length, MAX_SHIPS)
      vessels.count = count
      if (count === 0) return
      if (colorDirty) applyColors()

      let t = 1
      if (ships.animating) {
        t = Math.min(1, (performance.now() - ships.animStart) / CONVERGE_MS)
        if (t >= 1) ships.animating = false
        t = smoothstep(t)
      }
      const selected = selectedRef.current
      for (let i = 0; i < count; i++) {
        const row = current[i]
        let latitude = row.latitude
        let longitude = row.longitude
        if (t < 1) {
          const from = ships.prev.get(row.mmsi)
          if (from) {
            latitude = from.latitude + t * (row.latitude - from.latitude)
            longitude = from.longitude + t * (row.longitude - from.longitude)
          }
        }
        const heading = row.heading ?? row.cog ?? 0
        setVesselMatrix(matrix, longitude, latitude, heading, row.mmsi === selected ? 1.7 : 1)
        vessels.setMatrixAt(i, matrix)
      }
      vessels.instanceMatrix.needsUpdate = true
    }

    // ── Picking: a drag orbits, a settled click selects. ──
    const raycaster = new THREE.Raycaster()
    raycaster.far = 12
    const pointer = new THREE.Vector2()
    let grabX = 0
    let grabY = 0

    const onPointerDown = (event: PointerEvent) => {
      grabX = event.clientX
      grabY = event.clientY
    }
    const onPointerUp = (event: PointerEvent) => {
      if (Math.abs(event.clientX - grabX) > 4 || Math.abs(event.clientY - grabY) > 4) return
      const rect = renderer.domElement.getBoundingClientRect()
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
      raycaster.setFromCamera(pointer, camera)
      const hits = raycaster.intersectObject(vessels, false)
      const hit = hits.length > 0 ? hits[0].instanceId : undefined
      onSelectRef.current(hit !== undefined ? rowsRef.current[hit]?.mmsi ?? null : null)
    }
    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointerup', onPointerUp)

    const resize = () => {
      const width = host.clientWidth
      const height = host.clientHeight
      if (!width || !height) return
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(host)
    resize()

    let frame = 0
    const tick = () => {
      setSnapshot()
      if (tween) {
        const k = Math.min(1, (performance.now() - tween.start) / (FLY_SECONDS * 1000))
        const eased = k * k * (3 - 2 * k)
        camera.position.lerpVectors(tween.from, tween.to, eased)
        if (k >= 1) tween = null
      }
      renderShips()
      controls.update()
      renderer.render(scene, camera)
      frame = requestAnimationFrame(tick)
    }
    tick()

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      renderer.domElement.removeEventListener('pointerup', onPointerUp)
      controls.dispose()
      vessels.dispose()
      hullGeometry.dispose()
      hullMaterial.dispose()
      coastGeometry.dispose()
      coastMaterial.dispose()
      oceanGeometry.dispose()
      oceanMaterial.dispose()
      renderer.dispose()
      renderer.domElement.remove()
      apiRef.current = null
    }
  }, [])

  // Frame on the enabled set once the catalog arrives — and only once, so the
  // 30 s poll cannot yank the camera back to the start.
  useEffect(() => {
    apiRef.current?.frameInitial(regions)
  }, [regions])

  // Fly when the selector issues a request.
  useEffect(() => {
    const api = apiRef.current
    if (!api || !focus) return
    if (focus.kind === 'world' || !focus.region) {
      api.flyTo(WORLD_LON, WORLD_LAT, WORLD_DISTANCE)
    } else {
      const { region } = focus
      api.flyTo(
        (region.min_lon + region.max_lon) / 2,
        (region.min_lat + region.max_lat) / 2,
        fitDistance(region.max_lat - region.min_lat, region.max_lon - region.min_lon),
      )
    }
  }, [focus])

  return <div className="globe" ref={hostRef} />
}