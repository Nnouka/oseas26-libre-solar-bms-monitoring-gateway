# Part 2: MQTT first contact

**Goal.** Understand MQTT by doing: run a broker, send and receive messages from Python, and see **retained messages** and the **last will** work. These three things are exactly what the publisher in Part 7 relies on.

**Time.** 1.5 hours.

## Ideas

- **Broker, publisher, subscriber.** MQTT is a post office. Programs never talk to each other directly. They connect to a **broker** (Mosquitto), *publish* messages to **topics** (`bms/dev1/meas`), and *subscribe* to topics. The sender does not know who listens.
- **Topics** are slash-separated paths. `+` matches one level and `#` matches the rest: `bms/+/status`, `bms/#`.
- **QoS** is delivery effort. 0 means at most once (fire and forget, good for readings that are replaced a second later). 1 means at least once (acknowledged, good for state that matters).
- **Retained message.** The broker remembers the *last* retained message of a topic and gives it to every new subscriber immediately. Perfect for "what is the current status?".
- **Last Will and Testament (LWT).** When a client connects it can register a message that the *broker* publishes if the client disappears without saying goodbye. The simulator uses it to turn its status from `online` into `offline`.
- **Client id.** Must be unique per connected client. If a second client connects with the same id, the broker kicks the first one out.
- **The paho loop.** paho-mqtt does its network work in a loop. `loop_start()` runs it in a background thread, so your program stays free.

Reading (15 minutes, optional): [MQTT essentials, parts 1 to 5](https://www.hivemq.com/mqtt/mqtt-essentials/).

## Steps

### 2.1 Configure and start a broker

From the repository root create `infra/mosquitto/mosquitto.conf`. (The repository's folder is currently spelled `infra/mosquito`. Fix it first
with `git mv infra/mosquito infra/mosquitto`.)

```text
# DEVELOPMENT ONLY: anyone who can reach the port can read and publish.
listener 1883
allow_anonymous true
persistence false
```

Mosquitto 2 refuses outside connections without a `listener` line, which is why the file is needed even for a toy setup.

Start the broker in its own terminal and leave it running (PowerShell; in bash replace `${PWD}` by `$(pwd)`):

```bash
docker run --rm -it --name mosq -p 1883:1883 -v ${PWD}/infra/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto:2.0.20
```

You should see lines ending in `mosquitto version 2.0.20 running`. Press Ctrl+C there to stop it later.

### 2.2 Talk to it with the command line first

In terminal 2 (subscriber):

```bash
docker exec -it mosq mosquitto_sub -t "hello/#" -v
```

In terminal 3 (publisher):

```bash
docker exec -it mosq mosquitto_pub -t hello/world -m "first message"
```

The subscriber prints `hello/world first message`. Now the retained behaviour. Stop the subscriber, then:

```bash
docker exec -it mosq mosquitto_pub -t hello/retained -m "I stay" -r
docker exec -it mosq mosquitto_sub -t "hello/#" -v -C 1
```

The new subscriber is handed `hello/retained I stay` immediately although nobody published after it connected. Clear a retained message by publishing an empty retained payload: `mosquitto_pub -t hello/retained -n -r`.

### 2.3 Publish from Python

Create the folder `spikes/` (throw-away experiments) with `spikes/hello_pub.py`:

```python
import json
import time

import paho.mqtt.client as mqtt

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hello-pub")
client.connect("localhost", 1883)   # the host name of the broker, and its port
client.loop_start()                 # network traffic runs in a background thread

for n in range(5):
    info = client.publish("hello/world", json.dumps({"n": n}), qos=1)
    info.wait_for_publish()         # block until the broker has acknowledged the message
    print("sent", n)
    time.sleep(1)

client.loop_stop()
client.disconnect()
```

Run it (`uv run python spikes/hello_pub.py`) while a subscriber is running. Expected output of the publisher:

```text
sent 0
sent 1
sent 2
sent 3
sent 4
```

Read every line and answer: what does `wait_for_publish()` wait for, and why is it there? (Hint: with QoS 1 it waits for the broker's acknowledgement.)

### 2.4 Subscribe from Python

`spikes/hello_sub.py`:

```python
import paho.mqtt.client as mqtt


def on_connect(client, userdata, flags, reason_code, properties):
    print("connected:", reason_code)
    client.subscribe("hello/#")      # subscribe in on_connect so it is repeated after a reconnect


def on_message(client, userdata, msg):
    print(msg.topic, msg.payload.decode(), "(retained)" if msg.retain else "")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hello-sub")
client.on_connect = on_connect
client.on_message = on_message
client.connect("localhost", 1883)
client.loop_forever()                # blocks; stop with Ctrl+C
```

Start it, then run the publisher again. Expected output of the subscriber:

```text
connected: Success
hello/world {"n": 0} 
hello/world {"n": 1} 
hello/world {"n": 2} 
hello/world {"n": 3} 
hello/world {"n": 4} 
```

Questions to think through:

- Why is `subscribe` inside `on_connect`, not after `connect`? (Answer: after a reconnect the broker has forgotten the subscription, and `on_connect` runs again.)
- What is `properties=None`'s job? (paho 2 passes MQTT 5 properties; we ignore them.)

> **Gotcha (really happens).** Start a second copy of `hello_sub.py`. Both keep printing `connected: Success` and each misses messages. They share the client id `hello-sub`, so the broker keeps disconnecting the older one, which reconnects, which disconnects the other. Give every client a unique id. The simulator's id contains its device id.

### 2.5 The last will

`spikes/lwt_demo.py`:

```python
import os
import time

import paho.mqtt.client as mqtt

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lwt-demo")
client.will_set("demo/status", "offline", qos=1, retain=True)   # the broker keeps this in reserve
client.connect("localhost", 1883)
client.loop_start()
client.publish("demo/status", "online", qos=1, retain=True).wait_for_publish()
print("online, now crashing on purpose in 3 seconds ...")
time.sleep(3)
os._exit(1)   # dies at once, without a clean MQTT disconnect: like a power cut
```

In one terminal watch the status topic: `docker exec -it mosq mosquitto_sub -t demo/status -v`. In another run the script. You should see:

```text
demo/status online
demo/status offline
```

and the script's own output:

```text
online, now crashing on purpose in 3 seconds ...
```

The script never published `offline`. The *broker* did, because the connection died without a clean disconnect. This is how Home Assistant (Part 8) learns that the simulator is gone.

A real power cut is slower than `os._exit`, because the broker only notices a silent network after about 1.5 times the **keepalive** interval. That is why the publisher in Part 7 sets a short keepalive (30 s).

## Your turn: three small experiments

Write the answers in a notes file. You will need them.

1. Subscribe with `bms/+/status` and publish to `bms/a/status`, `bms/b/status` and `bms/a/meas`. Which messages arrive?
2. Publish a retained message with QoS 1, start a *new* subscriber with a *different* topic filter that still matches (`hello/+`). Does it get the retained message?
3. Stop the broker (Ctrl+C in its terminal) while `hello_sub.py` runs, then start it again. What does `hello_sub.py` do? (Look at what is printed. You will see the reconnect is automatic, and that is the behaviour Part 7 must preserve.)

<details>
<summary>Answers</summary>

1. `bms/a/status` and `bms/b/status`. `+` matches exactly one level, and `bms/a/meas` has a different last level.
2. Yes. Retained messages are delivered to every new subscription whose filter matches the topic.
3. It prints `connected: Success` again after the broker is back, because `loop_forever()` reconnects on its own and `on_connect` re-subscribes. (With `loop_start()` the same is true.)

</details>

## Checkpoint

- You can explain, in your own words, retained messages, QoS 0 versus 1, and the last will.
- All three experiments are answered.

## Commit

```bash
git add infra spikes
git commit -m "chore: mosquitto config and MQTT spikes"
```

Next: [Part 3: The ThingSet line](03-thingset-line.md).
