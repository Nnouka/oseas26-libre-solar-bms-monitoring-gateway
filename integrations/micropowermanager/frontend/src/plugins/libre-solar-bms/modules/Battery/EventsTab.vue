<template>
  <md-card>
    <md-card-header>
      <h4 class="title">Fault log</h4>
      <p class="category">Faults raised and cleared by the battery, newest first.</p>
    </md-card-header>
    <md-card-content>
      <div class="filters">
        <time-range v-model="range" />
        <label>
          <span class="muted">Severity</span>
          <select v-model="severity">
            <option value="">All</option>
            <option value="critical">critical</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
        </label>
        <label>
          <span class="muted">Type</span>
          <select v-model="type">
            <option value="">All</option>
            <option value="fault_raised">fault_raised</option>
            <option value="fault_cleared">fault_cleared</option>
          </select>
        </label>
      </div>

      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loaded && rows.length === 0">No events in this range</p>
      <table v-else-if="rows.length" class="lsb-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Severity</th>
            <th>Event</th>
            <th>Recommended action</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ fmtTime(e.occurred_at) }}</td>
            <td>
              <span :class="['sev', `sev-${e.severity}`]">{{ e.severity }}</span>
            </td>
            <td>{{ e.message }}</td>
            <td>{{ e.action || "-" }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="total > 0" class="pager">
        <button :disabled="page <= 1" @click="go(page - 1)">Previous</button>
        <span>Page {{ page }} of {{ lastPage }} ({{ total }} events)</span>
        <button :disabled="page >= lastPage" @click="go(page + 1)">Next</button>
      </div>
    </md-card-content>
  </md-card>
</template>

<script>
import BatteryRepository from "../../repositories/BatteryRepository.js"
import TimeRange from "../../shared/TimeRange.vue"
import { fmtTime, resolveRange } from "../../shared/format.js"

const POLL_MS = 30000
const PER_PAGE = 25

export default {
  name: "LibreSolarBmsEventsTab",
  components: { TimeRange },
  props: {
    batteryId: { type: Number, required: true },
  },
  data() {
    return {
      range: { preset: "24h", from: "", to: "" },
      severity: "",
      type: "",
      page: 1,
      rows: [],
      total: 0,
      lastPage: 1,
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
    severity() {
      this.page = 1
      this.load()
    },
    type() {
      this.page = 1
      this.load()
    },
  },
  mounted() {
    this.load()
    this.timer = setInterval(() => {
      if (this.page === 1) this.load()
    }, POLL_MS)
  },
  beforeDestroy() {
    clearInterval(this.timer)
  },
  methods: {
    fmtTime,
    go(page) {
      this.page = page
      this.load()
    },
    async load() {
      const id = ++this.requestId
      const { from, to } = resolveRange(this.range)
      try {
        const response = await BatteryRepository.events(this.batteryId, {
          from,
          to,
          severity: this.severity || undefined,
          type: this.type || undefined,
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
        this.error = "Could not load the events."
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
