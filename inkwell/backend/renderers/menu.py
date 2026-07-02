"""Menu Renderer — Structured menus with vertical title, sections, columns."""
from PIL import Image, ImageDraw, ImageFont


def render(image, draw, config, hardware, states, fonts):
    r = _MenuLayout(image, draw, config, hardware, states, fonts)
    r.draw_title()
    for section in config.get("sections", []):
        r.render_section(section)


class _MenuLayout:
    def __init__(self, image, draw, config, hardware, states, fonts):
        self.image, self.draw, self.config = image, draw, config
        self.states, self.fonts = states, fonts
        self.width, self.height = hardware["width"], hardware["height"]
        self.bg = hardware.get("bg_color", 0)
        self.fg = hardware.get("fg_color", 255)

        L = config.get("layout", {})
        self.title_x = L.get("title_x", 30)
        self.content_x_start = L.get("content_x_start", 120)
        self.col_w = L.get("column_width", 240)
        self.col_n = L.get("column_count", 3)
        self.m_top = L.get("margin_top", 25)
        self.m_bot = L.get("margin_bottom", 20)
        self.line_h = L.get("line_height", 45)
        self.hdr_gap = L.get("header_gap", 15)
        self.hdr_h = L.get("header_height", 45)
        self.hdr_pad = L.get("header_padding", 5)
        self.spc_gap = L.get("special_gap", 5)

        self.cols = [self.content_x_start + i * self.col_w for i in range(self.col_n)]
        self.col_i = 0
        self.cy = self.m_top

    def _s(self, eid): return self.states.get(eid, "unavailable")
    def _on(self, eid): return self._s(eid) == "on"
    def _x(self): return self.cols[self.col_i]
    def _f(self, name): return self.fonts.get(name, next(iter(self.fonts.values())))

    def _wrap(self, need):
        if self.cy + need > self.height - self.m_bot:
            self.cy = self.m_top
            self.col_i = min(self.col_i + 1, self.col_n - 1)

    def draw_title(self):
        t = self.config.get("title", {})
        if not t: return
        chars = t.get("text", "")
        font = self._f(t.get("font", "title"))
        sy, sp, sx = t.get("start_y", 60), t.get("char_spacing", 100), t.get("separator_x", 110)
        for i, c in enumerate(chars):
            self.draw.text((self.title_x, sy + i * sp), c, font=font, fill=self.fg)
        self.draw.line([(sx, 20), (sx, self.height - 20)], fill=self.fg, width=1)

    def print_item(self, text, fn="body"):
        self._wrap(self.line_h)
        self.draw.text((self._x(), self.cy), text, font=self._f(fn), fill=self.fg)
        self.cy += self.line_h

    def print_header(self, text, fn="header"):
        if self.cy > self.m_top: self.cy += self.hdr_gap
        needed = self.hdr_h + 10 + self.line_h
        if self.cy + needed > self.height - self.m_bot:
            self.cy = self.m_top; self.col_i = min(self.col_i + 1, self.col_n - 1)
        x, w = self._x(), self.col_w - 40
        self.draw.rectangle([(x-5, self.cy), (x-5+w, self.cy+self.hdr_h)], fill=self.fg)
        self.draw.text((x, self.cy+self.hdr_pad), text, font=self._f(fn), fill=self.bg)
        self.cy += self.hdr_h + 10

    def gap(self, px=None): self.cy += px if px else self.spc_gap

    def render_section(self, s):
        t = s.get("type", "static")
        if t == "bottles": self._bottles(s)
        elif t == "conditional": self._cond(s)
        elif t == "static": self._static(s)

    def _bottles(self, s):
        for i in s.get("items", []):
            v = self._s(i["entity"])
            if v not in ("none", "unavailable"):
                self.print_item(f"{i['prefix']}{v}"); self.gap()

    def _cond(self, s):
        hdr, cond, items = s.get("header",""), s.get("condition",{}), s.get("items",[])
        active = [i for i in items if self._eval(i.get("show_when", {}))]
        vis = True
        if "require_all" in cond: vis = all(self._on(e) for e in cond["require_all"])
        if vis and cond.get("require_any_item_active") and not active: vis = False
        if not vis or not active: return
        if hdr: self.print_header(hdr)
        for i in active:
            vs = i.get("variants")
            if vs:
                for v in vs:
                    if self._eval(v.get("when", {})): self.print_item(v["text"]); break
            else: self.print_item(i["text"])

    def _static(self, s):
        if s.get("header"): self.print_header(s["header"])
        for i in s.get("items", []): self.print_item(i["text"])

    def _eval(self, c):
        if not c: return True
        if "any_on" in c: return any(self._on(e) for e in c["any_on"])
        if "all_on" in c: return all(self._on(e) for e in c["all_on"])
        if "entity_on" in c: return self._on(c["entity_on"])
        if "any_select_equals" in c:
            sp = c["any_select_equals"]
            return any(self._s(e) == sp["value"] for e in sp["entities"])
        return True
