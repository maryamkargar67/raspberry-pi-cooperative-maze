import json
import socket
import sys
import time

# Import Raspberry Pi hardware libraries only when the code is running on the real Raspberry Pi.
try:
    import smbus2
    import RPi.GPIO as GPIO
    ON_PI = True
except ImportError:
    ON_PI = False
    print("[INFO] PC test mode: GPIO/I2C disabled")

# ================= NETWORK CONFIG =================

# Master Raspberry Pi IP address; the Client sends all UDP packets to this address.
MASTER_IP = "192.168.77.65" 
UDP_MASTER_PORT = 5005
UDP_CLIENT_PORT = 5006

# ================= HARDWARE CONFIG =================

# ADS1115 ADC settings for reading the joystick through the I2C bus.
ADS_ADDR = 0x48
I2C_BUS = 1

J1_B7_X = 0      # Client joystick X-axis -> RIGHT
J2_B8_Y = 3      # Client joystick Y-axis -> DOWN
JS_HIGH = 24000

# Fixed ADS1115 channels
X_CHANNEL = 0       # X-axis for RIGHT on Client
Y_CHANNEL = 3       # Y-axis for DOWN on Client

# Strong noise filtering for joystick direction.
AXIS_DEADZONE = 5500
AXIS_DOMINANCE_RATIO = 1.35
AXIS_FILTER_SAMPLES = 1
AXIS_SAMPLE_DELAY = 0.0
CALIBRATION_SAMPLES = 8
CALIBRATION_DELAY = 0.01

# Digital button inputs used by the Client controller.
PIN_B7 = 5
PIN_B8 = 6

# ================= ULTRA-LIGHT TIMING FOR RASPBERRY PI 1 B+ =================

# Timing values keep the loop lightweight for Raspberry Pi 1 B+.
HELLO_DELAY = 0.8
INPUT_DELAY = 0.08
STATUS_DELAY = 5.0
JOIN_REPEAT_DELAY = 0.8
ACK_DELAY = 1.0
LOOP_SLEEP = 0.02

# ================= INIT HARDWARE =================

# Configure GPIO pins only on the Raspberry Pi; PC test mode skips this part.
if ON_PI:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(PIN_B7, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(PIN_B8, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    bus = smbus2.SMBus(I2C_BUS)


# Read one ADS1115 analog channel and return a normalized ADC value.
def read_adc(channel):
    if not ON_PI:
        return 16384

    mux = [0x4000, 0x5000, 0x6000, 0x7000][channel]
    config = 0x8000 | mux | 0x0200 | 0x0100 | 0x0080 | 0x0003

    bus.write_i2c_block_data(
        ADS_ADDR,
        0x01,
        [(config >> 8) & 0xFF, config & 0xFF]
    )

    time.sleep(0.006)

    data = bus.read_i2c_block_data(ADS_ADDR, 0x00, 2)
    value = (data[0] << 8) | data[1]

    if value > 32767:
        value -= 65536

    return max(0, value)



def calibrate_adc_centers():
    """
    Read ADS1115 channels while joystick is neutral.
    Do not touch the joystick during startup calibration.
    """
    centers = {0: 16384, 1: 16384, 2: 16384, 3: 16384}

    if not ON_PI:
        return centers

    print("[INFO] Calibrating joystick neutral position. Do not touch joystick...")

    sums = {0: 0, 1: 0, 2: 0, 3: 0}
    count = 0

    for _ in range(CALIBRATION_SAMPLES):
        for ch in [0, 1, 2, 3]:
            try:
                sums[ch] += read_adc(ch)
            except Exception:
                sums[ch] += 16384
        count += 1
        time.sleep(CALIBRATION_DELAY)

    for ch in [0, 1, 2, 3]:
        centers[ch] = int(sums[ch] / max(1, count))

    print("[INFO] ADC neutral centers:", centers)
    return centers


# Store neutral joystick values immediately after startup calibration.
ADC_CENTERS = calibrate_adc_centers()


# Return the middle value of a sorted sample list for simple noise reduction.
def median_value(values):
    values = sorted(values)
    return values[len(values) // 2]


def read_adc_filtered(channel):
    """
    Read one ADC channel several times and return the median.
    This removes short spikes and random ADC noise.
    """
    samples = []

    for _ in range(AXIS_FILTER_SAMPLES):
        try:
            samples.append(read_adc(channel))
        except Exception:
            samples.append(ADC_CENTERS.get(channel, 16384))

        if AXIS_SAMPLE_DELAY > 0:
            time.sleep(AXIS_SAMPLE_DELAY)

    return median_value(samples)


# Calculate how far the joystick value is from its calibrated neutral position.
def axis_delta(channel):
    return read_adc_filtered(channel) - ADC_CENTERS.get(channel, 16384)


def read_xy_delta():
    x = axis_delta(X_CHANNEL)
    y = axis_delta(Y_CHANNEL)
    return x, y


# Convert joystick movement into the Client-controlled directions: RIGHT or DOWN.
def choose_client_direction():
    """
    Client responsibilities:
    X-axis movement -> RIGHT only
    Y-axis movement -> DOWN only

    Noise filtering:
    1) median ADC filtering
    2) neutral calibration
    3) deadzone
    4) axis dominance check
    5) ambiguous diagonal/noisy states are ignored
    """
    x, y = read_xy_delta()

    ax = abs(x)
    ay = abs(y)

    x_active = ax >= AXIS_DEADZONE
    y_active = ay >= AXIS_DEADZONE

    if not x_active and not y_active:
        return 0, 0

    if y_active and not x_active:
        return 0, 1

    if x_active and not y_active:
        return 1, 0

    if ay >= ax * AXIS_DOMINANCE_RATIO:
        return 0, 1

    if ax >= ay * AXIS_DOMINANCE_RATIO:
        return 1, 0

    return 0, 0


# Optional debug helper for printing live joystick ADC values.
def debug_adc_once():
    x, y = read_xy_delta()
    print(f"[ADC] X={x:+6d} Y={y:+6d}")


# Buttons use pull-up logic: pressed means the GPIO input becomes LOW.
def button_pressed(pin):
    if not ON_PI:
        return False
    return GPIO.input(pin) == GPIO.LOW


# Legacy join check using B7 and B8 together; auto-join is used in the current main loop.
def join_confirmed():
    return button_pressed(PIN_B7) and button_pressed(PIN_B8)


# Read the Client controller buttons and translate them into movement commands.
def read_client_input():
    """
    Client controller - DIGITAL ONLY:

    B7 / GPIO5  -> DOWN only
    B8 / GPIO6  -> RIGHT only

    ADS1115 analog joystick fallback is disabled here.
    Other physical directions are ignored.
    """
    if button_pressed(PIN_B7):
        return 0, 1

    if button_pressed(PIN_B8):
        return 1, 0

    return 0, 0


# Safely read Linux system files, for example CPU temperature.
def read_file(path, default="0"):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return default


# Collect Client Raspberry Pi status values that are sent to the Master result screen.
def system_status():
    status = {"build": "client_v25_combined_best_auto_join"}

    try:
        import psutil
        status["cpu"] = f"{psutil.cpu_percent(0.01):.0f}%"
        status["ram"] = f"{psutil.virtual_memory().percent:.0f}%"
        status["uptime"] = f"{int(time.time() - psutil.boot_time())}s"
        status["disk"] = f"{psutil.disk_usage('/').percent:.0f}%"

        raw = read_file("/sys/class/thermal/thermal_zone0/temp", "0")
        try:
            status["temp"] = f"{int(raw) / 1000:.1f}C"
        except Exception:
            status["temp"] = "n/a"

    except Exception:
        status["cpu"] = "n/a"
        status["ram"] = "n/a"
        status["uptime"] = "n/a"
        status["disk"] = "n/a"
        status["temp"] = "n/a"

    status["python"] = sys.version.split()[0]
    return status


# UDP communication helper: sends Client packets to Master and receives Master messages.
class ClientNetwork:
    # Create one UDP socket for sending and one for receiving messages from the Master.
    def __init__(self):
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("", UDP_CLIENT_PORT))
        self.rx.settimeout(0.02)

    # Convert a Python dictionary to JSON and send it as a UDP packet to the Master.
    def send(self, obj):
        try:
            data = json.dumps(obj).encode()
            self.tx.sendto(data, (MASTER_IP, UDP_MASTER_PORT))
            return True
        except Exception as e:
            print(f"[ERROR] UDP send failed: {e}")
            return False

    # Try to receive one UDP packet from the Master; return None if no packet arrives in time.
    def receive(self):
        try:
            data, _ = self.rx.recvfrom(2048)
            return json.loads(data.decode())
        except socket.timeout:
            return None
        except Exception:
            return None

    # Close UDP sockets before the program exits.
    def close(self):
        self.tx.close()
        self.rx.close()


# Main Client loop: auto-joins, sends movement/status packets, and replies with ACK messages.
def main():
    net = ClientNetwork()

    print("Client v25 controller started.")
    print("Player names are entered on the Master touchscreen.")
    print("Client joins the Master automatically.")
    print("Controls: B7/GPIO5 = DOWN, B8/GPIO6 = RIGHT")

    # The display names are entered on the Master; this fixed name identifies the Client controller.
    client_name = "ClientController"

    # These timestamps control periodic sending without blocking the main loop.
    last_hello = 0
    last_input = 0
    last_status = 0
    last_join = 0
    last_ack = 0
    # Auto-join mode: the Client is considered ready as soon as the script is running.
    joined = True

    try:
        while True:
            now = time.time()

            # Periodically tell the Master that the Client is alive and ready.
            if now - last_hello >= HELLO_DELAY:
                net.send({
                    "type": "hello",
                    "name": client_name,
                    "joined": joined,
                })
                last_hello = now

            # Re-send join confirmation periodically so reconnection works after a network interruption.
            if now - last_join >= JOIN_REPEAT_DELAY:
                net.send({
                    "type": "join_confirm",
                    "name": client_name,
                })
                last_join = now

            # Read Client buttons and send the latest RIGHT/DOWN movement command to the Master.
            if joined and now - last_input >= INPUT_DELAY:
                dx, dy = read_client_input()
                net.send({
                    "type": "move",
                    "dx": dx,
                    "dy": dy,
                })
                last_input = now

            # Send CPU, RAM, uptime, disk, temperature, and Python status to the Master.
            if now - last_status >= STATUS_DELAY:
                net.send({
                    "type": "status",
                    "status": system_status(),
                })
                last_status = now

            # Receive game-state or reset messages from the Master.
            msg = net.receive()
            if msg:
                msg_type = msg.get("type")

                # If the Master resets the join state, the Client immediately joins again automatically.
                if msg_type == "reset_join":
                    joined = True
                    print("Master reset join state. Auto-joining again.")

                # During gameplay, reply to Master state messages with ACK packets for latency measurement.
                elif msg_type == "playing":
                    master_sent_time = msg.get("master_sent_time")
                    if master_sent_time is not None and now - last_ack >= ACK_DELAY:
                        net.send({
                            "type": "ack",
                            "master_sent_time": master_sent_time,
                        })
                        last_ack = now

            # Small sleep prevents the loop from overloading the Raspberry Pi 1 B+ CPU.
            time.sleep(LOOP_SLEEP)

    # Allow safe manual stop with Ctrl+C during testing.
    except KeyboardInterrupt:
        print("Client stopped.")

    # Always close sockets and clean up GPIO when the Client program stops.
    finally:
        net.close()
        if ON_PI:
            GPIO.cleanup()


# Run the Client program only when this file is executed directly.
if __name__ == "__main__":
    main()
