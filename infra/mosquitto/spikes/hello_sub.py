import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, reason_code, properties):
    print("connected:", reason_code)
    client.subscribe("hello/#") # subscribe in on_connect so it is repeated after a reconnect
    
def on_message(client, userdata, msg):
    print(msg.topic, msg.payload.decode(), "(retained)" if msg.retain else "")
    
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hello-sub")
client.on_connect = on_connect
client.on_message = on_message
client.connect("localhost", 1883)
client.loop_forever() # blocks; stop with Ctrl + C
