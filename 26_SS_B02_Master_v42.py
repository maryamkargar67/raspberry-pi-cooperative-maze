"""
Master Raspberry Pi application for the B02 Cooperative Maze project.

The master device runs the complete game engine, reads the local controller,
communicates with the client Raspberry Pi over UDP/WLAN, renders the LCD output,
and stores session measurements in CSV format.
"""

import csv
import json
import math
import os
import random
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime

# ================= DISPLAY SETTINGS =================
# LCD framebuffer configuration for the 3.2/3.5 inch Raspberry Pi display.
LCD_FRAMEBUFFER = os.environ.get("MAZE_LCD_FB")
if LCD_FRAMEBUFFER is None and os.path.exists("/dev/fb1"):
    LCD_FRAMEBUFFER = "/dev/fb1"
if LCD_FRAMEBUFFER and "DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_FBDEV", LCD_FRAMEBUFFER)

# Optional vectorized framebuffer conversion for better LCD performance
try:
    import numpy as np
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False
    print("[WARN] numpy not found, framebuffer will be SLOW. Run: sudo apt install python3-numpy -y")

import pygame

# Direct framebuffer mode bypasses the desktop display stack
DIRECT_FB = True
FB_DEVICE = os.environ.get("MAZE_LCD_FB", LCD_FRAMEBUFFER or "/dev/fb1")


def read_fb_size(default_w=480, default_h=320):
    """Return the active framebuffer resolution, with a safe fallback."""
    fb_name = os.path.basename(FB_DEVICE)
    size_path = f"/sys/class/graphics/{fb_name}/virtual_size"
    try:
        raw = open(size_path, "r").read().strip()
        w, h = [int(v) for v in raw.split(",")[:2]]
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return default_w, default_h


def write_surface_to_fb(surface):
    """Render a pygame surface to the LCD framebuffer in RGB565 format."""
    try:
        if NUMPY_OK:
            # Vectorized RGB888-to-RGB565 conversion
            arr = pygame.surfarray.array3d(surface)        # (w, h, 3)
            arr = arr.transpose(1, 0, 2)                   # (h, w, 3)
            r = (arr[:, :, 0].astype(np.uint16) & 0xF8) << 8
            g = (arr[:, :, 1].astype(np.uint16) & 0xFC) << 3
            b = (arr[:, :, 2].astype(np.uint16) >> 3)
            rgb565 = r | g | b
            with open(FB_DEVICE, "wb", buffering=0) as fb:
                fb.write(rgb565.astype("<u2").tobytes())
        else:
            # Fallback conversion when numpy is unavailable
            rgb = pygame.image.tostring(surface, "RGB")
            out = bytearray((len(rgb) // 3) * 2)
            j = 0
            for i in range(0, len(rgb), 3):
                value = ((rgb[i] & 0xF8) << 8) | ((rgb[i+1] & 0xFC) << 3) | (rgb[i+2] >> 3)
                out[j] = value & 0xFF
                out[j+1] = (value >> 8) & 0xFF
                j += 2
            with open(FB_DEVICE, "wb", buffering=0) as fb:
                fb.write(out)
    except Exception as e:
        print(f"[ERROR] direct framebuffer write failed: {e}")


def update_display():
    """Update the display through direct framebuffer output or pygame flip."""
    if DIRECT_FB:
        write_surface_to_fb(screen)
    else:
        pygame.display.flip()


try:
    import smbus2
    import RPi.GPIO as GPIO
    ON_PI = True
except ImportError:
    ON_PI = False
    print("[INFO] PC test mode: GPIO/I2C disabled")

# ================= HARDWARE CONFIGURATION =================
# GPIO, I2C, ADC, and button mapping for the master-side controller.

ADS_ADDR = 0x48
I2C_BUS = 1

AXIS_DEADZONE = 5500
CALIBRATION_SAMPLES = 8
CALIBRATION_DELAY = 0.01
X_CHANNEL = 0
Y_CHANNEL = 3

AXIS_DOMINANCE_RATIO = 1.35
AXIS_FILTER_SAMPLES = 1
AXIS_SAMPLE_DELAY = 0.0

PIN_B7 = 5      # joystick/button B7: keyboard previous / gameplay UP
PIN_B8 = 6      # joystick/button B8: keyboard next / gameplay LEFT
PIN_B1 = 12     # green button B1: play again
PIN_B3 = 19     # white button B3: reset / show result
PIN_B4 = 16     # yellow button B4: ENTER / next name field
PIN_B5 = 26     # blue button B5: SELECT keyboard character

# ================= NETWORK CONFIGURATION =================
# UDP ports used for master-client communication over the direct WLAN link.

UDP_MASTER_PORT = 5005
UDP_CLIENT_PORT = 5006
CLIENT_TIMEOUT = 3.0   # seconds: if no packet arrives for this long, the client is treated as disconnected

# ================= GAME ENGINE & PERFORMANCE SETTINGS =================
# Runtime constants controlling maze size, timing, scoring, and logging.

WIN_W = 480
WIN_H = 320
TILE = 20
COLS = 20
ROWS = 15
MAZE_W = COLS * TILE
MAZE_H = ROWS * TILE
PANEL_X = MAZE_W
PANEL_W = WIN_W - MAZE_W

FPS = 15
INPUT_DELAY = 0.04
CLIENT_MOVE_HOLD_TIME = 0.15
MASTER_MOVE_HOLD_TIME = 0.12
MONSTER_DELAY = 4.00
HIT_COOLDOWN = 0.7
STATE_SEND_DELAY = 0.08
MASTER_STATUS_DELAY = 5.0

STAR_COUNT = 5
HOLE_COUNT = 4
DRAW_GRID = False
MONSTER_ENABLED = True

CSV_USB = "/mnt/usb/maze_scores.csv"
CSV_LOCAL = "maze_scores.csv"
CSV_FIELDS = [
    "rank", "timestamp", "team", "player1", "player2",
    "score", "stars", "monster_hits", "hole_hits",
    "duration_s", "overall_time_s", "last_played",
    "avg_latency_ms", "packet_errors"
]

# ================= COLORS =================
# Centralized color palette for pygame drawing.

BG = (15, 20, 30)
WALL = (20, 40, 70)
PATH = (235, 240, 255)
START_C = (255, 220, 0)
END_C = (0, 255, 120)
HOLE_C = (5, 5, 5)
STAR_C = (218, 165, 32)
PLAYER_C = (30, 140, 255)
MONSTER_C = (255, 70, 70)
WHITE = (240, 240, 240)
GRAY = (120, 135, 155)
GREEN = (0, 210, 100)
RED = (255, 80, 80)
YELLOW = (255, 210, 0)
CYAN = (0, 190, 255)
PANEL_C = (8, 14, 25)

# ================= HARDWARE INITIALIZATION =================
# Hardware interfaces are initialized only when the code runs on Raspberry Pi.

if ON_PI:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for p in [PIN_B7, PIN_B8, PIN_B1, PIN_B3, PIN_B4, PIN_B5]:
        GPIO.setup(p, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    bus = smbus2.SMBus(I2C_BUS)


def read_adc(channel):
    """Read one ADS1115 ADC channel and return a non-negative raw value."""
    if not ON_PI:
        return 16384
    mux = [0x4000, 0x5000, 0x6000, 0x7000][channel]
    config = 0x8000 | mux | 0x0200 | 0x0100 | 0x0080 | 0x0003
    bus.write_i2c_block_data(ADS_ADDR, 0x01, [(config >> 8) & 0xFF, config & 0xFF])
    time.sleep(0.006)
    data = bus.read_i2c_block_data(ADS_ADDR, 0x00, 2)
    value = (data[0] << 8) | data[1]
    if value > 32767:
        value -= 65536
    return max(0, value)


def calibrate_adc_centers():
    """Measure joystick neutral positions for stable dead-zone handling."""
    centers = {0: 16384, 1: 16384, 2: 16384, 3: 16384}
    if not ON_PI:
        return centers
    print("[INFO] Calibrating joystick. Do not touch joysticks...")
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
    print("[INFO] ADC centers:", centers)
    return centers


ADC_CENTERS = calibrate_adc_centers()


def median_value(values):
    """Return the median sample value for lightweight input filtering."""
    return sorted(values)[len(values) // 2]


def read_adc_filtered(channel):
    """Read an ADC channel with optional median filtering."""
    samples = []
    for _ in range(AXIS_FILTER_SAMPLES):
        try:
            samples.append(read_adc(channel))
        except Exception:
            samples.append(ADC_CENTERS.get(channel, 16384))
        if AXIS_SAMPLE_DELAY > 0:
            time.sleep(AXIS_SAMPLE_DELAY)
    return median_value(samples)


def axis_delta(channel):
    """Return joystick displacement relative to the calibrated center."""
    return read_adc_filtered(channel) - ADC_CENTERS.get(channel, 16384)


def read_xy_delta():
    """Return calibrated X/Y joystick displacement values."""
    return axis_delta(X_CHANNEL), axis_delta(Y_CHANNEL)


def choose_master_direction():
    """Map analog joystick movement to the allowed master directions."""
    x, y = read_xy_delta()
    ax, ay = abs(x), abs(y)
    x_active = ax >= AXIS_DEADZONE
    y_active = ay >= AXIS_DEADZONE
    if not x_active and not y_active:
        return 0, 0
    if y_active and not x_active:
        return 0, -1
    if x_active and not y_active:
        return -1, 0
    if ay >= ax * AXIS_DOMINANCE_RATIO:
        return 0, -1
    if ax >= ay * AXIS_DOMINANCE_RATIO:
        return -1, 0
    return 0, 0


def button_pressed(pin):
    """Return True when an active-low GPIO button is pressed."""
    if not ON_PI:
        return False
    return GPIO.input(pin) == GPIO.LOW


def read_master_input():
    """Read master-side movement input from joystick or fallback buttons."""
    jx, jy = choose_master_direction()
    if jx != 0 or jy != 0:
        return jx, jy
    if button_pressed(PIN_B7):
        return 0, -1
    if button_pressed(PIN_B8):
        return -1, 0
    return 0, 0

# ================= STORAGE & SYSTEM STATUS =================
# CSV score persistence and runtime diagnostics for documentation/test results.


def csv_path():
    """Select USB storage when available, otherwise use local CSV storage."""
    return CSV_USB if os.path.exists("/mnt/usb") else CSV_LOCAL


def load_scores():
    """Load stored score rows from the active CSV file."""
    path = csv_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _write_score_rows(path, rows):
    """Write the complete sorted score table to CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def verify_score_saved(path, timestamp, team, score):
    """Confirm that a newly written score row exists in the CSV file."""
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("timestamp") == timestamp and row.get("team") == team and str(row.get("score")) == str(score)):
                    return True
    except Exception:
        return False
    return False


def save_score(row):
    """Append a score row, sort by score, update ranks, and persist it."""
    preferred_path = csv_path()
    rows = load_scores()
    rows.append(row)
    rows.sort(key=lambda r: int(float(r.get("score", 0))), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    try:
        _write_score_rows(preferred_path, rows)
        if verify_score_saved(preferred_path, row["timestamp"], row["team"], row["score"]):
            return True, preferred_path, "OK"
    except Exception:
        pass
    return False, preferred_path, "FAIL"


def top_scores(limit=3):
    """Return the highest stored scores for result display or reporting."""
    rows = load_scores()
    try:
        rows.sort(key=lambda r: int(float(r.get("score", 0))), reverse=True)
    except Exception:
        pass
    return rows[:limit]


def overall_time_played():
    """Accumulate total played time from all stored sessions."""
    total = 0
    for row in load_scores():
        try:
            total += int(float(row.get("duration_s", 0)))
        except Exception:
            pass
    return total


def last_played_time():
    """Return the last recorded play timestamp."""
    rows = load_scores()
    if not rows:
        return "-"
    return rows[-1].get("last_played", rows[-1].get("timestamp", "-"))


def system_status():
    """Collect local Raspberry Pi runtime diagnostics."""
    status = {"build": "master_v42_reconnect"}
    try:
        import psutil
        status["cpu"] = f"{psutil.cpu_percent(0.01):.0f}%"
        status["ram"] = f"{psutil.virtual_memory().percent:.0f}%"
        status["uptime"] = f"{int(time.time() - psutil.boot_time())}s"
        status["disk"] = f"{psutil.disk_usage('/').percent:.0f}%"
        status["python"] = sys.version.split()[0]
        status["temp"] = f"{int(open('/sys/class/thermal/thermal_zone0/temp').read().strip()) / 1000:.1f}C"
    except Exception:
        status.setdefault("cpu", "n/a")
        status.setdefault("ram", "n/a")
        status.setdefault("uptime", "n/a")
        status.setdefault("disk", "n/a")
        status.setdefault("python", sys.version.split()[0])
        status.setdefault("temp", "n/a")
    return status

# ================= NETWORK COMMUNICATION =================
# Network layer for receiving client input and sending game state updates.


class NetworkMaster:
    """UDP communication manager for the master Raspberry Pi."""
    def __init__(self):
        """Initialize runtime state and required subsystems."""
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("", UDP_MASTER_PORT))
        self.rx.setblocking(False)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.lock = threading.Lock()
        self.running = True
        self.client_addr = None
        self.client_connected = False
        self.client_joined = False
        self.client_name = "Client"
        self.client_status = {}
        self.client_dx, self.client_dy = 0, 0
        self.client_move_time = 0.0
        self.latency_ms = 0.0
        self.latency_samples = deque(maxlen=10)
        self.packet_errors = 0
        self.last_rx = 0.0   # timestamp used for client timeout detection
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        """Continuously process incoming UDP packets from the client device."""
        while self.running:
            try:
                # Drain pending packets so the game uses the latest client input
                while True:
                    data, addr = self.rx.recvfrom(2048)
                    now = time.time()
                    try:
                        msg = json.loads(data.decode())
                    except Exception:
                        continue
                    with self.lock:
                        self.client_addr = addr
                        self.client_connected = True
                        self.last_rx = now   # refresh timeout timestamp after valid packet reception
                        msg_type = msg.get("type")
                        if msg_type in ["hello", "join_confirm"]:
                            self.client_name = msg.get("name", "Client")
                            self.client_joined = True
                        elif msg_type == "move" and self.client_joined:
                            self.client_dx = int(msg.get("dx", 0))
                            self.client_dy = int(msg.get("dy", 0))
                            self.client_move_time = now
                        elif msg_type == "status":
                            self.client_status = msg.get("status", {})
                        elif msg_type == "ack":
                            try:
                                sent = float(msg.get("master_sent_time", 0))
                                lat = (now - sent) * 1000
                                if 0 <= lat <= 5000:
                                    self.latency_ms = lat
                                    self.latency_samples.append(lat)
                            except Exception:
                                pass
            except BlockingIOError:
                pass
            except Exception:
                pass

            # Mark the client offline if no packet is received within the timeout.
            with self.lock:
                if self.last_rx > 0 and (time.time() - self.last_rx) > CLIENT_TIMEOUT:
                    self.client_connected = False
                    self.client_joined = False

            time.sleep(0.01)

    def get_move(self):
        """Return the latest valid client movement within its hold interval."""
        with self.lock:
            if time.time() - self.client_move_time <= CLIENT_MOVE_HOLD_TIME:
                return self.client_dx, self.client_dy
            return 0, 0

    def consume_move(self):
        """Clear the consumed client movement command."""
        with self.lock:
            self.client_dx, self.client_dy = 0, 0
            self.client_move_time = 0.0

    def send_state(self, state):
        """Send the current game state to the client device."""
        with self.lock:
            addr = self.client_addr
        if not addr:
            return
        try:
            state["master_sent_time"] = time.time()
            self.tx.sendto(json.dumps(state).encode(), (addr[0], UDP_CLIENT_PORT))
        except Exception:
            pass

    def reset_join(self):
        """Clear join state and request the client to rejoin."""
        with self.lock:
            self.client_joined = False
            self.client_dx = 0
            self.client_dy = 0
            addr = self.client_addr
        if addr:
            try:
                self.tx.sendto(json.dumps({"type": "reset_join"}).encode(), (addr[0], UDP_CLIENT_PORT))
            except Exception:
                pass

    def snapshot(self):
        """Return a thread-safe copy of current network/client status."""
        with self.lock:
            avg = sum(self.latency_samples) / len(self.latency_samples) if self.latency_samples else self.latency_ms
            return {
                "connected": self.client_connected,
                "joined": self.client_joined,
                "name": self.client_name,
                "status": dict(self.client_status),
                "latency": self.latency_ms,
                "avg_latency": avg,
                "packet_errors": self.packet_errors,
            }

    def stop(self):
        """Stop the background network receive loop."""
        self.running = False

# ================= MAZE =================
# Maze generation and path validation utilities.


def generate_maze(w, h):
    """Generate a solvable maze using randomized depth-first backtracking."""
    maze = [[1] * w for _ in range(h)]
    start_x, start_y = 1, 1
    maze[start_y][start_x] = 0
    stack = [(start_x, start_y)]
    while stack:
        x, y = stack[-1]
        directions = [(2, 0), (-2, 0), (0, 2), (0, -2)]
        random.shuffle(directions)
        moved = False
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            if 1 <= nx < w - 1 and 1 <= ny < h - 1 and maze[ny][nx] == 1:
                maze[ny][nx] = 0
                maze[y + dy // 2][x + dx // 2] = 0
                stack.append((nx, ny))
                moved = True
                break
        if not moved:
            stack.pop()
    return maze


def bfs_distances(start, maze, blocks=None):
    """Calculate shortest path distances from a start cell."""
    if blocks is None:
        blocks = []
    blocked = {tuple(b) for b in blocks}
    q = deque([tuple(start)])
    dist = {tuple(start): 0}
    while q:
        x, y = q.popleft()
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS:
                if maze[ny][nx] == 0 and (nx, ny) not in blocked and (nx, ny) not in dist:
                    dist[(nx, ny)] = dist[(x, y)] + 1
                    q.append((nx, ny))
    return dist


def choose_far_start_end(maze):
    """Choose distant start and end cells to increase maze length."""
    empty = [(x, y) for y in range(ROWS) for x in range(COLS) if maze[y][x] == 0]
    first = random.choice(empty)
    d1 = bfs_distances(first, maze)
    far1 = max(d1, key=d1.get)
    d2 = bfs_distances(far1, maze)
    far2 = max(d2, key=d2.get)
    return list(far1), list(far2)


def path_exists(start, end, maze, blocks):
    """Check whether the end cell remains reachable with blocked cells."""
    return tuple(end) in bfs_distances(start, maze, blocks)


def random_empty(maze, exclude):
    """Select a random free maze cell outside excluded positions."""
    cells = [[x, y] for y in range(ROWS) for x in range(COLS) if maze[y][x] == 0 and [x, y] not in exclude]
    return random.choice(cells) if cells else [1, 1]

# ================= PYGAME INIT =================
# Pygame display, framebuffer, and font initialization.

if DIRECT_FB:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

pygame.init()

if DIRECT_FB:
    WIN_W, WIN_H = read_fb_size(WIN_W, WIN_H)
    screen = pygame.Surface((WIN_W, WIN_H))
else:
    screen = pygame.display.set_mode((WIN_W, WIN_H))

TILE = max(14, min(WIN_W // COLS, WIN_H // ROWS))
MAZE_W, MAZE_H = COLS * TILE, ROWS * TILE
PANEL_X, PANEL_W = MAZE_W, max(0, WIN_W - MAZE_W)
clock = pygame.time.Clock()

FONT_SCALE = max(0.8, min(1.4, WIN_H / 480.0))
F_TINY = pygame.font.SysFont("monospace", int(12 * FONT_SCALE))
F_SMALL = pygame.font.SysFont("monospace", int(15 * FONT_SCALE))
F_MED = pygame.font.SysFont("monospace", int(22 * FONT_SCALE), bold=True)
F_BIG = pygame.font.SysFont("monospace", int(30 * FONT_SCALE), bold=True)
F_RESULT = pygame.font.SysFont("monospace", int(20 * FONT_SCALE), bold=True)
F_RESULT_SMALL = pygame.font.SysFont("monospace", int(15 * FONT_SCALE), bold=True)
F_NAME = pygame.font.SysFont("monospace", int(18 * FONT_SCALE), bold=True)
F_KEY = pygame.font.SysFont("monospace", int(16 * FONT_SCALE), bold=True)


def draw_text(text, x, y, color=WHITE, font=None):
    """Render text to the shared pygame screen surface."""
    screen.blit((font or F_SMALL).render(str(text), True, color), (x, y))

# ================= GAME =================
# Main game controller and screen-state machine.


class Game:
    """Main application class for game logic, rendering, input, and storage."""
    def __init__(self):
        """Initialize runtime state and required subsystems."""
        self.net = NetworkMaster()
        self.team = os.environ.get("MAZE_TEAM", "")
        self.player1 = os.environ.get("MAZE_PLAYER1", "")
        self.player2 = os.environ.get("MAZE_PLAYER2", "")
        self.state = "name_entry"
        self.name_index = 0
        self.keyboard_index = 0
        self.keyboard_keys = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("0123456789") + ["SPACE", "DEL", "CLEAR"]
        self.last_master_status_time = 0
        self.cached_master_status = system_status()
        self.last_state_send = 0
        self.master_dx, self.master_dy = 0, 0
        self.master_move_time = 0.0
        self.last_score = 0
        self.last_duration = 0
        self.init_game()

    def get_master_status_cached(self):
        """Cache master diagnostics to avoid expensive polling every frame."""
        now = time.time()
        if now - self.last_master_status_time >= MASTER_STATUS_DELAY:
            self.cached_master_status = system_status()
            self.last_master_status_time = now
        return self.cached_master_status

    def init_game(self):
        """Create a new maze session and reset all gameplay counters."""
        while True:
            self.maze = generate_maze(COLS, ROWS)
            self.start, self.end = choose_far_start_end(self.maze)
            if path_exists(self.start, self.end, self.maze, []):
                break
        self.player = self.start[:]
        self.holes, self.stars = [], []
        while len(self.holes) < HOLE_COUNT:
            h = random_empty(self.maze, [self.start, self.end] + self.holes)
            self.holes.append(h)
            if not path_exists(self.start, self.end, self.maze, self.holes):
                self.holes.pop()
        reachable = list(bfs_distances(self.start, self.maze, self.holes).keys())
        reachable_cells = [list(c) for c in reachable]
        while len(self.stars) < STAR_COUNT:
            s = random.choice(reachable_cells)
            if s not in self.holes and s != self.start and s != self.end and s not in self.stars:
                self.stars.append(s)
        while True:
            self.monster = random.choice(reachable_cells)
            if self.monster not in self.holes and self.monster not in self.stars and self.monster != self.start and self.monster != self.end:
                break
        self.stars_collected, self.monster_hits, self.hole_hits = 0, 0, 0
        self.start_time = time.time()
        self.last_hit_time, self.monster_timer, self.input_timer = 0, 0, 0
        self.build_static_maze_surface()

    def build_static_maze_surface(self):
        """Pre-render static maze elements to reduce LCD drawing load."""
        surface = pygame.Surface((MAZE_W, MAZE_H))
        for y, row in enumerate(self.maze):
            for x, value in enumerate(row):
                pygame.draw.rect(surface, WALL if value else PATH, (x * TILE, y * TILE, TILE, TILE))
        sx, sy, ex, ey = self.start[0], self.start[1], self.end[0], self.end[1]
        pygame.draw.polygon(surface, START_C, [(sx*TILE+TILE//2, sy*TILE+1), (sx*TILE+1, sy*TILE+TILE-1), (sx*TILE+TILE-1, sy*TILE+TILE-1)])
        pygame.draw.polygon(surface, END_C, [(ex*TILE+TILE//2, ey*TILE+1), (ex*TILE+1, ey*TILE+TILE-1), (ex*TILE+TILE-1, ey*TILE+TILE-1)])
        for h in self.holes:
            pygame.draw.circle(surface, HOLE_C, (h[0]*TILE+TILE//2, h[1]*TILE+TILE//2), max(3, TILE//3))
        self.static_maze_surface = surface

    def score(self):
        """Compute the current score from stars, penalties, and elapsed time."""
        return self.stars_collected * 1000 - self.monster_hits * 50 - self.hole_hits * 30 - int(time.time() - self.start_time) * 2

    def move_player(self, dx, dy):
        """Move the player if the target cell is inside the maze path."""
        nx, ny = self.player[0] + dx, self.player[1] + dy
        if 0 <= nx < COLS and 0 <= ny < ROWS and self.maze[ny][nx] == 0:
            self.player = [nx, ny]

    def move_monster(self):
        """Move the monster one step toward the player when possible."""
        if not MONSTER_ENABLED:
            return
        dirs = []
        if self.player[0] > self.monster[0]:
            dirs.append((1, 0))
        if self.player[0] < self.monster[0]:
            dirs.append((-1, 0))
        if self.player[1] > self.monster[1]:
            dirs.append((0, 1))
        if self.player[1] < self.monster[1]:
            dirs.append((0, -1))
        dirs += [(1, 0), (-1, 0), (0, 1), (0, -1)]
        random.shuffle(dirs)
        for dx, dy in dirs:
            nx, ny = self.monster[0] + dx, self.monster[1] + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS and self.maze[ny][nx] == 0 and [nx, ny] not in self.holes:
                self.monster = [nx, ny]
                return

    def check_events(self):
        """Handle star collection, hole penalties, and monster collision penalties."""
        now = time.time()
        if self.player in self.stars:
            self.stars.remove(self.player[:])
            self.stars_collected += 1
        if now - self.last_hit_time < HIT_COOLDOWN:
            return
        if self.player in self.holes:
            self.hole_hits += 1
            self.player = self.start[:]
            self.last_hit_time = now
        elif self.player == self.monster:
            self.monster_hits += 1
            self.player = self.start[:]
            self.last_hit_time = now

    def draw_maze(self):
        """Render dynamic maze objects on top of the static maze background."""
        screen.blit(self.static_maze_surface, (0, 0))
        for s in self.stars:
            cx, cy = s[0]*TILE+TILE//2, s[1]*TILE+TILE//2
            pygame.draw.circle(screen, STAR_C, (cx, cy), max(3, TILE//4))
        mx, my = self.monster[0]*TILE+TILE//2, self.monster[1]*TILE+TILE//2
        pygame.draw.circle(screen, MONSTER_C, (mx, my), max(5, TILE//2 - 1))
        px, py = self.player[0]*TILE+TILE//2, self.player[1]*TILE+TILE//2
        pygame.draw.circle(screen, PLAYER_C, (px, py), max(5, TILE//2 - 1))

    def draw_panel(self):
        """Render compact gameplay and device-status information."""
        snap = self.net.snapshot()
        master = self.get_master_status_cached()
        client = snap["status"]
        elapsed = int(time.time() - self.start_time)
        pygame.draw.rect(screen, PANEL_C, (PANEL_X, 0, PANEL_W, WIN_H))
        pygame.draw.line(screen, CYAN, (PANEL_X, 0), (PANEL_X, WIN_H), 1)
        lines = [
            "Team:", self.team[:10],
            f"P2:{snap['name'][:9]}",
            f"S {self.stars_collected}/{STAR_COUNT}",
            f"Score {self.score()}",
            f"T {elapsed}s",
            f"C {'ON' if snap['connected'] else 'OFF'}",
            f"J {'YES' if snap['joined'] else 'NO'}",
            f"Lat {snap['latency']:.0f}ms",
            f"MCPU {master.get('cpu')}",
            f"MT {master.get('temp')}",
            f"CCPU {client.get('cpu', '-')}",
            f"CT {client.get('temp', '-')}",
        ]
        y = 4
        for line in lines:
            draw_text(line, PANEL_X + 4, y, WHITE, F_TINY)
            y += 18


    def get_name_values(self):
        """Return the current team and player-name fields."""
        return [self.team, self.player1, self.player2]

    def set_name_values(self, values):
        """Update the team and player-name fields."""
        self.team, self.player1, self.player2 = values

    def names_complete(self):
        """Check whether all required name fields are filled."""
        return bool(self.team.strip() and self.player1.strip() and self.player2.strip())

    def apply_keyboard_select(self):
        """Apply the selected on-screen keyboard key to the active field."""
        values = self.get_name_values()
        key = self.keyboard_keys[self.keyboard_index]
        text_value = values[self.name_index]

        if key == "SPACE":
            if len(text_value) < 12:
                text_value += " "
        elif key == "DEL":
            text_value = text_value[:-1]
        elif key == "CLEAR":
            text_value = ""
        else:
            if len(text_value) < 12:
                text_value += key

        values[self.name_index] = text_value
        self.set_name_values(values)

    def apply_name_enter(self):
        """Confirm the active name field and advance the setup state."""
        # B4 confirms the active field and advances setup.
        values = self.get_name_values()
        if not values[self.name_index].strip():
            return
        if self.name_index < 2:
            self.name_index += 1
            self.keyboard_index = 0
        elif self.names_complete():
            self.state = "waiting_client"

    def draw_name_entry(self):
        """Render the on-screen name-entry interface."""
        screen.fill(BG)
        labels = ["Team", "Player 1", "Player 2"]
        values = self.get_name_values()

        draw_text("ENTER NAMES", max(10, WIN_W//2 - 120), 6, CYAN, F_BIG)
        draw_text("B7/B8: move   B5 blue: select   B4 yellow: enter", 10, 42, GRAY, F_TINY)

        field_y = 62
        field_h = 30
        for i, label in enumerate(labels):
            y = field_y + i * 36
            color = YELLOW if i == self.name_index else GRAY
            draw_text(f"{label}:", 14, y + 4, color, F_NAME)
            rect = pygame.Rect(132, y, WIN_W - 150, field_h)
            pygame.draw.rect(screen, (22, 34, 54), rect, border_radius=5)
            pygame.draw.rect(screen, color, rect, 2, border_radius=5)
            cursor = "_" if i == self.name_index else ""
            draw_text(values[i] + cursor, rect.x + 8, rect.y + 5, WHITE, F_NAME)

        keys = self.keyboard_keys
        cols = 10
        key_w = 43
        key_h = 25
        gap = 3
        start_x = 14
        start_y = 178
        for idx, key in enumerate(keys):
            row = idx // cols
            col = idx % cols
            x = start_x + col * (key_w + gap)
            y = start_y + row * (key_h + gap)
            w = key_w + 12 if key in ["SPACE", "CLEAR"] else key_w
            rect = pygame.Rect(x, y, w, key_h)
            selected = idx == self.keyboard_index
            pygame.draw.rect(screen, (45, 65, 95) if selected else (30, 42, 62), rect, border_radius=5)
            pygame.draw.rect(screen, RED if selected else CYAN, rect, 2 if selected else 1, border_radius=5)
            label = "SP" if key == "SPACE" else key
            draw_text(label, rect.x + 5, rect.y + 4, YELLOW if selected else WHITE, F_KEY)

        draw_text("Blue B5 selects. Yellow B4 confirms.", 10, WIN_H - 18, GRAY, F_TINY)

    def draw_waiting(self):
        """Render the waiting screen until the client joins."""
        screen.fill(BG)
        snap = self.net.snapshot()
        draw_text("Waiting for Client", max(10, WIN_W//2 - 200), int(WIN_H * 0.13), YELLOW, F_BIG)
        draw_text("1) Run client script", max(10, WIN_W//2 - 160), int(WIN_H * 0.30), WHITE, F_MED)
        draw_text("2) Client joins automatically", max(10, WIN_W//2 - 230), int(WIN_H * 0.42), WHITE, F_MED)
        draw_text(f"Connected: {'YES' if snap['connected'] else 'NO'}", max(10, WIN_W//2 - 160), int(WIN_H * 0.62), GREEN if snap['connected'] else RED, F_MED)
        draw_text(f"P1:{self.player1} P2:{self.player2}", max(10, WIN_W//2 - 200), int(WIN_H * 0.85), CYAN, F_SMALL)

    def draw_result(self):
        """Render final score, session metrics, and Pi diagnostics."""
        screen.fill(BG)
        snap = self.net.snapshot()
        master = self.get_master_status_cached()
        client = snap["status"]

        draw_text("FINAL RESULTS", max(8, WIN_W//2 - 130), 5, WHITE, F_BIG)

        game_lines = [
            "GAME DATA",
            f"Team: {self.team}",
            f"P1: {self.player1}",
            f"P2: {self.player2}",
            f"Score: {self.last_score}",
            f"Stars: {self.stars_collected}/{STAR_COUNT}",
            f"Monster: {self.monster_hits}",
            f"Holes: {self.hole_hits}",
            f"Session: {self.last_duration}s",
            f"Overall: {overall_time_played()}s",
        ]

        master_lines = [
            "MASTER PI",
            f"CPU: {master.get('cpu','-')}",
            f"RAM: {master.get('ram','-')}",
            f"Temp: {master.get('temp','-')}",
            f"Uptime: {master.get('uptime','-')}",
            f"Disk: {master.get('disk','-')}",
            f"Python: {master.get('python','-')}",
            f"Build: {master.get('build','-')[:12]}",
        ]

        client_lines = [
            "CLIENT PI",
            f"Conn: {'ON' if snap['connected'] else 'OFF'}",
            f"Join: {'YES' if snap['joined'] else 'NO'}",
            f"Latency: {snap['avg_latency']:.0f}ms",
            f"Errors: {snap['packet_errors']}",
            f"CPU: {client.get('cpu','-')}",
            f"RAM: {client.get('ram','-')}",
            f"Temp: {client.get('temp','-')}",
            f"Build: {client.get('build','-')[:12]}",
        ]

        x1, x2, x3 = 8, 166, 322
        y0 = 42
        line_h = 23 if WIN_H >= 320 else 20

        for i, line in enumerate(game_lines):
            font = F_RESULT if i in [0, 4] else F_RESULT_SMALL
            draw_text(line, x1, y0 + i * line_h, YELLOW if i == 0 else WHITE, font)

        for i, line in enumerate(master_lines):
            font = F_RESULT if i == 0 else F_RESULT_SMALL
            draw_text(line, x2, y0 + i * line_h, GREEN if i == 0 else WHITE, font)

        for i, line in enumerate(client_lines):
            font = F_RESULT if i == 0 else F_RESULT_SMALL
            draw_text(line, x3, y0 + i * line_h, CYAN if i == 0 else WHITE, font)

        draw_text("B1: play again   B3: new names", max(8, WIN_W//2 - 150), WIN_H - 22, GRAY, F_TINY)

    def finish_game(self):
        """Freeze final metrics and save the completed session to CSV."""
        self.last_duration = int(time.time() - self.start_time)
        self.last_score = self.score()
        snap = self.net.snapshot()
        row = {
            "rank": 0, "timestamp": datetime.now().isoformat(timespec="seconds"), "team": self.team,
            "player1": self.player1, "player2": self.player2, "score": self.last_score, "stars": self.stars_collected,
            "monster_hits": self.monster_hits, "hole_hits": self.hole_hits, "duration_s": self.last_duration,
            "overall_time_s": overall_time_played() + self.last_duration, "last_played": datetime.now().isoformat(timespec="seconds"),
            "avg_latency_ms": round(snap["avg_latency"], 1), "packet_errors": snap["packet_errors"]
        }
        save_score(row)

    def reset_game(self):
        """Reset game state and require a new name/client setup."""
        self.net.reset_join()
        self.init_game()
        self.state = "name_entry"
        self.name_index = 0
        self.keyboard_index = 0

    def run(self):
        """Execute the main event loop and game-state machine."""
        running = True
        prev_b1, prev_b3, prev_b4, prev_b5, prev_b7, prev_b8 = False, False, False, False, False, False
        last_b1_time, last_b3_time, last_b4_time, last_b5_time, last_b7_time, last_b8_time = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        BUTTON_DEBOUNCE = 0.18
        last_display_time = 0.0
        DISPLAY_INTERVAL = 1.0 / 8.0  # limited refresh rate to keep LCD rendering stable

        try:
            while running:
                now = time.time()
                clock.tick(FPS)

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False

                b1 = button_pressed(PIN_B1)
                b3 = button_pressed(PIN_B3)
                b4 = button_pressed(PIN_B4)
                b5 = button_pressed(PIN_B5)
                b7 = button_pressed(PIN_B7)
                b8 = button_pressed(PIN_B8)

                b1_edge = b1 and not prev_b1 and (now - last_b1_time) >= BUTTON_DEBOUNCE
                b3_edge = b3 and not prev_b3 and (now - last_b3_time) >= BUTTON_DEBOUNCE
                b4_edge = b4 and not prev_b4 and (now - last_b4_time) >= BUTTON_DEBOUNCE
                b5_edge = b5 and not prev_b5 and (now - last_b5_time) >= BUTTON_DEBOUNCE
                b7_edge = b7 and not prev_b7 and (now - last_b7_time) >= BUTTON_DEBOUNCE
                b8_edge = b8 and not prev_b8 and (now - last_b8_time) >= BUTTON_DEBOUNCE

                if b1_edge:
                    last_b1_time = now
                if b3_edge:
                    last_b3_time = now
                if b4_edge:
                    last_b4_time = now
                if b5_edge:
                    last_b5_time = now
                if b7_edge:
                    last_b7_time = now
                if b8_edge:
                    last_b8_time = now

                if b3_edge:
                    if self.state == "playing":
                        self.finish_game()
                        self.state = "result"
                    else:
                        self.reset_game()

                # Setup state: collect team and player names locally on the master.
                if self.state == "name_entry":
                    if b7_edge:
                        self.keyboard_index = (self.keyboard_index - 1) % len(self.keyboard_keys)
                    if b8_edge:
                        self.keyboard_index = (self.keyboard_index + 1) % len(self.keyboard_keys)
                    if b5_edge:
                        self.apply_keyboard_select()
                    if b4_edge:
                        self.apply_name_enter()
                    if now - last_display_time >= DISPLAY_INTERVAL:
                        self.draw_name_entry()

                # Synchronization state: wait until the client confirms joining.
                elif self.state == "waiting_client":
                    if now - last_display_time >= DISPLAY_INTERVAL:
                        self.draw_waiting()
                    snap = self.net.snapshot()
                    if snap["connected"] and snap["joined"]:
                        self.init_game()
                        self.state = "playing"

                # Runtime state: merge master/client input, update game logic, and transmit status.
                elif self.state == "playing":
                    if now - self.input_timer >= INPUT_DELAY:
                        mdx, mdy = read_master_input()
                        if mdx != 0 or mdy != 0:
                            self.master_dx, self.master_dy, self.master_move_time = mdx, mdy, now
                        if now - self.master_move_time <= MASTER_MOVE_HOLD_TIME:
                            mdx, mdy = self.master_dx, self.master_dy
                        else:
                            mdx, mdy = 0, 0
                        cdx, cdy = self.net.get_move()
                        if mdx != 0 or mdy != 0:
                            self.move_player(mdx, mdy)
                        if cdx != 0 or cdy != 0:
                            self.move_player(cdx, cdy)
                            self.net.consume_move()
                        self.input_timer = now

                    if now - self.monster_timer >= MONSTER_DELAY:
                        self.move_monster()
                        self.monster_timer = now

                    self.check_events()
                    if self.player == self.end:
                        self.finish_game()
                        self.state = "result"

                    if now - last_display_time >= DISPLAY_INTERVAL:
                        # Gameplay uses the lightweight maze-only render path.
                        screen.fill(BG)
                        self.draw_maze()

                    if now - self.last_state_send >= STATE_SEND_DELAY:
                        self.net.send_state({
                            "type": "playing", "player": self.player, "score": self.score(),
                            "stars": self.stars_collected, "time": int(time.time() - self.start_time),
                        })
                        self.last_state_send = now

                # Result state: display final metrics and allow a new session.
                elif self.state == "result":
                    if now - last_display_time >= DISPLAY_INTERVAL:
                        self.draw_result()
                    if b1_edge:
                        self.init_game()
                        self.state = "playing"

                prev_b1, prev_b3, prev_b4, prev_b5, prev_b7, prev_b8 = b1, b3, b4, b5, b7, b8

                if now - last_display_time >= DISPLAY_INTERVAL:
                    update_display()
                    last_display_time = now

        finally:
            self.net.stop()
            pygame.quit()
            if ON_PI:
                GPIO.cleanup()


if __name__ == "__main__":
    Game().run()
