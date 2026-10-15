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

      <div class="tabs" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.id"
          role="tab"
          :aria-selected="tab === t.id ? 'true' : 'false'"
          :class="['tab', { active: tab === t.id }]"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </div>

      <overview-tab v-if="tab === 'overview'" :battery="battery" />
      <history-tab v-else-if="tab === 'history'" :battery-id="battery.id" />
      <events-tab v-else :battery-id="battery.id" />
    </template>
  </div>
</template>

<script>
import BatteryRepository from "../../repositories/BatteryRepository.js"
import HealthBadge from "../../shared/HealthBadge.vue"
import EventsTab from "./EventsTab.vue"
import HistoryTab from "./HistoryTab.vue"
import OverviewTab from "./OverviewTab.vue"

const POLL_MS = 30000

export default {
  name: "LibreSolarBmsBatteryDetail",
  components: { HealthBadge, OverviewTab, HistoryTab, EventsTab },
  data() {
    return {
      battery: null,
      error: "",
      timer: null,
      tab: "overview",
      tabs: [
        { id: "overview", label: "Overview" },
        { id: "history", label: "History" },
        { id: "events", label: "Events" },
      ],
    }
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
        const response = await BatteryRepository.get(this.$route.params.id)
        this.battery = response.data.data
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
.tabs {
  display: flex;
  gap: 4px;
  margin: 16px 0 8px;
  border-bottom: 1px solid #ddd;
}
.tab {
  background: none;
  border: none;
  border-bottom: 3px solid transparent;
  padding: 8px 16px;
  font-size: 1rem;
  cursor: pointer;
  color: #555;
  &.active {
    color: #222;
    font-weight: 500;
    border-bottom-color: #3f51b5;
  }
}
.error {
  color: #b71c1c;
}
</style>
