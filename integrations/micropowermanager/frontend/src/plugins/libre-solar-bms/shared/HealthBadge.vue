<template>
  <span :class="['health-badge', `health-${health}`]">
    <span class="dot" />
    {{ label }}
  </span>
</template>

<script>
const LABELS = { green: "Healthy", amber: "Needs attention", red: "Critical" }

export default {
  name: "HealthBadge",
  props: {
    health: { type: String, default: "green" },
  },
  computed: {
    label() {
      return LABELS[this.health] || "Unknown"
    },
  },
}
</script>

<style lang="scss" scoped>
.health-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.85rem;
  font-weight: 500;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}
.health-green {
  color: #1b5e20;
  background: #e8f5e9;
}
.health-amber {
  color: #8a5a00;
  background: #fff3e0;
}
.health-red {
  color: #b71c1c;
  background: #ffebee;
}
</style>
