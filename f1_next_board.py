"""
F1 Next Race Board - Displays the upcoming race schedule and session times.

Positions and sizing are driven by layout_64x32.json / layout_128x64.json.
If no layout file is found the board falls back to sensible pixel defaults.

Scrollable content structure:
  [padding row — sits behind sticky header at scroll offset 0]
  Race name (word-wrapped)
  Race date/time (yellow, full format, word-wrapped)
  [blank]
  [Location:]  <- red section header, text-width bg
  Circuit:     <- gray sub-label
  <name>       <- white, word-wrapped
  Location:    <- gray sub-label
  <city, ctry> <- white, word-wrapped
  [blank]
  [Weekend:]   <- red section header, text-width bg
  FP1   05/01  09:30    <- label left, date+time in fixed right columns
  ...
"""
import logging

from PIL import Image, ImageDraw

from boards.base_board import BoardBase
from . import __version__
from . import f1_worker

debug = logging.getLogger("scoreboard")

COLOR_WHITE  = (255, 255, 255)
COLOR_BLACK  = (0, 0, 0)
COLOR_GRAY   = (120, 120, 120)
COLOR_YELLOW = (255, 220, 0)
COLOR_F1_RED = (232, 0, 32)


def _mk_line(
    left=None, lc=COLOR_WHITE,
    date=None, dc=COLOR_WHITE,
    time=None, tc=COLOR_WHITE,
    bg=None, bg_text_only=False,
):
    return {
        "left": left, "lc": lc,
        "date": date, "dc": dc,
        "time": time, "tc": tc,
        "bg": bg, "bg_text_only": bg_text_only,
    }


class F1NextBoard(BoardBase):
    """
    F1 Next Race Board.

    Shows the upcoming race name, location, and all weekend session times.
    Scrolls vertically through the full schedule.
    Positions are driven by the plugin layout files.
    """

    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name        = "F1 Next Race"
        self.board_version     = __version__
        self.board_description = "Upcoming F1 race weekend schedule"

        self.scroll_speed    = self.get_config_value("scroll_speed",    0.12)
        self.rotation_rate   = self.get_config_value("rotation_rate",   5)
        self.refresh_minutes = self.get_config_value("refresh_minutes", 60)
        self.use_local_time  = self.get_config_value("use_local_time",  True)
        self.time_24h        = self.get_config_value("time_24h",        True)

        # Load layout file; _init_layout_metrics() derives pixel constants from it.
        self.layout = self.get_board_layout("f1_next")

        if self.matrix.width >= 128:
            self.font        = data.config.layout.font_large
            self.font_height = 13
        else:
            self.font        = data.config.layout.font
            self.font_height = 7

        self._init_layout_metrics()

        self.cache_ttl = self.refresh_minutes * 60 * 2

        f1_worker.fetch_next_race(cache_ttl=self.cache_ttl)

        self.add_scheduled_job(
            lambda: f1_worker.fetch_next_race(cache_ttl=self.cache_ttl),
            "interval",
            job_id="f1_next_fetch",
            minutes=self.refresh_minutes,
        )

    def _init_layout_metrics(self):
        """
        Pull badge width, title x, content left margin, and line height from
        the layout file.  Every drawing method uses these values so swapping
        layout files is all that's needed to support a new display size.
        JSONData uses attribute access, so all reads go through getattr().
        """
        fh = self.font_height
        lo = self.layout

        badge    = getattr(lo, "header_badge", None)
        badge_sz = getattr(badge, "size", None)
        self.badge_w = badge_sz[0] if badge_sz is not None else int(self.font.getlength("F1")) + 4

        title     = getattr(lo, "header_title", None)
        title_pos = getattr(title, "position", None)
        self.title_x = title_pos[0] if title_pos is not None else self.badge_w + 2

        content     = getattr(lo, "content", None)
        content_pos = getattr(content, "position", None)
        self.content_x   = content_pos[0] if content_pos is not None else 2
        self.line_height = getattr(content, "line_height", fh)

    # ------------------------------------------------------------------
    # Render entry point
    # ------------------------------------------------------------------

    def render(self):
        race = f1_worker.get_cached_next_race()
        if not race:
            debug.warning("F1 next board: no cached race data")
            return

        lines      = self._build_lines(race)
        img_height = len(lines) * self.line_height
        image      = self._draw_content(lines, img_height)
        self._scroll_image(image, img_height)

    # ------------------------------------------------------------------
    # Datetime formatting
    # ------------------------------------------------------------------

    def _localize(self, dt):
        return dt.astimezone() if self.use_local_time else dt

    @staticmethod
    def _ordinal(n):
        suffix = (
            "th" if 11 <= (n % 100) <= 13
            else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        )
        return f"{n}{suffix}"

    def _fmt_date(self, dt):
        return self._localize(dt).strftime("%m/%d")

    def _fmt_time(self, dt):
        dt = self._localize(dt)
        return dt.strftime("%H:%M") if self.time_24h else dt.strftime("%-I:%M%p").lower()

    def _fmt_summary_dt(self, dt):
        """Format as 'Sunday, May 3rd - 1:00PM' (or 24h if configured)."""
        dt       = self._localize(dt)
        day_name = dt.strftime("%A")
        month    = dt.strftime("%B")
        day      = self._ordinal(dt.day)
        time_str = dt.strftime("%H:%M") if self.time_24h else dt.strftime("%-I:%M%p")
        return f"{day_name}, {month} {day} - {time_str}"

    # ------------------------------------------------------------------
    # Content builder
    # ------------------------------------------------------------------

    def _section_header(self, text):
        return _mk_line(left=text, lc=COLOR_WHITE, bg=COLOR_F1_RED, bg_text_only=True)

    def _build_lines(self, race):
        lines = []
        avail = self.matrix.width - self.content_x

        # Empty padding row — hidden behind sticky header at scroll offset 0.
        lines.append(_mk_line())

        # Race name
        for seg in self._word_wrap(race["name"], avail):
            lines.append(_mk_line(left=seg, lc=COLOR_WHITE))

        # Race date/time — full format, word-wrapped, yellow
        for seg in self._word_wrap(self._fmt_summary_dt(race["dt"]), avail):
            lines.append(_mk_line(left=seg, lc=COLOR_WHITE))

        # Location section
        circuit  = race.get("circuit", "")
        locality = race.get("locality", "")
        country  = race.get("country", "")

        if circuit or locality or country:
            lines.append(_mk_line())
            lines.append(self._section_header("Location:"))

            if circuit:
                lines.append(_mk_line(left="Circuit:", lc=COLOR_GRAY))
                for seg in self._word_wrap(circuit, avail):
                    lines.append(_mk_line(left=seg, lc=COLOR_WHITE))

            loc_str = ", ".join(filter(None, [locality, country]))
            if loc_str:
                lines.append(_mk_line(left="Host:", lc=COLOR_GRAY))
                for seg in self._word_wrap(loc_str, avail):
                    lines.append(_mk_line(left=seg, lc=COLOR_WHITE))

        # Weekend section
        lines.append(_mk_line())
        lines.append(self._section_header("Weekend:"))

        for session in race["sessions"]:
            lines.append(_mk_line(
                left=session["label"], lc=COLOR_GRAY,
                date=self._fmt_date(session["dt"]), dc=COLOR_WHITE,
                time=self._fmt_time(session["dt"]), tc=COLOR_WHITE,
            ))

        return lines

    def _word_wrap(self, text, max_width):
        words   = text.split()
        result  = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if self.font.getlength(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = word
        if current:
            result.append(current)
        return result or [text]

    # ------------------------------------------------------------------
    # Image drawing
    # ------------------------------------------------------------------

    def _draw_content(self, lines, img_height):
        width = self.matrix.width
        lh    = self.line_height
        image = Image.new("RGB", (width, img_height))
        draw  = ImageDraw.Draw(image)

        # Pre-compute fixed right-edge columns for date/time so every value
        # shares the same anchor regardless of variable digit widths.
        time_right = width - 2
        max_time_w = max(
            (int(self.font.getlength(ln["time"])) for ln in lines if ln.get("time")),
            default=0,
        )
        date_right = time_right - max_time_w - 3

        y = 0
        for line in lines:
            bg = line["bg"]
            if bg:
                if line["bg_text_only"] and line["left"]:
                    bg_w = int(self.font.getlength(line["left"])) + 5
                    draw.rectangle([0, y, bg_w, y + lh - 1], fill=bg)
                else:
                    draw.rectangle([0, y, width - 1, y + lh - 1], fill=bg)

            if line["left"]:
                draw.text((self.content_x, y), line["left"], font=self.font, fill=line["lc"])

            if line["date"]:
                dw = int(self.font.getlength(line["date"]))
                draw.text((date_right - dw, y), line["date"], font=self.font, fill=line["dc"])

            if line["time"]:
                tw = int(self.font.getlength(line["time"]))
                draw.text((time_right - tw, y), line["time"], font=self.font, fill=line["tc"])

            y += lh

        return image

    # ------------------------------------------------------------------
    # Scroll + sticky header
    # ------------------------------------------------------------------

    def _scroll_image(self, image, im_height):
        i = 0
        self._draw_frame(i, image)
        self.sleepEvent.wait(5)

        while i > -(im_height - self.matrix.height) and not self.sleepEvent.is_set():
            i -= 1
            self._draw_frame(i, image)
            self.sleepEvent.wait(self.scroll_speed)

        self.sleepEvent.wait(self.rotation_rate)

    def _draw_frame(self, y_offset, image):
        self.matrix.draw_image((0, y_offset), image)
        self._draw_sticky_header()
        self.matrix.render()

    def _draw_sticky_header(self):
        """Draw the F1 badge + 'Next Race' title using layout-derived positions."""
        lh = self.line_height
        self.matrix.draw_rectangle((0, 0), (self.matrix.width, lh - 1), fill=COLOR_BLACK)

        header = Image.new("RGB", (self.matrix.width, lh))
        draw   = ImageDraw.Draw(header)

        # F1 badge — width and height from layout header_badge.size
        draw.rectangle([0, 0, self.badge_w, lh - 1], fill=COLOR_F1_RED)
        draw.text((1, 0), "F1", font=self.font, fill=COLOR_WHITE)

        # Title — x position from layout header_title.position
        draw.text((self.title_x, 0), "Next Race", font=self.font, fill=COLOR_WHITE)

        self.matrix.draw_image((0, 0), header)
