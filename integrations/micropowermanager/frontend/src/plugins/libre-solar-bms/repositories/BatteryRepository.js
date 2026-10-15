import Client from "@/repositories/Client/AxiosClient.js"

const resource = "/api/libre-solar-bms/batteries"

export default {
  list() {
    return Client.get(resource)
  },
  get(batteryId) {
    return Client.get(`${resource}/${batteryId}`)
  },
  summaries(batteryId, params = {}) {
    return Client.get(`${resource}/${batteryId}/summaries`, { params })
  },
  trend(batteryId, params = {}) {
    return Client.get(`${resource}/${batteryId}/trend`, { params })
  },
  // `params` may be an old-style numeric limit.
  events(batteryId, params = {}) {
    const query = typeof params === "number" ? { limit: params } : params
    return Client.get(`${resource}/${batteryId}/events`, { params: query })
  },
}
