import { describe, expect, it } from 'vitest'
import { displayKampala, kampalaToUtc } from './appointmentTime'

describe('Africa/Kampala appointment timestamps', () => {
  it('converts entered Kampala wall time to UTC without using the browser timezone', () => {
    expect(kampalaToUtc('2027-06-15T09:30')).toBe('2027-06-15T06:30:00.000Z')
    expect(kampalaToUtc('2027-01-01T01:30')).toBe('2026-12-31T22:30:00.000Z')
  })
  it('always labels the displayed local zone', () => {
    const displayed = displayKampala('2027-06-15T06:30:00Z')
    expect(displayed).toContain('09:30')
    expect(displayed).toContain('Africa/Kampala')
  })
  it.each(['bad', '2027-02-29T09:00', '2027-04-31T12:00', '2027-06-15', '2027-06-15T25:00'])(
    'rejects malformed or impossible wall time %s', (value) => expect(() => kampalaToUtc(value)).toThrow(),
  )
})
