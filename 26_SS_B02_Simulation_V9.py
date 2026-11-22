
import pygame
import random
import time
from collections import deque
import math

pygame.init()

# Display and maze geometry for the desktop simulation.
WIDTH, HEIGHT = 600, 600
TILE = 25
COLS = WIDTH // TILE
ROWS = HEIGHT // TILE

screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Maze Game")
clock = pygame.time.Clock()

font = pygame.font.SysFont(None, 28)
big_font = pygame.font.SysFont(None, 40)

# ================= MAZE GENERATION AND PATH VALIDATION =================

def generate_maze(w, h):
    """
    Generate a solvable maze using randomized depth-first search.

    Cells with value 1 represent walls and cells with value 0 represent paths.
    After the main DFS generation, a small number of extra wall cells is opened
    to reduce dead ends and make the simulation less linear.
    """
    maze = [[1] * w for _ in range(h)]
    x, y = 1, 1
    maze[y][x] = 0
    stack = [(x, y)]

    while stack:
        x, y = stack[-1]
        dirs = [(2, 0), (-2, 0), (0, 2), (0, -2)]
        random.shuffle(dirs)

        moved = False
        for dx, dy in dirs:
            nx, ny = x + dx, y + dy
            if 1 <= nx < w - 1 and 1 <= ny < h - 1 and maze[ny][nx] == 1:
                maze[ny][nx] = 0
                maze[y + dy // 2][x + dx // 2] = 0
                stack.append((nx, ny))
                moved = True
                break

        if not moved:
            stack.pop()

    # Open selected walls with at least two path neighbours to improve playability.
    for _ in range(int(w * h * 0.08)):
        x = random.randint(1, w - 2)
        y = random.randint(1, h - 2)
        if maze[y][x] == 1:
            neighbors = sum(
                maze[y + dy][x + dx] == 0
                for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]
            )
            if neighbors >= 2:
                maze[y][x] = 0

    return maze


def bfs_distances(start, maze, blocks=None):
    """
    Calculate reachable cells and shortest grid distances from a start position.

    Optional blocked cells are used to test whether hazards would make the exit
    unreachable. This keeps the generated level playable.
    """
    if blocks is None:
        blocks = []

    blocks_set = {tuple(b) for b in blocks}
    queue = deque([tuple(start)])
    distances = {tuple(start): 0}

    while queue:
        x, y = queue.popleft()

        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nx, ny = x + dx, y + dy

            if 0 <= nx < COLS and 0 <= ny < ROWS:
                if maze[ny][nx] == 0 and (nx, ny) not in blocks_set and (nx, ny) not in distances:
                    distances[(nx, ny)] = distances[(x, y)] + 1
                    queue.append((nx, ny))

    return distances


def path_exists(start, end, maze, blocks):
    """Return True when the exit remains reachable from the start."""
    distances = bfs_distances(start, maze, blocks)
    return tuple(end) in distances


def find_empty(maze):
    """Select a random free maze cell."""
    while True:
        x = random.randint(1, COLS - 2)
        y = random.randint(1, ROWS - 2)
        if maze[y][x] == 0:
            return [x, y]


def choose_far_start_end(maze):
    """
    Select distant start and finish cells.

    Two BFS passes are used to approximate the longest playable distance in the
    generated maze, which creates a more meaningful route for the player.
    """
    empty_cells = [(x, y) for y in range(ROWS) for x in range(COLS) if maze[y][x] == 0]
    first = random.choice(empty_cells)

    dist1 = bfs_distances(first, maze)
    farthest1 = max(dist1, key=dist1.get)

    dist2 = bfs_distances(farthest1, maze)
    farthest2 = max(dist2, key=dist2.get)

    return list(farthest1), list(farthest2)


# ================= LEVEL INITIALIZATION =================

# Generate a maze and verify that a valid path from start to exit exists.
while True:
    maze = generate_maze(COLS, ROWS)
    start, end = choose_far_start_end(maze)

    if path_exists(start, end, maze, []):
        break

player = start[:]

# Place holes only when they do not block the complete route to the exit.
holes = []
while len(holes) < 6:
    h = find_empty(maze)

    if h == start or h == end or h in holes:
        continue

    holes.append(h)

    if not path_exists(start, end, maze, holes):
        holes.pop()

# Limit collectible and monster placement to cells that are reachable by the player.
reachable = bfs_distances(start, maze, holes)
reachable_cells = [list(cell) for cell in reachable.keys()]

stars = []
while len(stars) < 5:
    s = random.choice(reachable_cells)

    if s not in holes and s != start and s != end and s not in stars:
        stars.append(s)

while True:
    monster = random.choice(reachable_cells)
    if monster != start and monster != end and monster not in holes and monster not in stars:
        break

# Runtime counters used for scoring and result reporting.
stars_collected = 0
start_time = time.time()
end_time = None
monster_hits = 0
hole_hits = 0
finished = False

last_hit_time = 0
HIT_COOLDOWN = 0.5

# Monster animation state.
mouth_angle = 10
mouth_dir = 1

# ================= DRAWING FUNCTIONS =================

def draw_monster(x, y):
    """Draw the monster with a simple animated mouth."""
    global mouth_angle, mouth_dir

    cx = x * TILE + TILE // 2
    cy = y * TILE + TILE // 2
    r = TILE // 2

    mouth_angle += mouth_dir * 2
    if mouth_angle > 30 or mouth_angle < 5:
        mouth_dir *= -1

    pygame.draw.circle(screen, (255, 0, 0), (cx, cy), r)

    angle = math.radians(mouth_angle)
    p1 = (cx, cy)
    p2 = (cx + r * math.cos(angle), cy - r * math.sin(angle))
    p3 = (cx + r * math.cos(-angle), cy - r * math.sin(-angle))

    pygame.draw.polygon(screen, (255, 255, 255), [p1, p2, p3])


def draw_star(x, y):
    """Draw a five-point collectible star."""
    cx = x * TILE + TILE // 2
    cy = y * TILE + TILE // 2

    outer_r = TILE // 3
    inner_r = TILE // 6

    points = []

    for i in range(10):
        angle = i * (math.pi / 5) - math.pi / 2
        r = outer_r if i % 2 == 0 else inner_r

        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)

        points.append((px, py))

    pygame.draw.polygon(screen, (218, 165, 32), points)


def draw_grid():
    """Draw tile boundaries for easier visual inspection during simulation."""
    for x in range(0, WIDTH, TILE):
        pygame.draw.line(screen, (200, 200, 200), (x, 0), (x, HEIGHT))

    for y in range(0, HEIGHT, TILE):
        pygame.draw.line(screen, (200, 200, 200), (0, y), (WIDTH, y))


def draw():
    """Render the current game state."""
    screen.fill((255, 255, 255))

    for y, row in enumerate(maze):
        for x, val in enumerate(row):
            rect = (x * TILE, y * TILE, TILE, TILE)

            if val == 1:
                pygame.draw.rect(screen, (0, 0, 0), rect)
            else:
                pygame.draw.rect(screen, (255, 255, 255), rect)

    draw_grid()

    pygame.draw.polygon(screen, (255, 255, 0), [
        (start[0] * TILE + TILE // 2, start[1] * TILE),
        (start[0] * TILE, start[1] * TILE + TILE),
        (start[0] * TILE + TILE, start[1] * TILE + TILE)
    ])

    pygame.draw.polygon(screen, (0, 255, 0), [
        (end[0] * TILE + TILE // 2, end[1] * TILE),
        (end[0] * TILE, end[1] * TILE + TILE),
        (end[0] * TILE + TILE, end[1] * TILE + TILE)
    ])

    for h in holes:
        pygame.draw.circle(
            screen,
            (0, 0, 0),
            (h[0] * TILE + TILE // 2, h[1] * TILE + TILE // 2),
            TILE // 3
        )

    for s in stars:
        draw_star(s[0], s[1])

    draw_monster(monster[0], monster[1])

    pygame.draw.rect(
        screen,
        (0, 0, 255),
        (player[0] * TILE, player[1] * TILE, TILE, TILE)
    )


# ================= GAMEPLAY LOGIC =================

def move(dx, dy):
    """Move the player by one tile if the target cell is inside the maze and free."""
    nx = player[0] + dx
    ny = player[1] + dy

    if 0 <= nx < COLS and 0 <= ny < ROWS:
        if maze[ny][nx] == 0:
            player[0], player[1] = nx, ny


monster_move_timer = 0
MONSTER_MOVE_DELAY = 0.25

def move_monster():
    """
    Move the monster one tile toward the player when possible.

    Candidate directions toward the player are prioritized, then all directions are
    added as fallback options. This creates simple chasing behavior without pathfinding.
    """
    dirs = []

    if player[0] > monster[0]:
        dirs.append((1, 0))
    if player[0] < monster[0]:
        dirs.append((-1, 0))
    if player[1] > monster[1]:
        dirs.append((0, 1))
    if player[1] < monster[1]:
        dirs.append((0, -1))

    dirs += [(1, 0), (-1, 0), (0, 1), (0, -1)]
    random.shuffle(dirs)

    for dx, dy in dirs:
        nx, ny = monster[0] + dx, monster[1] + dy

        if 0 <= nx < COLS and 0 <= ny < ROWS:
            if maze[ny][nx] == 0 and [nx, ny] not in holes:
                monster[0], monster[1] = nx, ny
                break


def check_events():
    """
    Process collectible and hazard events at the player's current position.

    A cooldown prevents repeated hit counting while the player is reset after
    touching a hole or the monster.
    """
    global player, monster_hits, hole_hits, last_hit_time, stars_collected

    now = time.time()

    if player in stars:
        stars.remove(player[:])
        stars_collected += 1

    if now - last_hit_time < HIT_COOLDOWN:
        return

    if player in holes:
        hole_hits += 1
        player = start[:]
        last_hit_time = now
        return

    if player == monster:
        monster_hits += 1
        player = start[:]
        last_hit_time = now


def draw_result():
    """Display the final score and session statistics after reaching the exit."""
    elapsed = int(end_time - start_time)

    score = (
        stars_collected * 1000
        - monster_hits * 50
        - hole_hits * 30
        - elapsed * 2
    )

    screen.fill((240, 240, 240))

    pygame.draw.rect(screen, (255, 255, 255), (80, 80, 340, 340), border_radius=15)
    pygame.draw.rect(screen, (0, 0, 0), (80, 80, 340, 340), 2, border_radius=15)

    title = big_font.render("YOU WIN!", True, (0, 150, 0))
    screen.blit(title, (170, 100))

    texts = [
        (f"Stars: {stars_collected}", (218, 165, 32)),
        (f"Time: {elapsed}s", (0, 0, 200)),
        (f"Monster hits: {monster_hits}", (200, 0, 0)),
        (f"Hole hits: {hole_hits}", (0, 0, 0)),
        (f"Score: {score}", (180, 120, 0))
    ]

    for i, (t, color) in enumerate(texts):
        img = font.render(t, True, color)
        screen.blit(img, (120, 150 + i * 40))


# ================= MAIN SIMULATION LOOP =================

running = True
while running:
    now = time.time()

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        if event.type == pygame.KEYDOWN and not finished:
            if event.key == pygame.K_UP:
                move(0, -1)
            if event.key == pygame.K_DOWN:
                move(0, 1)
            if event.key == pygame.K_LEFT:
                move(-1, 0)
            if event.key == pygame.K_RIGHT:
                move(1, 0)

    if not finished:
        if now - monster_move_timer >= MONSTER_MOVE_DELAY:
            move_monster()
            monster_move_timer = now

        check_events()

        if player == end:
            finished = True
            end_time = time.time()

        draw()
    else:
        draw_result()

    pygame.display.flip()
    clock.tick(20)

pygame.quit()
