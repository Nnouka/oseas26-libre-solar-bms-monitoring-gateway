import os
import time

import paho.mqtt.client as mqtt

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lwt-demo")
client.will_set("demo/status", "offline", qos=1, retain=True) # the broker keeps this in reserve
client.connect("localhost", 1883)
client.loop_start()
client.publish("demo/status", "online", qos=1, retain=True).wait_for_publish()
print("online, now crashing on purpose in 3 seconds...")
time.sleep(3)
os._exit(1) # dies at once, without a clean MQTT disconnect: like a power cut