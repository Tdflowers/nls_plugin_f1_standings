"""
F1 Next Race Board - Displays the upcoming race schedule and session times.

Layout (64x32, 7px font):
  Row 0  [F1][Next Race]            <- sticky header
  Row 1   [padding]
  Row 2   Race name (word-wrapped)
  Row 3   Race date          time   (yellow, two fixed columns)
  Row 4   [blank]
  Row 5   [Location:]               (white on red bg, text-width only)
  Row 6   Circuit:  <name>          (label gray, value white, word-wrapped)
  Row 7   Location: <city, country> (label gray, value white, word-wrapped)
  Row 8   [blank]
  Row 9   [Weekend:]                (white on red bg, text-width only)
  Row 10  FP1          05/04 13:30  (date and time in independent right columns)
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
    """
    left         — drawn at x=2, left-aligned
    date         — right-aligned to the date column
    time         — right-aligned to the right edge
    bg           — row background fill color
    bg_text_only — bg rectangle covers only the left text width when True
    """
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

        if self.matrix.width >= 128:
            self.font             = data.config.layout.font_large
            self.font_height      = 13
            self.width_multiplier = 2
        else:
            self.font             = data.config.layout.font
            self.font_height      = 7
            self.width_multiplier = 1

        self.cache_ttl = self.refresh_minutes * 60 * 2

        f1_worker.fetch_next_race(cache_ttl=self.cache_ttl)

        self.add_scheduled_job(
            lambda: f1_worker.fetch_next_race(cache_ttl=self.cache_ttl),
            "interval",
            job_id="f1_next_fetch",
            minutes=self.refresh_minutes,
        )

    # ------------------------------------------------------------------
    # Render entry point
    # ------------------------------------------------------------------

    def render(self):
        race = f1_worker.get_cached_next_race()
        if not race:
            debug.warning("F1 next board: no cached race data")
            return

        lines      = self._build_lines(race)
        img_height = len(lines) * self.font_height
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
        """Red background section header, bg covers text width only."""
        return _mk_line(left=text, lc=COLOR_WHITE, bg=COLOR_F1_RED, bg_text_only=True)

    def _build_lines(self, race):
        """Return list of line dicts for all scrollable rows."""
        lines = []

        # Empty row hidden behind sticky header at scroll offset 0.
        lines.append(_mk_line())

        # Race name — word-wrapped, left-aligned
        for wrapped in self._word_wrap(race["name"], self.matrix.width - 2):
            lines.append(_mk_line(left=wrapped, lc=COLOR_WHITE))

        # Race date + time — full format, word-wrapped, yellow
        for seg in self._word_wrap(self._fmt_summary_dt(race["dt"]), self.matrix.width - 2):
            lines.append(_mk_line(left=seg, lc=COLOR_YELLOW))

        # Location section
        circuit  = race.get("circuit", "")
        locality = race.get("locality", "")
        country  = race.get("country", "")

        if circuit or locality or country:
            lines.append(_mk_line())
            lines.append(self._section_header("Location:"))

            if circuit:
                lines.append(_mk_line(left="Circuit:", lc=COLOR_GRAY))
                for seg in self._word_wrap(circuit, self.matrix.width - 2):
                    lines.append(_mk_line(left=seg, lc=COLOR_WHITE))

            loc_str = ", ".join(filter(None, [locality, country]))
            if loc_str:
                lines.append(_mk_line(left="Location:", lc=COLOR_GRAY))
                for seg in self._word_wrap(loc_str, self.matrix.width - 2):
                    lines.append(_mk_line(left=seg, lc=COLOR_WHITE))

        # Blank separator before weekend
        lines.append(_mk_line())

        # Weekend section header
        lines.append(self._section_header("Weekend:"))

        # Sessions: label on left (gray), date + time in two right columns
        for session in race["sessions"]:
            lines.append(_mk_line(
                left=session["label"], lc=COLOR_GRAY,
                date=self._fmt_date(session["dt"]), dc=COLOR_WHITE,
                time=self._fmt_time(session["dt"]), tc=COLOR_WHITE,
            ))

        return lines

    def _word_wrap(self, text, max_width):
        """Split text into lines that fit within max_width pixels."""
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
        fh    = self.font_height
        image = Image.new("RGB", (width, img_height))
        draw  = ImageDraw.Draw(image)

        # Pre-compute fixed right-edge columns for date and time so every
        # value shares the same anchor regardless of digit widths.
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
                    draw.rectangle([0, y, bg_w, y + fh - 1], fill=bg)
                else:
                    draw.rectangle([0, y, width - 1, y + fh - 1], fill=bg)

            if line["left"]:
                draw.text((2, y), line["left"], font=self.font, fill=line["lc"])

            if line["date"]:
                dw = int(self.font.getlength(line["date"]))
                draw.text((date_right - dw, y), line["date"], font=self.font, fill=line["dc"])

            if line["time"]:
                tw = int(self.font.getlength(line["time"]))
                draw.text((time_right - tw, y), line["time"], font=self.font, fill=line["tc"])

            y += fh

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
        fh = self.font_height
        self.matrix.draw_rectangle((0, 0), (self.matrix.width, fh - 1), fill=COLOR_BLACK)
        header = Image.new("RGB", (self.matrix.width, fh))
        draw   = ImageDraw.Draw(header)
        self._draw_f1_header(draw, 0, "Next Race")
        self.matrix.draw_image((0, 0), header)

    def _draw_f1_header(self, draw, row_y, title):
        """Red 'F1' badge on the left, title text to the right."""
        f1_w = int(self.font.getlength("F1")) + 2 * self.width_multiplier
        draw.rectangle(
            [0, row_y, f1_w, row_y + self.font_height - 1],
            fill=COLOR_F1_RED,
        )
        draw.text((1, row_y), "F1", font=self.font, fill=COLOR_WHITE)
        draw.text((f1_w + 2, row_y), title, font=self.font, fill=COLOR_WHITE)
