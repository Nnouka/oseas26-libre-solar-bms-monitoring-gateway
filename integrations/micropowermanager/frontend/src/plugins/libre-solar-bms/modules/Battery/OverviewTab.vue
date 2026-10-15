<template>
  <div>
    <div class="tiles">
      <div v-for="t in tiles" :key="t.label" class="tile">
        <div class="muted">{{ t.label }}</div>
        <div class="value">{{ t.value }}</div>
      </div>
    </div>

    <div class="faults">
      <span class="muted">Active faults</span>
      <span v-if="!(battery.active_faults || []).length" class="chip chip-none">None</span>
      <span v-for="f in battery.active_faults" :key="f" class="chip chip-fault">{{ f }}</span>
    </div>

    <div class="range-row">
      <time-range v-model="range" />
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div class="charts">
      <trend-chart title="State of charge" unit=" %" :digits="0" color="#2e7d32" :points="series.soc" />
      <trend-chart title="Pack voltage" unit=" V" :digits="2" color="#1565c0" :points="series.voltage" />
      <trend-chart title="Max temperature" unit=" °C" :digits="1" color="#ef6c00" :points="series.temp" />
      <trend-chart title="Cell imbalance" unit=" mV" :digits="0" color="#6a1b9a" :points="series.imbalance" />
    </div>
  </div>
</template>

<script>
import BatteryRepository from "../../repositories/BatteryRepository.js"
import TimeRange from "../../shared/TimeRange.vue"
import TrendChart from "../../shared/TrendChart.vue"
import { agoText, fmtNumber, fmtTime, resolveRange } from "../../shared/format.js"

const POLL_MS = 30000
const BUCKETS = 96

export default {
  name: "LibreSolarBmsOverviewTab",
  components: { TimeRange, TrendChart },
  props: {
    battery: { type: Object, required: true },
  },
  data() {
    return {
      range: { preset: "24h", from: "", to: "" },
      series: { soc: [], voltage: [], temp: [], imbalance: [] },
      error: "",
      timer: null,
      requestId: 0,
    }
  },
  computed: {
    tiles() {
      const b = this.battery
      return [
        { label: "State of charge", value: fmtNumber(b.soc_pct, 0, " %") },
        { label: "Capacity left (estimate)", value: fmtNumber(b.soh_pct, 0, " %") },
        { label: "Charge cycles", value: fmtNumber(b.cycles, 0) },
        { label: "Pack voltage", value: fmtNumber(b.pack_voltage_v, 2, " V") },
        { label: "Max temperature", value: fmtNumber(b.max_temp_c, 1, " °C") },
        {
          label: "Cell imbalance",
          value: b.imbalance_v === null || b.imbalance_v === undefined ? "-" : Math.round(b.imbalance_v * 1000) + " mV",
        },
        { label: "State", value: b.state || "-" },
        { label: "Last report", value: b.last_seen_at ? `${fmtTime(b.last_seen_at)} (${agoText(b.last_seen_at)})` : "never" },
      ]
    },
  },
  watch: {
    range: {
      deep: true,
      handler() {
        this.load()
      },
    },
  },
  mounted() {
    this.load()
    this.timer = setInterval(this.load, POLL_MS)
  },
  beforeDestroy() {
    clearInterval(this.timer)
  },
  methods: {
    async load() {
      const id = ++this.requestId
      const { from, to } = resolveRange(this.range)
      try {
        const response = await BatteryRepository.trend(this.battery.id, { from, to, buckets: BUCKETS })
        if (id !== this.requestId) return // a newer request is in flight: drop this reply
        const rows = response.data.data
        const pick = (key, scale = 1) =>
          rows.map((r) => ({ t: r.t, v: r[key] === null || r[key] === undefined ? null : Number(r[key]) * scale }))
        this.series = {
          soc: pick("soc_pct"),
          voltage: pick("pack_voltage_v"),
          temp: pick("max_temp_c"),
          imbalance: pick("imbalance_v", 1000),
        }
        this.error = ""
      } catch (e) {
        if (id !== this.requestId) return
        this.error = "Could not load the trends."
      }
    },
  },
}
</script>

<style lang="scss" scoped>
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  margin: 12px 0;
}
.tile {
  background: #fff;
  padding: 12px 16px;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  .value {
    font-size: 1.2rem;
    font-weight: 500;
  }
}
.muted {
  color: #888;
  font-size: 0.8rem;
}
.faults {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 8px 0 16px;
}
.chip {
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.85rem;
}
.chip-none {
  background: #e8f5e9;
  color: #2e7d32;
}
.chip-fault {
  background: #ffebee;
  color: #b71c1c;
}
.range-row {
  margin: 8px 0 12px;
}
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}
.error {
  color: #b71c1c;
}
</style>
