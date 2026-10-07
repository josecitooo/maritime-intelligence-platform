import { describe, expect, it } from 'vitest'
import {
  formatAge,
  formatAgo,
  formatCount,
  formatLatLon,
  formatNavStatus,
  formatShipType,
  formatUtc,
} from './format'

describe('format', () => {
  it('formats UTC timestamps', () => {
    expect(formatUtc('2026-10-07T12:34:00Z')).toMatch('7 oct')
  })
  it('handles missing UTC', () => {
    expect(formatUtc(null)).toBe('—')
  })
  it('formats ages', () => {
    expect(formatAge(0.2)).toBe('menos de 1 min')
    expect(formatAge(7)).toBe('7 min')
    expect(formatAge(150)).toBe('2,5 h')
  })
  it('formats ago', () => {
    expect(formatAgo(30)).toBe('hace 30 min')
  })
  it('formats counts', () => {
    expect(formatCount(1234)).toMatch(/\d/)
  })
  it('formats lat/lon', () => {
    expect(formatLatLon(18.4717, -69.93)).toContain('N')
    expect(formatLatLon(18.4717, -69.93)).toContain('O')
  })
  it('formats nav status', () => {
    expect(formatNavStatus(1)).toBe('Fondeado')
    expect(formatNavStatus(5)).toBe('Amarrado')
    expect(formatNavStatus(null)).toBe('—')
    expect(formatNavStatus(99)).toBe('Código 99')
  })
  it('formats ship type', () => {
    expect(formatShipType(null)).toBe('Sin tipo reportado')
    expect(formatShipType(70)).toBe('Carga')
    expect(formatShipType(89)).toBe('Cisterna')
    expect(formatShipType(30)).toBe('Pesquero')
    expect(formatShipType(99)).toBe('Otro')
  })
})