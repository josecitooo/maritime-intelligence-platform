/**
 * Display formatting for the control room.
 *
 * Timestamps are rendered in UTC because that is what the API stores and
 * returns; showing them in the browser's zone would imply an offset the data
 * does not have. Figures follow `es-ES`, the language the UI speaks.
 */

const UTC_STAMP = new Intl.DateTimeFormat('es-ES', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})

const COUNT = new Intl.NumberFormat('es-ES')

/** `7 oct 12:00` from an ISO-8601 string; `—` when there is no timestamp. */
export function formatUtc(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : UTC_STAMP.format(date)
}

/** `7 min`, `1 h 5 min`, `14 h`; `—` when unknown, `menos de 1 min` below a minute. */
export function formatAge(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—'
  const total = Math.round(minutes)
  if (total < 1) return 'menos de 1 min'
  if (total < 90) return `${total} min`
  const hours = total / 60
  // Whole hours once they get large; a comma-decimal below ten.
  const text = hours < 10 ? hours.toFixed(1).replace('.', ',') : `${Math.round(hours)}`
  return `${text} h`
}

/** `hace 7 min`; `—` when the age is unknown. */
export function formatAgo(minutes: number | null | undefined): string {
  const age = formatAge(minutes)
  return age === '—' ? '—' : `hace ${age}`
}

/** An integer with Spanish grouping: `1284` → `1.284`. */
export function formatCount(value: number): string {
  return COUNT.format(value)
}

/** `[min_lat, max_lat, min_lon, max_lon]` as `8,0°N–31,0°N · 98,0°O–59,0°O`. */
export function formatBbox(bbox: number[] | null | undefined): string | null {
  if (!bbox || bbox.length !== 4) return null
  const [minLat, maxLat, minLon, maxLon] = bbox
  const lat = (value: number) =>
    `${Math.abs(value).toFixed(1).replace('.', ',')}°${value < 0 ? 'S' : 'N'}`
  const lon = (value: number) =>
    `${Math.abs(value).toFixed(1).replace('.', ',')}°${value < 0 ? 'O' : 'E'}`
  return `${lat(minLat)}–${lat(maxLat)} · ${lon(minLon)}–${lon(maxLon)}`
}
