<template>
  <div class="lsb-page">
    <p v-if="error" class="error">{{ error }}</p>
    <template v-else-if="battery">
      <md-card :class="['banner', `banner-${battery.health}`]">
        <md-card-content>
          <div class="banner-head">
            <h3>{{ battery.name }}</h3>
            <health-badge :health="battery.health" />
            <span class="score">Score {{ battery.health_score === null ? "-" : battery.health_score }} / 100</span>
          </div>
          <ul v-if="(battery.health_reasons || []).length" class="reasons">
            <li v-for="r in battery.health_reasons" :key="r">{{ r }}</li>
          </ul>
          <p v-else>No problems detected.</p>
        </md-card-content>
      </md-card>

      <div class="tiles">
        <div v-for="t in tiles" :key="t.label" class="tile">
          <div class="muted">{{ t.label }}</div>
          <div class="value">{{ t.value }}</div>
        </div>
      </div>

      <md-card>
        <md-card-header>
          <h4 class="title">Fault log</h4>
        </md-card-header>
        <md-card-content>
          <p v-if="events.length === 0">No events recorded.</p>
          <table v-else class="lsb-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Severity</th>
                <th>Event</th>
                <th>Recommended action</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="e in events" :key="e.id">
                <td>{{ new Date(e.occurred_at).toLocaleString() }}</td>
                <td>
                  <span :class="['sev', `sev-${e.severity}`]">{{ e.severity }}</span>
                </td>
                <td>{{ e.message }}</td>
                <td>{{ e.action || "-" }}</td>
              </tr>
            </tbody>
          </table>
        </md-card-content>
      </md-card>
    </template>
  </div>
</template>

<script>
import BatteryRepository from "../../repositories/BatteryRepository.js"
import HealthBadge from "../../shared/HealthBadge.vue"

const POLL_MS = 30000

export default {
  name: "LibreSolarBmsBatteryDetail",
  components: { HealthBadge },
  data() {
    return { battery: null, events: [], error: "", timer: null }
  },
  computed: {
    tiles() {
      const b = this.battery
      const n = (v, d, u = "") => (v === null || v === undefined ? "-" : Number(v).toFixed(d) + u)
      return [
        { label: "State of charge", value: n(b.soc_pct, 0, " %") },
        { label: "Capacity left (estimate)", value: n(b.soh_pct, 0, " %") },
        { label: "Charge cycles", value: n(b.cycles, 0) },
        { label: "Pack voltage", value: n(b.pack_voltage_v, 2, " V") },
        { label: "Max temperature", value: n(b.max_temp_c, 1, " °C") },
        {
          label: "Cell imbalance",
          value: b.imbalance_v === null ? "-" : Math.round(b.imbalance_v * 1000) + " mV",
        },
        { label: "State", value: b.state || "-" },
        {
          label: "Last report",
          value: b.last_seen_at ? new Date(b.last_seen_at).toLocaleString() : "never",
        },
      ]
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
      const id = this.$route.params.id
      try {
        const [battery, events] = await Promise.all([
          BatteryRepository.get(id),
          BatteryRepository.events(id),
        ])
        this.battery = battery.data.data
        this.events = events.data.data
        this.error = ""
      } catch (e) {
        this.error = "Could not load this battery."
      }
    },
  },
}
</script>

<style lang="scss" scoped>
.lsb-page {
  margin-top: 1rem;
}
.banner {
  border-left: 6px solid #999;
  &-green {
    border-left-color: #2e7d32;
  }
  &-amber {
    border-left-color: #ef8f00;
  }
  &-red {
    border-left-color: #c62828;
  }
}
.banner-head {
  display: flex;
  align-items: center;
  gap: 16px;
  h3 {
    margin: 0;
  }
}
.score {
  color: #666;
}
.reasons {
  margin: 12px 0 0;
}
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
    font-size: 1.4rem;
    font-weight: 500;
  }
}
.muted {
  color: #888;
  font-size: 0.8rem;
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
.sev {
  font-weight: 500;
  &-critical {
    color: #b71c1c;
  }
  &-warning {
    color: #8a5a00;
  }
  &-info {
    color: #555;
  }
}
.error {
  color: #b71c1c;
}
</style>
