import json
import time

import paho.mqtt.client as mqtt

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hello-pub")
client.connect("localhost", 1883) # the host name of the broker, and its port
client.loop_start() # netzork traffic runs in a background thread

for n in range(5):
    info = client.publish("hello/world", json.dumps({"n": n}), qos=1)
    info.wait_for_publish() # block until the broker has acknowledged the message
    print("sent", n)
    time.sleep(1)
    
client.loop_stop()
client.disconnect()