<template>
  <figure class="trend">
    <figcaption>
      <strong>{{ title }}</strong>
      <span class="last">{{ lastText }}</span>
    </figcaption>
    <p v-if="valid.length === 0" class="empty">No data in this range</p>
    <svg v-else :viewBox="`0 0 ${W} ${H}`" role="img" :aria-label="`${title}: ${lastText}`">
      <line :x1="PAD_L" :y1="PAD_T" :x2="PAD_L" :y2="H - PAD_B" class="axis" />
      <line :x1="PAD_L" :y1="H - PAD_B" :x2="W - PAD_R" :y2="H - PAD_B" class="axis" />
      <text :x="PAD_L - 4" :y="PAD_T + 4" class="label" text-anchor="end">{{ yLabel(hi) }}</text>
      <text :x="PAD_L - 4" :y="H - PAD_B" class="label" text-anchor="end">{{ yLabel(lo) }}</text>
      <text :x="PAD_L" :y="H - 4" class="label" text-anchor="start">{{ timeLabel(t0) }}</text>
      <text :x="W - PAD_R" :y="H - 4" class="label" text-anchor="end">{{ timeLabel(t1) }}</text>
      <polyline v-for="(seg, i) in lines" :key="`l${i}`" :points="seg" fill="none" :stroke="color" stroke-width="2" />
      <circle v-for="(dot, i) in dots" :key="`d${i}`" :cx="dot[0]" :cy="dot[1]" r="2.5" :fill="color" />
    </svg>
  </figure>
</template>

<script>
const W = 400
const H = 130
const PAD_L = 42
const PAD_R = 8
const PAD_T = 8
const PAD_B = 20

export default {
  name: "LibreSolarBmsTrendChart",
  props: {
    // [{ t: ISO string, v: number | null }] oldest first; null = no reading (drawn as a gap)
    points: { type: Array, default: () => [] },
    title: { type: String, required: true },
    unit: { type: String, default: "" },
    digits: { type: Number, default: 1 },
    color: { type: String, default: "#2e7d32" },
  },
  data() {
    return { W, H, PAD_L, PAD_R, PAD_T, PAD_B }
  },
  computed: {
    valid() {
      return this.points.filter((p) => p.v !== null && p.v !== undefined && !Number.isNaN(Number(p.v)))
    },
    t0() {
      return this.valid.length ? new Date(this.valid[0].t).getTime() : 0
    },
    t1() {
      return this.valid.length ? new Date(this.valid[this.valid.length - 1].t).getTime() : 0
    },
    hi() {
      return Math.max(...this.valid.map((p) => Number(p.v)))
    },
    lo() {
      const lo = Math.min(...this.valid.map((p) => Number(p.v)))
      return lo === this.hi ? lo - 1 : lo // a flat line still needs a vertical span
    },
    xy() {
      const span = this.t1 - this.t0
      const yspan = this.hi - this.lo || 1
      const width = W - PAD_L - PAD_R
      return this.points.map((p) => {
        if (p.v === null || p.v === undefined || Number.isNaN(Number(p.v))) return null
        const x = span === 0 ? PAD_L + width / 2 : PAD_L + ((new Date(p.t).getTime() - this.t0) / span) * width
        const y = PAD_T + (1 - (Number(p.v) - this.lo) / yspan) * (H - PAD_T - PAD_B)
        return [Math.round(x * 10) / 10, Math.round(y * 10) / 10]
      })
    },
    segments() {
      const out = []
      let current = []
      for (const point of this.xy) {
        if (point === null) {
          if (current.length) out.push(current)
          current = []
        } else {
          current.push(point)
        }
      }
      if (current.length) out.push(current)
      return out
    },
    lines() {
      return this.segments.filter((s) => s.length > 1).map((s) => s.map((p) => p.join(",")).join(" "))
    },
    dots() {
      return this.segments.filter((s) => s.length === 1).map((s) => s[0])
    },
    lastText() {
      if (!this.valid.length) return "-"
      return this.yLabel(Number(this.valid[this.valid.length - 1].v))
    },
  },
  methods: {
    yLabel(v) {
      return Number(v).toFixed(this.digits) + this.unit
    },
    timeLabel(ms) {
      const d = new Date(ms)
      return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    },
  },
}
</script>

<style lang="scss" scoped>
.trend {
  margin: 0;
  background: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  figcaption {
    display: flex;
    justify-content: space-between;
  }
}
.last {
  color: #555;
}
.empty {
  color: #888;
  margin: 24px 0;
  text-align: center;
}
svg {
  width: 100%;
  height: auto;
}
.axis {
  stroke: #ccc;
  stroke-width: 1;
}
.label {
  fill: #888;
  font-size: 9px;
}
</style>
