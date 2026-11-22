
        #deque(maxlen=10) یعنی فقط آخرین ۱۰ نمونه را نگه دار.
        #برای اینکه میانگین latency از چند نمونه آخر حساب شود و خیلی نوسانی نباشد.
        self.latency_samples = deque(maxlen=10)
        #این شمارنده برای packetهای خراب یا خطاهای ارتباطی است.
        self.packet_errors = 0
        #rx یعنی receive.
        #این متغیر زمان آخرین packet دریافتی از Client را نگه می‌دارد.
        self.last_rx = 0.0   # timestamp used for client timeout detection
        #7. یک thread جدا برای دریافت پیام‌های شبکه شروع کن.
        threading.Thread(target=self.loop, daemon=True).start()


#این تابع بعد از ساختن NetworkMaster در background اجرا می‌شود.
    def loop(self):
        """Continuously process incoming UDP packets from the client device."""
        #یعنی تا وقتی شبکه فعال است، این حلقه ادامه داشته باشد.
        while self.running:
            try:
                #یعنی تلاش کن پیام‌های UDP را دریافت و پردازش کنی.

                # Drain pending packets so the game uses the latest client input
                
                #یعنی همه‌ی packetهایی که فعلاً منتظر دریافت هستند را یکی‌یکی بخوان.
                while True:
                    data, addr = self.rx.recvfrom(2048)
                    #یعنی از socket دریافت‌کننده‌ی Master یک پیام UDP بگیر.
                    now = time.time()
                    #زمان فعلی را ذخیره می‌کند.
                    try:
                        msg = json.loads(data.decode())
                        #یعنی داده‌ی UDP اول از bytes به متن تبدیل می‌شود:
                        #بعد متن JSON به dictionary پایتون تبدیل می‌شود:
                    except Exception:
                        continue
                    #چون این loop در thread جدا اجرا می‌شود، برای تغییر متغیرهای مشترک از lock استفاده می‌کند.
                    with self.lock:
                        #یعنی Master می‌فهمد پیام از کدام IP و port آمده است.
                        self.client_addr = addr
                        self.client_connected = True
                        self.last_rx = now   # refresh timeout timestamp after valid packet reception
                        #نوع پیام را از داخل JSON می‌گیرد.
                        msg_type = msg.get("type")
                        if msg_type in ["hello", "join_confirm"]:
                            #اسم Client را از پیام می‌گیرد.
                            self.client_name = msg.get("name", "Client")
                            #یعنی Client آماده و joined است.
                            self.client_joined = True
                            #یعنی اگر Client هنوز joined نباشد، حرکتش نادیده گرفته می‌شود.
                        elif msg_type == "move" and self.client_joined:
                            self.client_dx = int(msg.get("dx", 0))
                            self.client_dy = int(msg.get("dy", 0))
                            #زمان دریافت آخرین حرکت Client را ذخیره می‌کند.
                            self.client_move_time = now
                            #اگر پیام از نوع status باشد.
                        elif msg_type == "status":
                            #وضعیت سیستم Client را ذخیره می‌کند.
                            self.client_status = msg.get("status", {})
                        elif msg_type == "ack":
                            try:
                                #از پیام ACK مقدار master_sent_time را می‌گیرد.
                                sent = float(msg.get("master_sent_time", 0))
                                #اینجا latency را حساب می‌کند.
                                #ضرب در 1000 برای تبدیل ثانیه به میلی‌ثانیه است.
                                lat = (now - sent) * 1000
                                #یعنی latency نباید منفی باشد و نباید بیشتر از ۵ ثانیه باشد.
                                #اگر خیلی غیرعادی باشد، نادیده گرفته می‌شود.
                                if 0 <= lat <= 5000:
                                    #آخرین مقدار latency را ذخیره می‌کند.
                                    self.latency_ms = lat
                                    #این latency را به لیست نمونه‌های latency اضافه می‌کند.
                                    self.latency_samples.append(lat)
                                    #یعنی فقط ۱۰ نمونه آخر نگه داشته می‌شود.
                                    deque(maxlen=10)
                            except Exception:
                                pass
            except BlockingIOError:
                pass
            except Exception:
                pass

            # Mark the client offline if no packet is received within the timeout.
            #چون متغیرهای Client بین network thread و game loop مشترک هستند، با lock محافظت می‌شوند.
            with self.lock:
                #اینجا بررسی می‌کند آیا Client timeout شده یا نه.
                #یعنی قبلاً حداقل یک پیام معتبر از Client دریافت شده باشد.
                if self.last_rx > 0 and (time.time() - self.last_rx) > CLIENT_TIMEOUT:
                    #یعنی Client دیگر متصل و آماده‌ی بازی حساب نمی‌شود.
                    self.client_connected = False
                    self.client_joined = False
#یعنی network loop کمی صبر کند؛ فقط ۰.۰۱ ثانیه.
                    #این باعث می‌شود CPU بی‌دلیل ۱۰۰٪ درگیر نشود.
            time.sleep(0.01)


#این تابع آخرین حرکت معتبر Client را به game loop می‌دهد.
    def get_move(self):
        """Return the latest valid client movement within its hold interval."""
        #چون دارد client_dx, client_dy, و client_move_time را می‌خواند، از lock استفاده می‌کند.
        with self.lock:
            if time.time() - self.client_move_time <= CLIENT_MOVE_HOLD_TIME:
                return self.client_dx, self.client_dy
            return 0, 0

#این تابع حرکت Client را بعد از استفاده پاک می‌کند.
    def consume_move(self):
        """Clear the consumed client movement command."""
        with self.lock:
            self.client_dx, self.client_dy = 0, 0
            self.client_move_time = 0.0


#این تابع وضعیت فعلی بازی را از Master به Client می‌فرستد.
    def send_state(self, state):
        """Send the current game state to the client device."""
        #اینجا آدرس Client را به صورت thread-safe می‌خواند.
        #(IP address, port)
        #("192.168.77.66", 5006)
        with self.lock:
            #اگر هنوز Client هیچ پیامی نفرستاده باشد، Master آدرس Client را نمی‌داند.
            addr = self.client_addr
        if not addr:
            return
        try:
            state["master_sent_time"] = time.time()
            #اینجا پیام ارسال می‌شود.
            #دیکشنری Python را به متن JSON تبدیل می‌کند.
            self.tx.sendto(json.dumps(state).encode(), (addr[0], UDP_CLIENT_PORT))
        except Exception:
            pass


#این تابع وضعیت join مربوط به Client را reset می‌کند و به Client می‌گوید دوباره join شود.
    def reset_join(self):
        """Clear join state and request the client to rejoin."""
        with self.lock:
            #داخل lock چند کار انجام می‌دهد:
            #یعنی Client دیگر joined حساب نمی‌شود.
            #یعنی حرکت قبلی Client پاک می‌شود.
            #آدرس Client هم ذخیره می‌شود تا بتواند پیام reset بفرستد.
            self.client_joined = False
            self.client_dx = 0
            self.client_dy = 0
            addr = self.client_addr
            #اگر آدرس Client موجود باشد، یعنی Master قبلاً از Client پیام گرفته و می‌داند به کجا باید پیام بفرستد.
        if addr:
            try:
                #اینجا Master یک پیام به Client می‌فرستد:
                self.tx.sendto(json.dumps({"type": "reset_join"}).encode(), (addr[0], UDP_CLIENT_PORT))
            except Exception:
                #اگر ارسال پیام reset خطا بدهد، برنامه متوقف نمی‌شود.
                pass


#این تابع یک کپی امن از وضعیت فعلی شبکه و Client برمی‌گرداند.
    def snapshot(self):
        """Return a thread-safe copy of current network/client status."""
        #چون اطلاعات شبکه ممکن است همزمان توسط thread شبکه تغییر کند، اینجا با lock خوانده می‌شود.
        with self.lock:
            #اینجا میانگین latency حساب می‌شود.
            avg = sum(self.latency_samples) / len(self.latency_samples) if self.latency_samples else self.latency_ms
            return {
                #آیا Client به Master متصل است یا نه.
                "connected": self.client_connected,
                #آیا Client وارد بازی شده و آماده است یا نه.
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

#یعنی تابعی تعریف می‌شود که با گرفتن عرض w و ارتفاع h، یک maze بسازد.
def generate_maze(w, h):
    #یک maze قابل حل با استفاده از الگوریتم randomized depth-first backtracking بساز.
    """Generate a solvable maze using randomized depth-first backtracking."""
    #همه خانه‌ها در ابتدا 1 هستند.
    maze = [[1] * w for _ in range(h)]
    #نقطه شروع الگوریتم را مشخص می‌کند.
    #اینجا از خانه‌ی (1, 1) شروع می‌کند، نه از (0, 0).
    #چون خانه‌های لبه‌ی maze معمولاً دیوار باقی می‌مانند.
    start_x, start_y = 1, 1
    maze[start_y][start_x] = 0
    #اینجا یک stack ساخته می‌شود و نقطه شروع داخل آن قرار می‌گیرد.
    #stack برای الگوریتم backtracking استفاده می‌شود.
    #یعنی مسیرهایی که رفته‌ایم را نگه می‌دارد تا اگر به بن‌بست رسیدیم، برگردیم عقب.
    stack = [(start_x, start_y)]
    #یعنی تا وقتی stack خالی نشده، ساخت maze ادامه پیدا کند.
    while stack:
        #آخرین خانه‌ی stack را می‌گیرد.
        x, y = stack[-1]
        #چون بین دو خانه باید یک دیوار باشد که بعداً شکسته شود.
        #(2, 0)   → دو خانه به راست
        #(-2, 0)  → دو خانه به چپ
        #(0, 2)   → دو خانه پایین
#(0, -2)  → دو خانه بالا

        directions = [(2, 0), (-2, 0), (0, 2), (0, -2)]
        #ترتیب جهت‌ها را تصادفی می‌کند.
        random.shuffle(directions)
        #یعنی فعلاً از خانه فعلی حرکتی انجام نشده است.
        moved = False
        #یعنی هر چهار جهت را یکی‌یکی بررسی کن.
        for dx, dy in directions:
            #مختصات خانه بعدی را حساب می‌کند.
            nx, ny = x + dx, y + dy
            #یعنی خانه جدید از نظر افقی داخل maze باشد و روی لبه نباشد.
            #یعنی خانه جدید از نظر عمودی داخل maze باشد و روی لبه نباشد.
            #یعنی خانه جدید هنوز دیوار باشد و قبلاً باز نشده باشد.
            if 1 <= nx < w - 1 and 1 <= ny < h - 1 and maze[ny][nx] == 1:
                #خانه بعدی را از دیوار به مسیر آزاد تبدیل می‌کند.
                maze[ny][nx] = 0
                #این خط دیوار بین خانه فعلی و خانه بعدی را باز می‌کند.
                #چون حرکت دو خانه دو خانه است، دیوار وسط در فاصله نصف قرار دارد.
                maze[y + dy // 2][x + dx // 2] = 0
                #خانه جدید را به stack اضافه می‌کند.
                stack.append((nx, ny))
                moved = True
                break
            #اگر از خانه فعلی هیچ مسیر جدیدی پیدا نشد، یعنی به بن‌بست رسیده‌ایم.
            #پس آخرین خانه از stack حذف می‌شود.
            #این همان بخش backtracking است.
        if not moved:
            stack.pop()
    return maze



#این تابع از یک خانه‌ی شروع، فاصله‌ی کوتاه‌ترین مسیر تا خانه‌های دیگر maze را حساب می‌کند.
def bfs_distances(start, maze, blocks=None):
    """Calculate shortest path distances from a start cell."""
    if blocks is None:
        blocks = []
        #لیست خانه‌های blocked را به set تبدیل می‌کند.
    blocked = {tuple(b) for b in blocks}
    #اینجا یک صف برای BFS ساخته می‌شود.
    q = deque([tuple(start)])
    #اینجا dictionary فاصله‌ها ساخته می‌شود.
    dist = {tuple(start): 0}
    #تا وقتی صف خالی نشده، BFS ادامه دارد.
    while q:
        #اولین خانه از صف بیرون آورده می‌شود.
        x, y = q.popleft()
        #چهار جهت اطراف خانه را بررسی می‌کند:
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            #مختصات خانه‌ی همسایه را حساب می‌کند.
            nx, ny = x + dx, y + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS:
                #این شرط سه چیز را بررسی می‌کند:
                #یعنی خانه مسیر آزاد باشد، نه دیوار.
                #یعنی خانه جزو خانه‌های ممنوع یا blocked نباشد.
                #فاصله‌ی خانه‌ی جدید را ثبت می‌کند.
                if maze[ny][nx] == 0 and (nx, ny) not in blocked and (nx, ny) not in dist:
                    dist[(nx, ny)] = dist[(x, y)] + 1
                    q.append((nx, ny))
    return dist

#این تابع دو خانه‌ی دور از هم را برای شروع و پایان بازی انتخاب می‌کند.
def choose_far_start_end(maze):
    """Choose distant start and end cells to increase maze length."""
    #اینجا همه خانه‌های آزاد maze را پیدا می‌کند.
    #یعنی فقط خانه‌هایی که مقدارشان 0 است.
    empty = [(x, y) for y in range(ROWS) for x in range(COLS) if maze[y][x] == 0]
    #یک خانه‌ی آزاد را به صورت تصادفی انتخاب می‌کند.
    first = random.choice(empty)
    #از آن خانه‌ی تصادفی، فاصله تا همه‌ی خانه‌های قابل دسترسی را حساب می‌کند.
    d1 = bfs_distances(first, maze)
    #دورترین خانه نسبت به first را پیدا می‌کند.
    far1 = max(d1, key=d1.get)
    #حالا دوباره BFS اجرا می‌کند، ولی این بار از far1.
    d2 = bfs_distances(far1, maze)
    #دورترین خانه نسبت به far1 را پیدا می‌کند.
    far2 = max(d2, key=d2.get)
    #در آخر این دو نقطه را به صورت list برمی‌گرداند.
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
    #اگر DIRECT_FB فعال نباشد، برنامه یک window معمولی Pygame باز می‌کند.
    screen = pygame.display.set_mode((WIN_W, WIN_H))
#اینجا اندازه هر خانه maze حساب می‌شود.
TILE = max(14, min(WIN_W // COLS, WIN_H // ROWS))
MAZE_W, MAZE_H = COLS * TILE, ROWS * TILE
PANEL_X, PANEL_W = MAZE_W, max(0, WIN_W - MAZE_W)
clock = pygame.time.Clock()

FONT_SCALE = max(0.8, min(1.4, WIN_H / 480.0))
#فونت خیلی کوچک.
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


#این بخش شروع کلاس اصلی بازی است.

class Game:
    """Main application class for game logic, rendering, input, and storage."""
    def __init__(self):
        """Initialize runtime state and required subsystems."""
        self.net = NetworkMaster()
        #اینجا برنامه تلاش می‌کند نام تیم را از environment variable بخواند.
        self.team = os.environ.get("MAZE_TEAM", "")
        self.player1 = os.environ.get("MAZE_PLAYER1", "")
        self.player2 = os.environ.get("MAZE_PLAYER2", "")
        #یعنی برنامه اول وارد صفحه وارد کردن نام می‌شود.
        self.state = "name_entry"
        self.name_index = 0
        #این مشخص می‌کند در کیبورد مجازی، الان کدام کاراکتر انتخاب شده است.
        self.keyboard_index = 0
        #اینجا لیست کلیدهای کیبورد مجازی ساخته می‌شود.
        self.keyboard_keys = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("0123456789") + ["SPACE", "DEL", "CLEAR"]
        #این زمان آخرین باری را نگه می‌دارد که status مربوط به Master آپدیت شده است.
        self.last_master_status_time = 0
        #اینجا وضعیت Master گرفته و ذخیره می‌شود.
        self.cached_master_status = system_status()
        self.last_state_send = 0
        #اینجا آخرین حرکت کنترلر Master ذخیره می‌شود.
        self.master_dx, self.master_dy = 0, 0
        self.master_move_time = 0.0
        self.last_score = 0
        self.last_duration = 0
        self.init_game()

#این تابع وضعیت Raspberry Pi Master را می‌دهد، اما به صورت cached.
#یعنی هر بار دوباره CPU/RAM/temp را نمی‌خواند.
    def get_master_status_cached(self):
        """Cache master diagnostics to avoid expensive polling every frame."""
        now = time.time()
        #بررسی می‌کند از آخرین بار که status خوانده شده، آیا به اندازه کافی زمان گذشته یا نه.
        if now - self.last_master_status_time >= MASTER_STATUS_DELAY:
            #اگر زمانش رسیده باشد، دوباره وضعیت Master را می‌خواند.
            self.cached_master_status = system_status()
            #زمان آخرین آپدیت status را ذخیره می‌کند.
            self.last_master_status_time = now
            #در آخر همان status ذخیره‌شده را برمی‌گرداند.
        return self.cached_master_status


#یعنی وقتی بازی شروع یا restart می‌شود، این تابع همه چیز را از نو آماده می‌کند.
    def init_game(self):
        """Create a new maze session and reset all gameplay counters."""
        #یعنی تا وقتی maze مناسب ساخته نشده، ادامه بده.
        while True:
            #یک maze جدید با تعداد ستون‌ها و ردیف‌های مشخص می‌سازد.
            self.maze = generate_maze(COLS, ROWS)
            #برای این maze، یک نقطه شروع و یک نقطه پایان دور از هم انتخاب می‌کند.
            self.start, self.end = choose_far_start_end(self.maze)
            #بررسی می‌کند آیا واقعاً از start می‌شود به end رسید یا نه.
            if path_exists(self.start, self.end, self.maze, []):
                #اگر مسیر وجود داشته باشد، از حلقه خارج می‌شود.
                break
            #بازیکن را در نقطه شروع قرار می‌دهد.
        self.player = self.start[:]
        #لیست holeها و starها را خالی می‌کند.
        self.holes, self.stars = [], []
        #تا وقتی تعداد holeها به مقدار موردنظر نرسیده، ادامه بده.
        while len(self.holes) < HOLE_COUNT:
            #یک خانه آزاد تصادفی انتخاب می‌کند.
            h = random_empty(self.maze, [self.start, self.end] + self.holes)
            self.holes.append(h)
            #بعد بررسی می‌کند آیا با اضافه شدن این hole هنوز مسیر از start به end وجود دارد یا نه.
            if not path_exists(self.start, self.end, self.maze, self.holes):
                self.holes.pop()
                #از start شروع می‌کند و با BFS همه خانه‌هایی را که قابل رسیدن هستند پیدا می‌کند.
        reachable = list(bfs_distances(self.start, self.maze, self.holes).keys())
        #خانه‌های reachable را از tuple به list تبدیل می‌کند.
        reachable_cells = [list(c) for c in reachable]
        while len(self.stars) < STAR_COUNT:
            #یک خانه تصادفی از خانه‌های reachable انتخاب می‌کند.
            s = random.choice(reachable_cells)
            #اگر star روی hole، start، end یا star قبلی نبود، آن را اضافه می‌کند.
            if s not in self.holes and s != self.start and s != self.end and s not in self.stars:
                self.stars.append(s)
                #تا وقتی monster جای مناسب پیدا نکرده، ادامه بده.
        while True:
            #monster را در یک خانه قابل دسترس تصادفی قرار می‌دهد.
            self.monster = random.choice(reachable_cells)
            #اگر monster روی hole، star، start یا end نبود، جای آن قبول می‌شود.
            if self.monster not in self.holes and self.monster not in self.stars and self.monster != self.start and self.monster != self.end:
                break
            #در شروع بازی جدید: همه صفر می‌شوند.
        self.stars_collected, self.monster_hits, self.hole_hits = 0, 0, 0
        #زمان شروع بازی ذخیره می‌شود.
        self.start_time = time.time()
        #چند timer ریست می‌شوند:
        #last_hit_time → آخرین زمان برخورد
        #monster_timer → زمان‌بندی حرکت monster
        #input_timer → زمان‌بندی خواندن input
        self.last_hit_time, self.monster_timer, self.input_timer = 0, 0, 0
        #بخش ثابت maze را از قبل رسم می‌کند و آماده نگه می‌دارد.
        self.build_static_maze_surface()


#این تابع قسمت‌هایی از maze را که ثابت هستند، از قبل رسم می‌کند.
    def build_static_maze_surface(self):
        #قسمت‌های ثابت maze را از قبل رسم کن تا فشار رسم روی LCD کمتر شود.
        """Pre-render static maze elements to reduce LCD drawing load."""
        #یک صفحه‌ی مجازی به اندازه maze ساخته می‌شود.
        surface = pygame.Surface((MAZE_W, MAZE_H))
        #ردیف‌های maze را یکی‌یکی بررسی می‌کند.
        for y, row in enumerate(self.maze):
            #داخل هر ردیف، خانه‌ها را یکی‌یکی بررسی می‌کند.
            for x, value in enumerate(row):
                #اینجا هر خانه maze را به صورت یک مربع رسم می‌کند.
                pygame.draw.rect(surface, WALL if value else PATH, (x * TILE, y * TILE, TILE, TILE))
                #مختصات start و end را جدا می‌کند.
        sx, sy, ex, ey = self.start[0], self.start[1], self.end[0], self.end[1]
        pygame.draw.polygon(surface, START_C, [(sx*TILE+TILE//2, sy*TILE+1), (sx*TILE+1, sy*TILE+TILE-1), (sx*TILE+TILE-1, sy*TILE+TILE-1)])
        #اینجا نقطه شروع را به شکل مثلث رسم می‌کند.
        #سه نقطه داخل لیست، سه رأس مثلث هستند.
        #این هم نقطه پایان را به شکل مثلث رسم می‌کند.
        pygame.draw.polygon(surface, END_C, [(ex*TILE+TILE//2, ey*TILE+1), (ex*TILE+1, ey*TILE+TILE-1), (ex*TILE+TILE-1, ey*TILE+TILE-1)])
        #همه holeها را یکی‌یکی بررسی می‌کند.
        for h in self.holes:
            #هر hole را به شکل دایره رسم می‌کند.
            #یعنی شعاع حداقل ۳ پیکسل باشد و معمولاً حدود یک‌سوم اندازه خانه باشد.
            pygame.draw.circle(surface, HOLE_C, (h[0]*TILE+TILE//2, h[1]*TILE+TILE//2), max(3, TILE//3))
            #در آخر surface آماده‌شده را ذخیره می‌کند.
        self.static_maze_surface = surface


#این تابع امتیاز فعلی بازی را حساب می‌کند.
    def score(self):
        """Compute the current score from stars, penalties, and elapsed time."""
        #پس هرچه بازیکن سریع‌تر بازی را تمام کند، score بهتر می‌شود.
        return self.stars_collected * 1000 - self.monster_hits * 50 - self.hole_hits * 30 - int(time.time() - self.start_time) * 2


#این تابع بازیکن را حرکت می‌دهد.
    def move_player(self, dx, dy):
        #UP    → dx = 0,  dy = -1
        #DOWN  → dx = 0,  dy = 1
        #LEFT  → dx = -1, dy = 0
        #RIGHT → dx = 1,  dy = 0
        """Move the player if the target cell is inside the maze path."""
        #مختصات خانه جدید را حساب می‌کند.
        nx, ny = self.player[0] + dx, self.player[1] + dy
        #این شرط بررسی می‌کند آیا حرکت مجاز است یا نه.
        if 0 <= nx < COLS and 0 <= ny < ROWS and self.maze[ny][nx] == 0:
            #یعنی از سمت چپ یا راست از maze بیرون نرود.
            #یعنی از بالا یا پایین از maze بیرون نرود.
            
            
            
            #یعنی خانه مقصد مسیر آزاد باشد، نه دیوار.
            self.player = [nx, ny]



#این تابع حرکت monster را کنترل می‌کند.
    def move_monster(self):
        """Move the monster one step toward the player when possible."""
        #اگر monster غیرفعال باشد، تابع همین‌جا تمام می‌شود.
        if not MONSTER_ENABLED:
            return
        #یک لیست خالی برای جهت‌های حرکت monster ساخته می‌شود.
        #dirs مخفف directions است.
        dirs = []
        #اگر مختصات x بازیکن از monster بزرگ‌تر باشد، یعنی player سمت راست monster است.
        #پس monster اول تلاش می‌کند به راست برود:
        if self.player[0] > self.monster[0]:
            dirs.append((1, 0))
            #اگر player سمت چپ monster باشد، monster تلاش می‌کند به چپ برود:
        if self.player[0] < self.monster[0]:
            dirs.append((-1, 0))
            #اگر player پایین‌تر از monster باشد، monster تلاش می‌کند پایین برود:
        if self.player[1] > self.monster[1]:
            dirs.append((0, 1))
            #اگر player بالاتر از monster باشد، monster تلاش می‌کند بالا برود:
        if self.player[1] < self.monster[1]:
            dirs.append((0, -1))
            #بعد همه جهت‌های ممکن هم به لیست اضافه می‌شوند.
        dirs += [(1, 0), (-1, 0), (0, 1), (0, -1)]
        #ترتیب جهت‌ها را تصادفی می‌کند.
        random.shuffle(dirs)
        #مختصات خانه بعدی monster را حساب می‌کند.
        for dx, dy in dirs:
            #مختصات خانه بعدی monster را حساب می‌کند.
            #این شرط بررسی می‌کند آیا monster می‌تواند به آن خانه برود یا نه.
            #اگر خانه معتبر باشد، monster به آن خانه منتقل می‌شود.
            nx, ny = self.monster[0] + dx, self.monster[1] + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS and self.maze[ny][nx] == 0 and [nx, ny] not in self.holes:
                self.monster = [nx, ny]
                #بعد از اولین حرکت معتبر، تابع تمام می‌شود.
                return


#این تابع اتفاقات مهم بازی را بررسی می‌کند.
    def check_events(self):
        """Handle star collection, hole penalties, and monster collision penalties."""
        #برای کنترل فاصله زمانی بین برخوردها استفاده می‌شود.
        now = time.time()
        #اگر موقعیت player روی یکی از starها باشد.
        if self.player in self.stars:
            #آن star از لیست starها حذف می‌شود.
            self.stars.remove(self.player[:])
            #تعداد starهای جمع‌شده یکی زیاد می‌شود.
            self.stars_collected += 1
            
            #اینجا بررسی می‌کند از آخرین برخورد چقدر گذشته است.
        if now - self.last_hit_time < HIT_COOLDOWN:
            return
        #اگر player روی hole باشد.
        if self.player in self.holes:
            #تعداد برخورد با hole یکی زیاد می‌شود.
            self.hole_hits += 1
            #بازیکن به نقطه شروع برگردانده می‌شود.
            self.player = self.start[:]
            self.last_hit_time = now
            #اگر player و monster دقیقاً در یک خانه باشند.
        elif self.player == self.monster:
            self.monster_hits += 1
            self.player = self.start[:]
            #زمان برخورد ذخیره می‌شود.
            self.last_hit_time = now




#    def draw_maze(self):
        """Render dynamic maze objects on top of the static maze background."""
        #اینجا maze ثابت که قبلاً آماده شده بود، روی صفحه اصلی کپی می‌شود.
        screen.blit(self.static_maze_surface, (0, 0))
        #همه starهای باقی‌مانده را یکی‌یکی بررسی می‌کند.
        for s in self.stars:
            #مختصات مرکز star را حساب می‌کند.
            #چون s[0] و s[1] شماره خانه هستند، باید به مختصات پیکسلی تبدیل شوند.
            cx, cy = s[0]*TILE+TILE//2, s[1]*TILE+TILE//2
            #star را به شکل دایره روی صفحه رسم می‌کند.
            #STAR_C رنگ star است.
            #یعنی شعاع حداقل ۳ پیکسل باشد و معمولاً یک‌چهارم اندازه tile باشد.
            pygame.draw.circle(screen, STAR_C, (cx, cy), max(3, TILE//4))
            #مختصات مرکز monster را حساب می‌کند.
        mx, my = self.monster[0]*TILE+TILE//2, self.monster[1]*TILE+TILE//2
        #monster را به شکل دایره رسم می‌کند.
        pygame.draw.circle(screen, MONSTER_C, (mx, my), max(5, TILE//2 - 1))
        #مختصات مرکز player را حساب می‌کند.
        px, py = self.player[0]*TILE+TILE//2, self.player[1]*TILE+TILE//2
        #player را به شکل دایره رسم می‌کند.
        pygame.draw.circle(screen, PLAYER_C, (px, py), max(5, TILE//2 - 1))




#این تابع پنل کناری را رسم می‌کند.
        #در این پنل اطلاعات بازی و وضعیت دستگاه‌ها نشان داده می‌شود.
    def draw_panel(self):
        """Render compact gameplay and device-status information."""
        #از کلاس NetworkMaster یک snapshot می‌گیرد.
        #snap شامل وضعیت شبکه و Client است، مثل:
        snap = self.net.snapshot()
        #وضعیت Master را می‌گیرد.
        #اما به صورت cached، یعنی هر فریم دوباره خوانده نمی‌شود.
        master = self.get_master_status_cached()
        #جدا
        client = snap["status"]
        #مدت زمان سپری‌شده از شروع بازی را حساب می‌کند.
        elapsed = int(time.time() - self.start_time)
        #یک مستطیل برای پنل کناری رسم می‌کند.
        pygame.draw.rect(screen, PANEL_C, (PANEL_X, 0, PANEL_W, WIN_H))
        #یک خط عمودی بین maze و پنل کناری می‌کشد.
        pygame.draw.line(screen, CYAN, (PANEL_X, 0), (PANEL_X, WIN_H), 1)
        #اینجا لیست متن‌هایی ساخته می‌شود که قرار است در پنل نمایش داده شوند.
        lines = [
            #بعد نام تیم را نشان می‌دهد، ولی فقط ۱۰ کاراکتر اول:
            "Team:", self.team[:10],
            #چون پنل باریک است و متن طولانی جا نمی‌شود.
            #فقط ۹ کاراکتر اول را می‌گیرد تا در پنل جا شود.
            f"P2:{snap['name'][:9]}",
            f"S {self.stars_collected}/{STAR_COUNT}",
            #امتیاز فعلی را نشان می‌دهد.
            f"Score {self.score()}",
            #زمان سپری‌شده بازی را نشان می‌دهد.
            f"T {elapsed}s",
            #وضعیت اتصال Client را نشان می‌دهد.
            f"C {'ON' if snap['connected'] else 'OFF'}",
            #وضعیت join شدن Client را نشان می‌دهد.
            f"J {'YES' if snap['joined'] else 'NO'}",
            #latency را بر حسب میلی‌ثانیه نشان می‌دهد.
            f"Lat {snap['latency']:.0f}ms",
            #درصد مصرف CPU در Master را نشان می‌دهد.
            f"MCPU {master.get('cpu')}",
            #دمای Master را نشان می‌دهد.
            f"MT {master.get('temp')}",
            #اگر دما موجود نبود، - نشان می‌دهد.
            f"CCPU {client.get('cpu', '-')}",
            f"CT {client.get('temp', '-')}",
        ]
        #یعنی متن‌ها از نزدیک بالای پنل شروع می‌شوند.
        y = 4
        #همه متن‌های داخل لیست lines را یکی‌یکی رسم می‌کند.
        for line in lines:
            #هر خط متن را روی پنل می‌نویسد.
            #یعنی کمی داخل‌تر از لبه پنل.
            draw_text(line, PANEL_X + 4, y, WHITE, F_TINY)
            #بعد از هر خط، y را ۱۸ پیکسل پایین‌تر می‌برد.
            y += 18

    def draw_maze(self):
        """Render dynamic maze objects on top of the static maze background."""
        #اول maze ثابت را روی صفحه می‌کشد.
        #static_maze_surface قبلاً ساخته شده و شامل این‌هاست:
        #wall
        #path
        #start
        #end
        #holes
        #چون این بخش‌ها ثابت هستند، هر بار از اول رسم نمی‌شوند. فقط surface آماده روی صفحه گذاشته می‌شود.
        screen.blit(self.static_maze_surface, (0, 0))
        #همه starهای باقی‌مانده را بررسی می‌کند.
        for s in self.stars:
            #مختصات star را از خانه‌ی maze به مختصات پیکسلی تبدیل می‌کند.
            cx, cy = s[0]*TILE+TILE//2, s[1]*TILE+TILE//2
            #star را به شکل دایره رسم می‌کند.
            pygame.draw.circle(screen, STAR_C, (cx, cy), max(3, TILE//4))
            #مختصات monster را حساب می‌کند و monster را به شکل دایره رسم می‌کند.
        mx, my = self.monster[0]*TILE+TILE//2, self.monster[1]*TILE+TILE//2
        #مختصات player را حساب می‌کند و player را روی maze رسم می‌کند.
        pygame.draw.circle(screen, MONSTER_C, (mx, my), max(5, TILE//2 - 1))
        px, py = self.player[0]*TILE+TILE//2, self.player[1]*TILE+TILE//2
        pygame.draw.circle(screen, PLAYER_C, (px, py), max(5, TILE//2 - 1))

    def draw_panel(self):
        """Render compact gameplay and device-status information."""
        #وضعیت شبکه و Client را می‌گیرد.
        snap = self.net.snapshot()
        #وضعیت Master را می‌گیرد، اما به صورت cache شده.
        #یعنی هر فریم CPU و temperature را دوباره نمی‌خواند.
        master = self.get_master_status_cached()
        #وضعیت Client را از snapshot جدا می‌کند.
        client = snap["status"]
        #مدت زمان بازی را حساب می‌کند.
        elapsed = int(time.time() - self.start_time)
        #پس‌زمینه پنل کناری را رسم می‌کند.
        pygame.draw.rect(screen, PANEL_C, (PANEL_X, 0, PANEL_W, WIN_H))
        #یک خط جداکننده بین maze و پنل می‌کشد.
        pygame.draw.line(screen, CYAN, (PANEL_X, 0), (PANEL_X, WIN_H), 1)
        lines = [
            #نام تیم را نشان می‌دهد. فقط ۱۰ کاراکتر اول، چون پنل کوچک است.
            "Team:", self.team[:10],
            #نام Client یا Player 2 را نشان می‌دهد.
            f"P2:{snap['name'][:9]}",
            #تعداد starهای جمع‌شده را نشان می‌دهد.
            f"S {self.stars_collected}/{STAR_COUNT}",
            f"Score {self.score()}",
            #زمان بازی را نشان می‌دهد.
            f"T {elapsed}s",
            #وضعیت اتصال Client را نشان می‌دهد.
            f"C {'ON' if snap['connected'] else 'OFF'}",
            #وضعیت join شدن Client را نشان می‌دهد.
            f"J {'YES' if snap['joined'] else 'NO'}",
            #latency را بر حسب میلی‌ثانیه نشان می‌دهد.
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

#این تابع مقدارهای جدید را داخل متغیرهای اصلی ذخیره می‌کند.
    def set_name_values(self, values):
        """Update the team and player-name fields."""
        self.team, self.player1, self.player2 = values

#این تابع بررسی می‌کند آیا همه نام‌ها کامل وارد شده‌اند یا نه.
    def names_complete(self):
        """Check whether all required name fields are filled."""
        #strip() فاصله‌های اضافی اول و آخر متن را حذف می‌کند.
        return bool(self.team.strip() and self.player1.strip() and self.player2.strip())


#این تابع وقتی اجرا می‌شود که کاربر روی کیبورد مجازی یک کلید را انتخاب کند.
    def apply_keyboard_select(self):
        """Apply the selected on-screen keyboard key to the active field."""
        values = self.get_name_values()
        key = self.keyboard_keys[self.keyboard_index]
        #اگر self.name_index = 1 باشد، یعنی داریم player1 را ویرایش می‌کنیم.
        text_value = values[self.name_index]

        if key == "SPACE":
            #اگر طول متن کمتر از ۱۲ کاراکتر باشد، یک فاصله به متن اضافه می‌کند.
            if len(text_value) < 12:
                text_value += " "
        elif key == "DEL":
            #آخرین کاراکتر متن را حذف می‌کند.
            text_value = text_value[:-1]
        elif key == "CLEAR":
            text_value = ""
        else:
            #اگر طول متن کمتر از ۱۲ باشد، آن حرف یا عدد را به متن اضافه می‌کند.
            if len(text_value) < 12:
                text_value += key
#متن جدید را در همان فیلد فعال ذخیره می‌کند.
        values[self.name_index] = text_value
        #در آخر، مقدارهای جدید را دوباره داخل متغیرهای اصلی ذخیره می‌کند:
        self.set_name_values(values)
        

    #این تابع وقتی اجرا می‌شود که کاربر دکمه Enter را بزند.
#در این کد، دکمه Enter همان دکمه زرد B4 است.
    def apply_name_enter(self):
        """Confirm the active name field and advance the setup state."""
        # B4 confirms the active field and advances setup.
        #مقدارهای فعلی نام‌ها را می‌گیرد.
        values = self.get_name_values()
        #اینجا بررسی می‌کند فیلد فعلی خالی نباشد.
        if not values[self.name_index].strip():
            return
        #اگر هنوز به آخرین فیلد نرسیده‌ایم.
        if self.name_index < 2:
            #می‌رود به فیلد بعدی.
            self.name_index += 1
            #کیبورد مجازی را دوباره می‌برد روی اولین کلید.
            #معمولاً اولین کلید A است.
            self.keyboard_index = 0
            #اگر الان آخرین فیلد یعنی Player 2 هم کامل شده باشد، state بازی عوض می‌شود.
        elif self.names_complete():
            self.state = "waiting_client"

    def draw_name_entry(self):
        """Render the on-screen name-entry interface."""
        #کل صفحه را با رنگ پس‌زمینه پر می‌کند.
        screen.fill(BG)
        #نام فیلدهایی که باید نمایش داده شوند:
        labels = ["Team", "Player 1", "Player 2"]
        #مقدار فعلی هر فیلد را می‌گیرد.
        values = self.get_name_values()
        #عنوان صفحه را می‌نویسد:

        draw_text("ENTER NAMES", max(10, WIN_W//2 - 120), 6, CYAN, F_BIG)
        draw_text("B7/B8: move   B5 blue: select   B4 yellow: enter", 10, 42, GRAY, F_TINY)
#field_y یعنی محل شروع اولین فیلد از بالا.
        #field_h یعنی ارتفاع هر کادر نام.
        field_y = 62
        field_h = 30
        #سه فیلد را یکی‌یکی رسم می‌کند.
        #i شماره فیلد است:
        for i, label in enumerate(labels):
            #        #موقعیت عمودی هر فیلد را حساب می‌کند.

            #هر فیلد ۳۶ پیکسل پایین‌تر از قبلی رسم می‌شود.
            y = field_y + i * 36
            #اگر این فیلد، فیلد فعال باشد، رنگش زرد می‌شود.
            #اگر فعال نباشد، خاکستری می‌شود.
            color = YELLOW if i == self.name_index else GRAY
            #اسم فیلد را می‌نویسد.
            draw_text(f"{label}:", 14, y + 4, color, F_NAME)
            #یک مستطیل برای کادر ورودی می‌سازد.
            rect = pygame.Rect(132, y, WIN_W - 150, field_h)
            #داخل کادر ورودی را با رنگ تیره پر می‌کند.
            #border_radius=5 یعنی گوشه‌های کادر کمی گرد باشد.
            pygame.draw.rect(screen, (22, 34, 54), rect, border_radius=5)
            #دور کادر یک خط رنگی می‌کشد.
            #اگر فیلد فعال باشد، خط زرد است.
            #اگر فعال نباشد، خط خاکستری است.
            #عدد 2 یعنی ضخامت خط دور کادر ۲ پیکسل است.

            pygame.draw.rect(screen, color, rect, 2, border_radius=5)
            #اگر این فیلد فعال باشد، یک cursor به شکل _ نشان می‌دهد.
            #یعنی کاربر بفهمد الان این فیلد در حال ویرایش است.
            #اگر فیلد فعال نباشد، cursor خالی است.
            cursor = "_" if i == self.name_index else ""
            #مقدار واردشده را داخل کادر می‌نویسد.
            #اگر فیلد فعال باشد، _ آخر متن دیده می‌شود.
            #MARYAM_
            draw_text(values[i] + cursor, rect.x + 8, rect.y + 5, WHITE, F_NAME)

#cols = تعداد کلید در هر ردیف
#key_w = عرض هر کلید
#key_h = ارتفاع هر کلید
#gap = فاصله بین کلیدها
#start_x/start_y = محل شروع کیبورد روی صفحه
        keys = self.keyboard_keys
        cols = 10
        key_w = 43
        key_h = 25
        gap = 3
        start_x = 14
        start_y = 178
        #همه کلیدها را یکی‌یکی رسم می‌کند.
        #idx شماره کلید است.
        #key متن کلید است، مثل A یا SPACE.

        for idx, key in enumerate(keys):
            #مشخص می‌کند کلید در کدام ردیف و ستون قرار بگیرد.
            row = idx // cols
            col = idx % cols
            #مختصات هر کلید روی صفحه حساب می‌شود.
            x = start_x + col * (key_w + gap)
            y = start_y + row * (key_h + gap)
            #اگر کلید SPACE یا CLEAR باشد، کمی عریض‌تر رسم می‌شود، چون متنش طولانی‌تر است.
            w = key_w + 12 if key in ["SPACE", "CLEAR"] else key_w
            #برای هر کلید یک مستطیل ساخته می‌شود.
            rect = pygame.Rect(x, y, w, key_h)
            #بررسی می‌کند آیا این کلید، کلید انتخاب‌شده فعلی است یا نه.
            selected = idx == self.keyboard_index
            #داخل کلید را رسم می‌کند.
            #اگر کلید انتخاب شده باشد، رنگ آن روشن‌تر است.
            #اگر انتخاب نشده باشد، رنگ آن تیره‌تر است.

            pygame.draw.rect(screen, (45, 65, 95) if selected else (30, 42, 62), rect, border_radius=5)
            #دور کلید را رسم می‌کند.
            pygame.draw.rect(screen, RED if selected else CYAN, rect, 2 if selected else 1, border_radius=5)
            #اگر کلید SPACE باشد، روی صفحه فقط SP نشان داده می‌شود تا جا شود.
            label = "SP" if key == "SPACE" else key
            #متن کلید را داخل مستطیل می‌نویسد.
            draw_text(label, rect.x + 5, rect.y + 4, YELLOW if selected else WHITE, F_KEY)
            #پایین صفحه راهنمای دکمه‌ها را نشان می‌دهد:

        draw_text("Blue B5 selects. Yellow B4 confirms.", 10, WIN_H - 18, GRAY, F_TINY)
#این تابع صفحه‌ای را نشان می‌دهد که Master منتظر وصل شدن Client است.
    def draw_waiting(self):
        """Render the waiting screen until the client joins."""
        #صفحه را پاک می‌کند و رنگ پس‌زمینه را می‌گذارد.
        screen.fill(BG)
        #وضعیت شبکه را می‌گیرد.
        snap = self.net.snapshot()
        #عنوان صفحه را نمایش می‌دهد:
        draw_text("Waiting for Client", max(10, WIN_W//2 - 200), int(WIN_H * 0.13), YELLOW, F_BIG)
        #به کاربر می‌گوید اسکریپت Client را روی Raspberry Pi دوم اجرا کند.
        draw_text("1) Run client script", max(10, WIN_W//2 - 160), int(WIN_H * 0.30), WHITE, F_MED)
        #می‌گوید Client به صورت خودکار join می‌شود.
        draw_text("2) Client joins automatically", max(10, WIN_W//2 - 230), int(WIN_H * 0.42), WHITE, F_MED)
        #وضعیت اتصال Client را نشان می‌دهد.
        draw_text(f"Connected: {'YES' if snap['connected'] else 'NO'}", max(10, WIN_W//2 - 160), int(WIN_H * 0.62), GREEN if snap['connected'] else RED, F_MED)
        #نام Player 1 و Player 2 را پایین صفحه نشان می‌دهد.
        draw_text(f"P1:{self.player1} P2:{self.player2}", max(10, WIN_W//2 - 200), int(WIN_H * 0.85), CYAN, F_SMALL)

    def draw_result(self):
        """Render final score, session metrics, and Pi diagnostics."""
        #کل صفحه را با رنگ پس‌زمینه پاک می‌کند.
        screen.fill(BG)
        #وضعیت شبکه و Client را می‌گیرد.
        snap = self.net.snapshot()
        #وضعیت Master Raspberry Pi را می‌گیرد.
        master = self.get_master_status_cached()
        #وضعیت Client را از snapshot جدا می‌کند.
        client = snap["status"]
        #عنوان صفحه نتیجه را رسم می‌کند:
        #یعنی عنوان تقریباً وسط صفحه باشد، ولی حداقل ۸ پیکسل از سمت چپ فاصله داشته باشد.

        draw_text("FINAL RESULTS", max(8, WIN_W//2 - 130), 5, WHITE, F_BIG)
#اینجا یک لیست از متن‌هایی ساخته می‌شود که قرار است روی صفحه نتیجه نمایش داده شوند.
        game_lines = [
            "GAME DATA",
            f"Team: {self.team}",
            f"P1: {self.player1}",
            f"P2: {self.player2}",
            f"Score: {self.last_score}",
            f"Stars: {self.stars_collected}/{STAR_COUNT}",
            f"Monster: {self.monster_hits}",
            f"Holes: {self.hole_hits}",
            #مدت زمان همین بازی را نشان می‌دهد.
            f"Session: {self.last_duration}s",
            f"Overall: {overall_time_played()}s",
        ]
#اینجا یک لیست از متن‌ها ساخته می‌شود که مربوط به وضعیت Master Pi است.
        master_lines = [
            "MASTER PI",
            #اگر مقدار cpu وجود نداشته باشد، - نمایش می‌دهد.
            f"CPU: {master.get('cpu','-')}",
            f"RAM: {master.get('ram','-')}",
            f"Temp: {master.get('temp','-')}",
            #مدت زمانی را نشان می‌دهد که Master روشن بوده است.
            f"Uptime: {master.get('uptime','-')}",
            #درصد استفاده از حافظه دیسک Master را نشان می‌دهد.
            f"Disk: {master.get('disk','-')}",
            #نسخه Python روی Master را نشان می‌دهد.
            f"Python: {master.get('python','-')}",
            f"Build: {master.get('build','-')[:12]}",
        ]

        client_lines = [
            "CLIENT PI",
            f"Conn: {'ON' if snap['connected'] else 'OFF'}",
            f"Join: {'YES' if snap['joined'] else 'NO'}",
            f"Latency: {snap['avg_latency']:.0f}ms",
            #میانگین latency را نشان می‌دهد.
            f"Errors: {snap['packet_errors']}",
            f"CPU: {client.get('cpu','-')}",
            f"RAM: {client.get('ram','-')}",
            f"Temp: {client.get('temp','-')}",
            f"Build: {client.get('build','-')[:12]}",
        ]
#اینجا محل افقی سه ستون روی صفحه مشخص می‌شود.
        x1, x2, x3 = 8, 166, 322
        #محل شروع عمودی متن‌ها را مشخص می‌کند.
        y0 = 42
        #فاصله عمودی بین خط‌ها را تعیین می‌کند.
        #اگر ارتفاع صفحه حداقل ۳۲۰ پیکسل باشد، فاصله خط‌ها ۲۳ پیکسل است.
        #اگر صفحه کوچک‌تر باشد، فاصله خط‌ها ۲۰ پیکسل می‌شود.
        #یعنی کد خودش را با اندازه صفحه تنظیم می‌کند.
        line_h = 23 if WIN_H >= 320 else 20
        
        
        
        #line متن همان خط است.
        #i شماره خط است.


        for i, line in enumerate(game_lines):
            #اگر خط شماره 0 یا 4 باشد، فونت بزرگ‌تر استفاده می‌شود.
            #یعنی عنوان و score مهم‌تر هستند و بزرگ‌تر نمایش داده می‌شوند.
            font = F_RESULT if i in [0, 4] else F_RESULT_SMALL
            #این خط متن را در ستون اول رسم می‌کند.
            #y0 + i * line_h یعنی هر خط کمی پایین‌تر از خط قبلی رسم شود.
            draw_text(line, x1, y0 + i * line_h, YELLOW if i == 0 else WHITE, font)
#همه خط‌های مربوط به Master Pi را رسم می‌کند.
            #فقط خط اول یعنی عنوان MASTER PI بزرگ‌تر نمایش داده می‌شود.
        for i, line in enumerate(master_lines):
            font = F_RESULT if i == 0 else F_RESULT_SMALL
            #متن را در ستون دوم رسم می‌کند.
            #اگر خط اول باشد، رنگ آن سبز است:
            #بقیه خط‌ها سفید هستند.
            draw_text(line, x2, y0 + i * line_h, GREEN if i == 0 else WHITE, font)
#اگر خط اول باشد، رنگ آن آبی روشن است:
        for i, line in enumerate(client_lines):
            font = F_RESULT if i == 0 else F_RESULT_SMALL
            draw_text(line, x3, y0 + i * line_h, CYAN if i == 0 else WHITE, font)
#پایین صفحه راهنمای دکمه‌ها را نشان می‌دهد:
        draw_text("B1: play again   B3: new names", max(8, WIN_W//2 - 150), WIN_H - 22, GRAY, F_TINY)


#این تابع وقتی اجرا می‌شود که بازی تمام شده باشد.
    def finish_game(self):
        """Freeze final metrics and save the completed session to CSV."""
        #مدت زمان بازی را حساب می‌کند.
        self.last_duration = int(time.time() - self.start_time)
        #امتیاز نهایی را حساب می‌کند و در last_score ذخیره می‌کند.
        self.last_score = self.score()
        #وضعیت فعلی شبکه را می‌گیرد.
        snap = self.net.snapshot()
        #اینجا یک dictionary ساخته می‌شود.
        #این dictionary همان یک ردیف اطلاعات بازی است که بعداً در CSV ذخیره می‌شود.
        row = {
            #بعداً داخل save_score(row)، scoreها مرتب می‌شوند و rank واقعی تعیین می‌شود.
            "rank": 0, "timestamp": datetime.now().isoformat(timespec="seconds"), "team": self.team,
            "player1": self.player1, "player2": self.player2, "score": self.last_score, "stars": self.stars_collected,
            "monster_hits": self.monster_hits, "hole_hits": self.hole_hits, "duration_s": self.last_duration,
            #کل زمان بازی‌شده را ذخیره می‌کند.
            "overall_time_s": overall_time_played() + self.last_duration, "last_played": datetime.now().isoformat(timespec="seconds"),
            "avg_latency_ms": round(snap["avg_latency"], 1), "packet_errors": snap["packet_errors"]
        }
        save_score(row)

    def reset_game(self):
        """Reset game state and require a new name/client setup."""
        #به بخش شبکه می‌گوید join مربوط به Client را reset کند.
        self.net.reset_join()
        #یک maze و بازی جدید می‌سازد.
        self.init_game()
        self.state = "name_entry"
        self.name_index = 0
        self.keyboard_index = 0


#این تابع حلقه اصلی بازی است.
    def run(self):
        """Execute the main event loop and game-state machine."""
        #تا وقتی running برابر True باشد، بازی ادامه دارد.
        running = True
        #اینجا وضعیت قبلی دکمه‌ها ذخیره می‌شود.
        #در ابتدا همه False هستند، یعنی فرض می‌کنیم هیچ دکمه‌ای قبلاً فشار داده نشده.
        prev_b1, prev_b3, prev_b4, prev_b5, prev_b7, prev_b8 = False, False, False,
        #برای هر دکمه زمان آخرین فشار معتبر ذخیره می‌شود.False, False, False
        last_b1_time, last_b3_time, last_b4_time, last_b5_time, last_b7_time, last_b8_time = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        #یعنی بین دو فشار معتبر دکمه باید حداقل ۰.۱۸ ثانیه فاصله باشد.
        BUTTON_DEBOUNCE = 0.18
        #زمان آخرین آپدیت نمایشگر را نگه می‌دارد.
        last_display_time = 0.0
        #یعنی صفحه حداکثر حدود ۸ بار در ثانیه refresh شود.
        #پس هر ۰.۱۲۵ ثانیه یک بار نمایشگر آپدیت می‌شود.
        DISPLAY_INTERVAL = 1.0 / 8.0  # limited refresh rate to keep LCD rendering stable

        try:
            while running:
                now = time.time()
                #سرعت اجرای loop را محدود می‌کند.
                #یعنی game loop حداکثر حدود ۱۵ بار در ثانیه اجرا شود.
                clock.tick(FPS)
                #eventهای Pygame را بررسی می‌کند.

                for event in pygame.event.get():
                    #اگر کاربر پنجره Pygame را ببندد، مقدار running برابر False می‌شود و برنامه از loop خارج می‌شود.
                    if event.type == pygame.QUIT:
                        running = False
                        #هرکدام اگر فشار داده شده باشد، مقدار True می‌گیرد.

                b1 = button_pressed(PIN_B1)
                b3 = button_pressed(PIN_B3)
                b4 = button_pressed(PIN_B4)
                b5 = button_pressed(PIN_B5)
                b7 = button_pressed(PIN_B7)
                b8 = button_pressed(PIN_B8)
                #این یعنی دکمه B1 همین الآن تازه فشار داده شده و از آخرین فشار معتبر آن حداقل ۰.۱۸ ثانیه گذشته است.

                b1_edge = b1 and not prev_b1 and (now - last_b1_time) >= BUTTON_DEBOUNCE
                b3_edge = b3 and not prev_b3 and (now - last_b3_time) >= BUTTON_DEBOUNCE
                b4_edge = b4 and not prev_b4 and (now - last_b4_time) >= BUTTON_DEBOUNCE
                b5_edge = b5 and not prev_b5 and (now - last_b5_time) >= BUTTON_DEBOUNCE
                b7_edge = b7 and not prev_b7 and (now - last_b7_time) >= BUTTON_DEBOUNCE
                b8_edge = b8 and not prev_b8 and (now - last_b8_time) >= BUTTON_DEBOUNCE

                if b1_edge:
                    #اگر دکمه B1 تازه و معتبر فشار داده شده باشد، زمان آن ذخیره می‌شود.
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
                    #اگر بازی در حالت playing باشد، یعنی بازی در حال اجراست.
                    if self.state == "playing":
                        #بازی را تمام می‌کند.
                        self.finish_game()
                        self.state = "result"
                    else:
                        self.reset_game()

                # Setup state: collect team and player names locally on the master.
                if self.state == "name_entry":
                    if b7_edge:
                        #اگر B7 فشار داده شود، انتخاب کیبورد یک خانه به عقب می‌رود.
                        self.keyboard_index = (self.keyboard_index - 1) % len(self.keyboard_keys)
                    if b8_edge:
                        #اگر B8 فشار داده شود، انتخاب کیبورد یک خانه جلو می‌رود.
                        self.keyboard_index = (self.keyboard_index + 1) % len(self.keyboard_keys)
                    if b5_edge:
                        #اگر B5 فشار داده شود، کلید فعلی کیبورد انتخاب می‌شود.
                        self.apply_keyboard_select()
                    if b4_edge:
                        #اگر B4 فشار داده شود، فیلد فعلی تأیید می‌شود.
                        self.apply_name_enter()
                        #بررسی می‌کند آیا وقت رسم دوباره صفحه رسیده یا نه.
                    if now - last_display_time >= DISPLAY_INTERVAL:
                        self.draw_name_entry()

                # Synchronization state: wait until the client confirms joining.
                #یعنی اگر برنامه در حالت انتظار برای Client باشد، این بخش اجرا می‌شود.
                elif self.state == "waiting_client":
                    #اگر زمان refresh صفحه رسیده باشد، صفحه انتظار رسم می‌شود.
                    if now - last_display_time >= DISPLAY_INTERVAL:
                        self.draw_waiting()
                        #از شبکه یک snapshot می‌گیرد.
                    snap = self.net.snapshot()
                    #اگر Client هم وصل باشد و هم join شده باشد، بازی می‌تواند شروع شود.
                    if snap["connected"] and snap["joined"]:
                        #اگر Client آماده باشد:
                        self.init_game()
                        #اگر بازی در حال اجرا باشد، این بخش اجرا می‌شود.
                        self.state = "playing"

                # Runtime state: merge master/client input, update game logic, and transmit status.
                elif self.state == "playing":
                    #یعنی فقط هر چند میلی‌ثانیه یک بار input خوانده شود.
                    if now - self.input_timer >= INPUT_DELAY:
                        #ورودی کنترلر Master خوانده می‌شود.
                        mdx, mdy = read_master_input()
                        #اگر Master واقعاً حرکتی داده باشد، آن حرکت ذخیره می‌شود.
                        if mdx != 0 or mdy != 0:
                            self.master_dx, self.master_dy, self.master_move_time = mdx, mdy, now
                            #اینجا بررسی می‌کند حرکت Master هنوز تازه است یا نه.
                            #اگر حرکت در بازه مجاز باشد، همان حرکت استفاده می‌شود.
                            #اگر قدیمی شده باشد، حرکت صفر می‌شود.
                        if now - self.master_move_time <= MASTER_MOVE_HOLD_TIME:
                            mdx, mdy = self.master_dx, self.master_dy
                        else:
                            mdx, mdy = 0, 0
                            #آخرین حرکت معتبر Client از شبکه گرفته می‌شود.
                            #Client در این پروژه این جهت‌ها را کنترل می‌کند:
                        cdx, cdy = self.net.get_move()
                        #اگر Master حرکتی داده باشد، player حرکت می‌کند.
                        if mdx != 0 or mdy != 0:
                            #تابع move_player خودش بررسی می‌کند که حرکت مجاز باشد:
                            self.move_player(mdx, mdy)
                            #اگر Client حرکتی داده باشد، player با آن حرکت هم جابه‌جا می‌شود.
                        if cdx != 0 or cdy != 0:
                            #حرکت Client پاک می‌شود.
                            self.move_player(cdx, cdy)
                            #زمان آخرین بررسی input ذخیره می‌شود.
                            self.net.consume_move()
                        self.input_timer = now
                        #بررسی می‌کند آیا وقت حرکت monster رسیده یا نه.
                        #Monster در هر فریم حرکت نمی‌کند، بلکه با فاصله زمانی مشخص حرکت می‌کند.

                    if now - self.monster_timer >= MONSTER_DELAY:
                        self.move_monster()
                        self.monster_timer = now
                        
                        #اینجا اتفاقات بازی بررسی می‌شود:

                    self.check_events()
                    #اگر player به نقطه پایان رسیده باشد، بازی تمام می‌شود.
                    if self.player == self.end:
                        #نتیجه نهایی ذخیره می‌شود و state به صفحه نتیجه تغییر می‌کند.
                        self.finish_game()
                        self.state = "result"
                        #اگر زمان refresh صفحه رسیده باشد، صفحه بازی دوباره رسم می‌شود.

                    if now - last_display_time >= DISPLAY_INTERVAL:
                        # Gameplay uses the lightweight maze-only render path.
                        #اول صفحه پاک می‌شود، بعد maze رسم می‌شود.
                        screen.fill(BG)
                        self.draw_maze()
                        #بررسی می‌کند آیا وقت ارسال وضعیت بازی به Client رسیده یا نه.

                    if now - self.last_state_send >= STATE_SEND_DELAY:
                        self.net.send_state({
                            #Master یک پیام UDP به Client می‌فرستد.
                            "type": "playing", "player": self.player, "score": self.score(),
                            "stars": self.stars_collected, "time": int(time.time() - self.start_time),
                        })
                        #زمان آخرین ارسال state ذخیره می‌شود.
                        self.last_state_send = now

                # Result state: display final metrics and allow a new session.
                #اگر بازی تمام شده و در صفحه نتیجه باشیم، این بخش اجرا می‌شود.
                elif self.state == "result":
                    #اگر زمان refresh صفحه رسیده باشد، صفحه نتیجه رسم می‌شود.
                    if now - last_display_time >= DISPLAY_INTERVAL:
                        self.draw_result()
                        #اگر در صفحه result دکمه B1 فشار داده شود:
                    if b1_edge:
                        self.init_game()
                        self.state = "playing"
                        #در پایان هر loop، وضعیت فعلی دکمه‌ها به عنوان وضعیت قبلی ذخیره می‌شود.

                prev_b1, prev_b3, prev_b4, prev_b5, prev_b7, prev_b8 = b1, b3, b4, b5, b7, b8
                #اگر زمان refresh رسیده باشد، محتوای رسم‌شده واقعاً روی LCD نمایش داده می‌شود.

                if now - last_display_time >= DISPLAY_INTERVAL:
                    #یعنی تصویر نهایی را روی صفحه نشان بده.
                    update_display()
                    #زمان آخرین آپدیت نمایشگر ذخیره می‌شود.
                    last_display_time = now
                    #این بخش در پایان برنامه اجرا می‌شود، حتی اگر برنامه با خطا یا خروج عادی متوقف شود.
