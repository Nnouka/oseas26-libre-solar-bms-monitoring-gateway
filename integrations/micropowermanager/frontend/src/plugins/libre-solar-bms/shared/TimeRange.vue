<template>
  <div class="time-range">
    <label>
      <span class="muted">Time range</span>
      <select :value="value.preset" @change="setPreset($event.target.value)">
        <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.label }}</option>
      </select>
    </label>
    <template v-if="value.preset === 'custom'">
      <label>
        <span class="muted">From</span>
        <input type="datetime-local" :value="value.from" @change="setField('from', $event.target.value)" />
      </label>
      <label>
        <span class="muted">To</span>
        <input type="datetime-local" :value="value.to" @change="setField('to', $event.target.value)" />
      </label>
    </template>
  </div>
</template>

<script>
import { PRESETS } from "./format.js"

export default {
  name: "LibreSolarBmsTimeRange",
  props: {
    // { preset: "24h" | "7d" | "30d" | "custom", from: string, to: string } (from/to are datetime-local values)
    value: { type: Object, required: true },
  },
  data() {
    return { presets: PRESETS }
  },
  methods: {
    setPreset(preset) {
      this.$emit("input", { ...this.value, preset })
    },
    setField(field, text) {
      this.$emit("input", { ...this.value, [field]: text })
    },
  },
}
</script>

<style lang="scss" scoped>
.time-range {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: flex-end;
  label {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
}
.muted {
  color: #888;
  font-size: 0.8rem;
}
</style>
