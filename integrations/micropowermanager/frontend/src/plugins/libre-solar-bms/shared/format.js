export function fmtNumber(value, digits, unit = "") {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "-"
  return Number(value).toFixed(digits) + unit
}

export function fmtTime(iso) {
  if (!iso) return "-"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleString()
}

export function agoText(iso) {
  if (!iso) return "never"
  const m = (Date.now() - new Date(iso).getTime()) / 60000
  if (Number.isNaN(m)) return "never"
  if (m < 1) return "just now"
  if (m < 60) return `${Math.floor(m)} min ago`
  if (m < 1440) return `${Math.floor(m / 60)} h ago`
  return `${Math.floor(m / 1440)} d ago`
}

export const PRESETS = [
  { id: "24h", label: "Last 24 hours", ms: 24 * 3600 * 1000 },
  { id: "7d", label: "Last 7 days", ms: 7 * 24 * 3600 * 1000 },
  { id: "30d", label: "Last 30 days", ms: 30 * 24 * 3600 * 1000 },
  { id: "custom", label: "Custom", ms: null },
]

/**
 * Turns the time-range control's value into API query parameters (ISO strings, undefined = open end).
 * Presets are resolved against "now" at call time, so a polling reload keeps the window moving.
 */
export function resolveRange(range, now = Date.now()) {
  const preset = PRESETS.find((p) => p.id === range.preset)
  if (preset && preset.ms) {
    return { from: new Date(now - preset.ms).toISOString(), to: undefined }
  }
  const iso = (value) => {
    if (!value) return undefined
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? undefined : d.toISOString()
  }
  return { from: iso(range.from), to: iso(range.to) }
}
