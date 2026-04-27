"""
F1 Standings Board - Displays driver and constructor standings.

Layout (64x32, 7px font):
  Row 0  [F1][Drivers / Teams YYYY]   ← sticky header
  Row 1   1  [VER]                456
  Row 2   2  [NOR]                401
  ...                       (scrolls)

All column geometry is driven by layout_64x32.json / layout_128x64.json.
If no layout file is found the board falls back to sensible pixel defaults.
"""
import logging
from datetime import datetime

from PIL import Image, ImageDraw

from boards.base_board import BoardBase
from . import __version__
from . import f1_worker

debug = logging.getLogger("scoreboard")

COLOR_WHITE  = (255, 255, 255)
COLOR_BLACK  = (0, 0, 0)
COLOR_GRAY   = (120, 120, 120)
COLOR_F1_RED = (232, 0, 32)

# 2026 F1 team livery colors: team_id -> (bg_rgb, text_rgb)
TEAM_COLORS = {
    "red_bull":     ((54, 113, 198),  COLOR_BLACK),
    "ferrari":      ((232, 0, 32),    COLOR_BLACK),
    "mercedes":     ((39, 244, 210),  COLOR_BLACK),
    "mclaren":      ((255, 128, 0),   COLOR_BLACK),
    "aston_martin": ((34, 153, 113),  COLOR_BLACK),
    "alpine":       ((255, 135, 188), COLOR_BLACK),
    "williams":     ((100, 196, 255), COLOR_BLACK),
    "haas":         ((182, 186, 189), COLOR_BLACK),
    "kick_sauber":  ((82, 226, 82),   COLOR_BLACK),
    "rb":           ((102, 146, 255), COLOR_BLACK),
    "audi":         ((199, 42, 36),   COLOR_BLACK),
    "cadillac":     ((200, 215, 228), COLOR_BLACK),
}
DEFAULT_TEAM_COLOR = ((80, 80, 80), COLOR_BLACK)


def _team_colors(team_id):
    key = team_id.lower().replace(" ", "_").replace("-", "_")
    return TEAM_COLORS.get(key, DEFAULT_TEAM_COLOR)


def _fmt_points(pts):
    return str(int(pts)) if pts == int(pts) else f"{pts:.1f}"


class F1Board(BoardBase):
    """
    F1 Driver and Constructor Standings Board.

    Scrolls driver standings then constructor standings.
    Each section has a sticky header row that stays fixed while content scrolls.
    Column geometry is read from the plugin layout file.
    """

    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name        = "F1 Standings"
        self.board_version     = __version__
        self.board_description = "F1 driver and constructor standings"

        self.scroll_speed      = self.get_config_value("scroll_speed",      0.12)
        self.rotation_rate     = self.get_config_value("rotation_rate",     5)
        self.show_drivers      = self.get_config_value("show_drivers",      True)
        self.show_constructors = self.get_config_value("show_constructors", True)
        self.top_n             = self.get_config_value("top_n",             0)
        self.refresh_minutes   = self.get_config_value("refresh_minutes",   60)

        # Load layout; _init_layout_metrics() derives all pixel constants from it.
        self.layout = self.get_board_layout("f1_standings")

        if self.matrix.width >= 128:
            self.font        = data.config.layout.font_large
            self.font_height = 13
        else:
            self.font        = data.config.layout.font
            self.font_height = 7

        self._init_layout_metrics()

        self.cache_ttl = self.refresh_minutes * 60 * 2

        f1_worker.fetch(cache_ttl=self.cache_ttl)

        self.add_scheduled_job(
            lambda: f1_worker.fetch(cache_ttl=self.cache_ttl),
            "interval",
            job_id="f1_standings_fetch",
            minutes=self.refresh_minutes,
        )

    def _init_layout_metrics(self):
        """
        Derive all column-geometry constants from the layout file.
        Falls back to the original 64-px baseline values when no layout is present.
        """
        fh = self.font_height
        lo = self.layout or {}

        # F1 header badge
        badge       = lo.get("header_badge", {})
        badge_size  = badge.get("size", [None, None])
        self.badge_w = (
            badge_size[0]
            if badge_size[0] is not None
            else int(self.font.getlength("F1")) + 4
        )

        # Title x position
        title        = lo.get("header_title", {})
        title_pos    = title.get("position", [None, 0])
        self.title_x = title_pos[0] if title_pos[0] is not None else self.badge_w + 2

        # Rank column — right-aligned within this width
        rank_col        = lo.get("rank_col", {})
        rank_size       = rank_col.get("size", [8, fh])
        self.pos_width  = rank_size[0]

        # Coloured code/team badge background
        code_bg              = lo.get("code_bg", {})
        code_pos             = code_bg.get("position", [self.pos_width + 1, 0])
        code_size            = code_bg.get("size", [14, fh])
        self.code_x          = code_pos[0]
        self.code_bg_width   = code_size[0]
        self.code_bg_end     = self.code_x + self.code_bg_width

        # Points column margins
        self.pts_margin = lo.get("pts_margin", 2)
        self.pts_gap    = lo.get("pts_gap",    2)

    # ------------------------------------------------------------------
    # Render entry point
    # ------------------------------------------------------------------

    def render(self):
        if self.show_drivers and not self.sleepEvent.is_set():
            self._render_section(
                f1_worker.get_cached_drivers,
                self._draw_driver_table,
                "Drivers",
            )
        if self.show_constructors and not self.sleepEvent.is_set():
            self._render_section(
                f1_worker.get_cached_constructors,
                self._draw_constructor_table,
                "Teams",
            )

    # ------------------------------------------------------------------
    # Section renderer (shared scroll logic)
    # ------------------------------------------------------------------

    def _render_section(self, get_data, draw_table, label):
        data = get_data()
        if not data:
            debug.warning(f"F1 board: no cached data for '{label}'")
            return

        if self.top_n > 0:
            data = data[: self.top_n]

        title     = f"{label} {datetime.now().year}"
        im_height = (len(data) + 1) * self.font_height
        image     = draw_table(data, im_height, self.matrix.width)
        self._scroll_image(image, im_height, title)

    def _scroll_image(self, image, im_height, title):
        i = 0
        self._draw_frame(i, image, title)
        self.sleepEvent.wait(5)

        while i > -(im_height - self.matrix.height) and not self.sleepEvent.is_set():
            i -= 1
            self._draw_frame(i, image, title)
            self.sleepEvent.wait(self.scroll_speed)

        self.sleepEvent.wait(self.rotation_rate)

    def _draw_frame(self, y_offset, image, title):
        self.matrix.draw_image((0, y_offset), image)
        self._draw_sticky_header(title)
        self.matrix.render()

    # ------------------------------------------------------------------
    # Sticky header
    # ------------------------------------------------------------------

    def _draw_sticky_header(self, title):
        fh = self.font_height
        self.matrix.draw_rectangle((0, 0), (self.matrix.width, fh - 1), fill=COLOR_BLACK)
        header = Image.new("RGB", (self.matrix.width, fh))
        draw   = ImageDraw.Draw(header)
        self._draw_f1_header(draw, 0, title)
        self.matrix.draw_image((0, 0), header)

    def _draw_f1_header(self, draw, row_y, title):
        """Red 'F1' badge on the left, title text to the right."""
        draw.rectangle(
            [0, row_y, self.badge_w, row_y + self.font_height - 1],
            fill=COLOR_F1_RED,
        )
        draw.text((1, row_y), "F1", font=self.font, fill=COLOR_WHITE)
        draw.text((self.title_x, row_y), title, font=self.font, fill=COLOR_WHITE)

    # ------------------------------------------------------------------
    # Table drawing helpers
    # ------------------------------------------------------------------

    def _pts_column(self, entries, width):
        """Return (pts_col_x, pts_right_margin) for a fixed right-aligned points column."""
        max_pts_w = max(
            int(self.font.getlength(_fmt_points(e["points"]))) for e in entries
        )
        pts_col_x = width - max_pts_w - self.pts_margin
        return pts_col_x, self.pts_margin

    def _draw_driver_table(self, drivers, img_height, width):
        image   = Image.new("RGB", (width, img_height))
        draw    = ImageDraw.Draw(image)
        fh      = self.font_height
        row_pos = 0

        pts_col_x, pts_margin = self._pts_column(drivers, width)
        bg_right = pts_col_x - self.pts_gap

        self._draw_f1_header(draw, row_pos, f"Drivers {datetime.now().year}")
        row_pos += fh

        for entry in drivers:
            pos  = str(entry["position"])
            code = entry["code"]
            pts  = _fmt_points(entry["points"])
            bg, fg = _team_colors(entry.get("team_id", ""))

            # Rank — right-aligned within pos_width
            pos_w = int(self.font.getlength(pos))
            draw.text((self.pos_width - pos_w, row_pos), pos, font=self.font, fill=COLOR_GRAY)

            # Driver code on team-coloured background
            draw.rectangle([self.code_x, row_pos, bg_right, row_pos + fh - 1], fill=bg)
            code_w = int(self.font.getlength(code))
            draw.text(
                (self.code_x + (bg_right - self.code_x - code_w) // 2, row_pos),
                code, font=self.font, fill=fg,
            )

            # Points — right edge pinned at width - pts_margin
            pts_w = int(self.font.getlength(pts))
            draw.text((width - pts_w - pts_margin, row_pos), pts, font=self.font, fill=COLOR_WHITE)

            row_pos += fh

        return image

    def _draw_constructor_table(self, constructors, img_height, width):
        image   = Image.new("RGB", (width, img_height))
        draw    = ImageDraw.Draw(image)
        fh      = self.font_height
        row_pos = 0

        pts_col_x, pts_margin = self._pts_column(constructors, width)
        bg_right = pts_col_x - self.pts_gap

        self._draw_f1_header(draw, row_pos, f"Teams {datetime.now().year}")
        row_pos += fh

        for entry in constructors:
            pos   = str(entry["position"])
            short = entry.get("short", entry.get("name", "???")[:3].upper())
            pts   = _fmt_points(entry["points"])
            bg, fg = _team_colors(entry.get("team_id", ""))

            # Rank
            pos_w = int(self.font.getlength(pos))
            draw.text((self.pos_width - pos_w, row_pos), pos, font=self.font, fill=COLOR_GRAY)

            # Team code on team-coloured background
            draw.rectangle([self.code_x, row_pos, bg_right, row_pos + fh - 1], fill=bg)
            short_w = int(self.font.getlength(short))
            draw.text(
                (self.code_x + (bg_right - self.code_x - short_w) // 2, row_pos),
                short, font=self.font, fill=fg,
            )

            # Points — right edge pinned at width - pts_margin
            pts_w = int(self.font.getlength(pts))
            draw.text((width - pts_w - pts_margin, row_pos), pts, font=self.font, fill=COLOR_WHITE)

            row_pos += fh

        return image
