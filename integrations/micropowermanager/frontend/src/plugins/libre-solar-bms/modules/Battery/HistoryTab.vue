<template>
  <md-card>
    <md-card-header>
      <h4 class="title">Summary history</h4>
      <p class="category">Every summary the gateway reported, newest first.</p>
    </md-card-header>
    <md-card-content>
      <div class="filters">
        <time-range v-model="range" />
        <label>
          <span class="muted">Health</span>
          <select v-model="health">
            <option value="">All</option>
            <option value="green">Healthy</option>
            <option value="amber">Needs attention</option>
            <option value="red">Critical</option>
          </select>
        </label>
      </div>

      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loaded && rows.length === 0">No summaries in this range</p>
      <table v-else-if="rows.length" class="lsb-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Health</th>
            <th>Score</th>
            <th>State</th>
            <th>Charge</th>
            <th>Capacity left</th>
            <th>Cycles</th>
            <th>Voltage</th>
            <th>Max temp</th>
            <th>Imbalance</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="r in rows">
            <tr :key="r.id" class="clickable" @click="toggle(r.id)">
              <td>{{ fmtTime(r.reported_at) }}</td>
              <td><health-badge :health="r.health" /></td>
              <td>{{ r.health_score === null ? "-" : r.health_score }}</td>
              <td>{{ r.state || "-" }}</td>
              <td>{{ fmtNumber(r.soc_pct, 0, " %") }}</td>
              <td>{{ fmtNumber(r.soh_pct, 0, " %") }}</td>
              <td>{{ fmtNumber(r.cycles, 1) }}</td>
              <td>{{ fmtNumber(r.pack_voltage_v, 2, " V") }}</td>
              <td>{{ fmtNumber(r.max_temp_c, 1, " °C") }}</td>
              <td>{{ r.imbalance_v === null ? "-" : Math.round(r.imbalance_v * 1000) + " mV" }}</td>
            </tr>
            <tr v-if="expanded === r.id" :key="`x${r.id}`" class="detail">
              <td colspan="10">
                <div>
                  <strong>Reasons:</strong>
                  <span v-if="!(r.health_reasons || []).length"> none</span>
                  <ul v-else>
                    <li v-for="reason in r.health_reasons" :key="reason">{{ reason }}</li>
                  </ul>
                </div>
                <div>
                  <strong>Active faults:</strong>
                  <span v-if="!(r.active_faults || []).length"> none</span>
                  <span v-for="f in r.active_faults" :key="f" class="chip"> {{ f }}</span>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>

      <div v-if="total > 0" class="pager">
        <button :disabled="page <= 1" @click="go(page - 1)">Previous</button>
        <span>Page {{ page }} of {{ lastPage }} ({{ total }} summaries)</span>
        <button :disabled="page >= lastPage" @click="go(page + 1)">Next</button>
      </div>
    </md-card-content>
  </md-card>
</template>

<script>
import BatteryRepository from "../../repositories/BatteryRepository.js"
import HealthBadge from "../../shared/HealthBadge.vue"
import TimeRange from "../../shared/TimeRange.vue"
import { fmtNumber, fmtTime, resolveRange } from "../../shared/format.js"

const POLL_MS = 30000
const PER_PAGE = 25

export default {
  name: "LibreSolarBmsHistoryTab",
  components: { HealthBadge, TimeRange },
  props: {
    batteryId: { type: Number, required: true },
  },
  data() {
    return {
      range: { preset: "24h", from: "", to: "" },
      health: "",
      page: 1,
      rows: [],
      total: 0,
      lastPage: 1,
      expanded: null,
      error: "",
      loaded: false,
      timer: null,
      requestId: 0,
    }
  },
  watch: {
    range: {
      deep: true,
      handler() {
        this.page = 1
        this.load()
      },
    },
    health() {
      this.page = 1
      this.load()
    },
  },
  mounted() {
    this.load()
    this.timer = setInterval(() => {
      if (this.page === 1) this.load() // keep the newest page live; do not yank a page the operator is reading
    }, POLL_MS)
  },
  beforeDestroy() {
    clearInterval(this.timer)
  },
  methods: {
    fmtNumber,
    fmtTime,
    toggle(id) {
      this.expanded = this.expanded === id ? null : id
    },
    go(page) {
      this.page = page
      this.load()
    },
    async load() {
      const id = ++this.requestId
      const { from, to } = resolveRange(this.range)
      try {
        const response = await BatteryRepository.summaries(this.batteryId, {
          from,
          to,
          health: this.health || undefined,
          page: this.page,
          per_page: PER_PAGE,
        })
        if (id !== this.requestId) return // a newer request is in flight: drop this reply
        const body = response.data
        this.rows = body.data
        this.total = body.total
        this.lastPage = body.last_page
        this.error = ""
      } catch (e) {
        if (id !== this.requestId) return
        this.error = "Could not load the summary history."
      }
      this.loaded = true
    },
  },
}
</script>

<style lang="scss" scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  align-items: flex-end;
  margin-bottom: 12px;
  label {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
}
.lsb-table {
  width: 100%;
  border-collapse: collapse;
  th,
  td {
    text-align: left;
    padding: 8px;
    border-bottom: 1px solid #eee;
  }
}
.clickable {
  cursor: pointer;
  &:hover {
    background: #fafafa;
  }
}
.detail td {
  background: #fafafa;
}
.chip {
  display: inline-block;
  background: #ffebee;
  color: #b71c1c;
  border-radius: 12px;
  padding: 1px 8px;
  margin-left: 4px;
}
.pager {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-top: 12px;
}
.muted {
  color: #888;
  font-size: 0.8rem;
}
.error {
  color: #b71c1c;
}
</style>
