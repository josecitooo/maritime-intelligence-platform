import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import coastline from '../data/ne_110m_coastline.json'

/**
 * FASE 8: the world itself — a sphere of sea with the Natural Earth 110 m
 * coastline drawn over it, framed on the queried bounding box.
 *
 * The coastlines are `LineString`s, which is why they are drawn as line
 * segments on the sphere rather than filled: filling a geographic polygon on a
 * sphere needs triangulation this project does not have a reason to carry, and
 * an unlit line is what a control room draws anyway. Depth does the rest — the
 * sea sphere hides whatever is on the far side.
 *
 * Nothing here claims freshness: the globe is geography, and FASE 9 is what
 * will put positions on it.
 *
 * Data: Natural Earth 1:110m coastline, public domain (naturalearthdata.com),
 * versioned in `src/data/` so the build never needs the network.
 */
export function Globe() {
  const hostRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()

    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100)
    // Look at the region the API is asked about, not at 0°N 0°E.
    const home = toXYZ(HOME_LON, HOME_LAT, 3.1)
    camera.position.set(home[0], home[1], home[2])

    const oceanGeometry = new THREE.SphereGeometry(RADIUS, 64, 48)
    const oceanMaterial = new THREE.MeshLambertMaterial({ color: OCEAN })
    scene.add(new THREE.Mesh(oceanGeometry, oceanMaterial))

    const coastGeometry = buildCoastline()
    const coastMaterial = new THREE.LineBasicMaterial({ color: COAST })
    scene.add(new THREE.LineSegments(coastGeometry, coastMaterial))

    // Lit from the camera's side so the face we are looking at is the lit one.
    scene.add(new THREE.AmbientLight(0xffffff, 0.65))
    const sun = new THREE.DirectionalLight(0xffffff, 1.3)
    sun.position.set(home[0], home[1], home[2])
    scene.add(sun)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.enablePan = false
    controls.rotateSpeed = 0.45
    controls.minDistance = 1.6
    controls.maxDistance = 6
    controls.update()

    const resize = () => {
      const width = host.clientWidth
      const height = host.clientHeight
      if (!width || !height) return
      // `false`: CSS owns the canvas box, the renderer owns its backing store.
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(host)
    resize()

    let frame = 0
    const tick = () => {
      controls.update()
      renderer.render(scene, camera)
      frame = requestAnimationFrame(tick)
    }
    tick()

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      controls.dispose()
      coastGeometry.dispose()
      coastMaterial.dispose()
      oceanGeometry.dispose()
      oceanMaterial.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [])

  return <div className="globe" ref={hostRef} />
}

/** Sea, one step bluer than `--bg` (#0a0d11) so the sphere reads as water. */
const OCEAN = 0x0e1620

/** Coastline, `--accent` (#4fa3b0): the theme's low-chroma teal. */
const COAST = 0x4fa3b0

const RADIUS = 1

/** The mid-point of the bounding box the API is queried with. */
const HOME_LON = -78.5
const HOME_LAT = 19.5

/**
 * Geographic coordinates onto the sphere, equirectangular → xyz.
 *
 * The minus sign on z keeps longitude increasing eastward while the scene
 * keeps three.js' right-handed axes; the whole dataset is transformed with
 * this one function, so it only has to be self-consistent.
 */
function toXYZ(lon: number, lat: number, radius: number): [number, number, number] {
  const latR = (lat * Math.PI) / 180
  const lonR = (lon * Math.PI) / 180
  const ring = Math.cos(latR)
  return [radius * ring * Math.cos(lonR), radius * Math.sin(latR), -radius * ring * Math.sin(lonR)]
}

/** Every segment of every coastline as one buffer: one draw call for all of it. */
function buildCoastline(): THREE.BufferGeometry {
  const positions: number[] = []
  // A hair above the sea: exactly coplanar segments fight the sphere for the
  // depth buffer and flicker.
  const radius = RADIUS * 1.003

  for (const feature of coastline.features) {
    const ring = feature.geometry.coordinates
    for (let i = 1; i < ring.length; i++) {
      const from = toXYZ(ring[i - 1][0], ring[i - 1][1], radius)
      const to = toXYZ(ring[i][0], ring[i][1], radius)
      positions.push(from[0], from[1], from[2], to[0], to[1], to[2])
    }
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  return geometry
}
