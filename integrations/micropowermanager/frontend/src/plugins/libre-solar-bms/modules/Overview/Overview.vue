<template>
  <div class="lsb-page">
    <md-card>
      <md-card-header>
        <h4 class="title">Battery health</h4>
        <p class="category">
          Libre Solar BMS batteries reported by the monitoring gateway. Refreshes every 30 seconds.
        </p>
      </md-card-header>
      <md-card-content>
        <p v-if="error" class="error">{{ error }}</p>
        <p v-else-if="loaded && batteries.length === 0">
          No batteries yet. Start the mpm-ingestor service with an API key from Settings, API keys.
        </p>
        <table v-else-if="batteries.length" class="lsb-table">
          <thead>
            <tr>
              <th>Battery</th>
              <th>Health</th>
              <th>Charge</th>
              <th>Capacity left</th>
              <th>Cycles</th>
              <th>Max temp</th>
              <th>Imbalance</th>
              <th>Faults</th>
              <th>Last report</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in batteries" :key="b.id" class="clickable" @click="open(b)">
              <td>
                <strong>{{ b.name }}</strong>
                <div class="muted">{{ b.device_id }}</div>
              </td>
              <td><health-badge :health="b.health" /></td>
              <td>{{ fmt(b.soc_pct, 0, "%") }}</td>
              <td>{{ fmt(b.soh_pct, 0, "%") }}</td>
              <td>{{ fmt(b.cycles, 0) }}</td>
              <td>{{ fmt(b.max_temp_c, 1, " °C") }}</td>
              <td>{{ b.imbalance_v === null ? "-" : Math.round(b.imbalance_v * 1000) + " mV" }}</td>
              <td>{{ (b.active_faults || []).length }}</td>
              <td :class="{ stale: isStale(b) }">{{ ago(b.last_seen_at) }}</td>
            </tr>
          </tbody>
        </table>
      </md-card-content>
    </md-card>
  </div>
</template>

<script>
import BatteryRepository from "../../repositories/BatteryRepository.js"
import HealthBadge from "../../shared/HealthBadge.vue"

const POLL_MS = 30000
const STALE_MIN = 5

export default {
  name: "LibreSolarBmsOverview",
  components: { HealthBadge },
  data() {
    return { batteries: [], loaded: false, error: "", timer: null }
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
      try {
        const response = await BatteryRepository.list()
        this.batteries = response.data.data
        this.error = ""
      } catch (e) {
        this.error = "Could not load batteries."
      }
      this.loaded = true
    },
    open(battery) {
      this.$router.push(`/libre-solar-bms/batteries/${battery.id}`)
    },
    fmt(value, digits, unit = "") {
      return value === null || value === undefined ? "-" : Number(value).toFixed(digits) + unit
    },
    minutesSince(ts) {
      return ts ? (Date.now() - new Date(ts).getTime()) / 60000 : null
    },
    isStale(battery) {
      const m = this.minutesSince(battery.last_seen_at)
      return m === null || m > STALE_MIN
    },
    ago(ts) {
      const m = this.minutesSince(ts)
      if (m === null) return "never"
      if (m < 1) return "just now"
      if (m < 60) return `${Math.floor(m)} min ago`
      if (m < 1440) return `${Math.floor(m / 60)} h ago`
      return `${Math.floor(m / 1440)} d ago`
    },
  },
}
</script>

<style lang="scss" scoped>
.lsb-page {
  margin-top: 1rem;
}
.lsb-table {
  width: 100%;
  border-collapse: collapse;
  th,
  td {
    text-align: left;
    padding: 10px 8px;
    border-bottom: 1px solid #eee;
  }
}
.clickable {
  cursor: pointer;
  &:hover {
    background: #fafafa;
  }
}
.muted {
  color: #888;
  font-size: 0.8rem;
}
.stale {
  color: #b71c1c;
}
.error {
  color: #b71c1c;
}
</style>
