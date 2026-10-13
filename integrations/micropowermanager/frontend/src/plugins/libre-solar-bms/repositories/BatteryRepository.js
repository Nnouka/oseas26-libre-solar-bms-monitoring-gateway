import Client from "@/repositories/Client/AxiosClient.js"

const resource = "/api/libre-solar-bms/batteries"

export default {
  list() {
    return Client.get(resource)
  },
  get(batteryId) {
    return Client.get(`${resource}/${batteryId}`)
  },
  events(batteryId, limit = 50) {
    return Client.get(`${resource}/${batteryId}/events`, { params: { limit } })
  },
}
