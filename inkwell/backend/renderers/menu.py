"""Menu Renderer — structured menus with a vertical title, headers, and columns.

Two config schemas are supported:

- **groups** (current): a list of groups, each with an optional header and a list of items.
  Each item is a Line / Choice / Advanced render plus a `when` condition list (ANDed).
  Condition types: entity_on, entity_off, select_equals ({entity|any_of|entities, value}).
- **sections** (legacy): the original bottles/conditional/static schema. Rendered by the old
  code path unchanged so pre-existing configs keep working.
"""
from PIL import Image, ImageDraw, ImageFont


def render(image, draw, config, hardware, states, fonts):
    r = _MenuLayout(image, draw, config, hardware, states, fonts)
    r.draw_title()
    if config.get("groups") is not None:
        for group in config["groups"]:
            r.render_group(group)
    else:  # legacy
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

    # --- shared helpers ---
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

    # ------------------------------------------------------------ new: groups
    def render_group(self, g):
        header = g.get("header")
        lines = [t for item in g.get("items", []) if (t := self._item_text(item))]
        if not lines:
            # empty group: drop it (incl. header) unless it explicitly opts out
            if header and g.get("hide_if_empty", True) is False:
                self.print_header(header)
            return
        if header:
            self.print_header(header)
            for t in lines:
                self.print_item(t)
        else:  # headerless group (e.g. bottle list) — spaced like the old bottles
            for t in lines:
                self.print_item(t)
                self.gap()

    def _item_text(self, item):
        """Rendered text for an item, or None/'' if it shouldn't be shown."""
        control = item.get("control", "line")
        if control == "advanced":
            return self._advanced_text(item)
        if not self._when_ok(item.get("when")):
            return None
        if control == "choice":
            eid = item.get("entity")
            val = self._s(eid) if eid else None
            if val in (None, "", "none", "unavailable", "unknown"):
                return None
            return f"{item.get('text', '')}{item.get('separator', '：')}{val}"
        return item.get("text", "")

    def _when_ok(self, conditions):
        return all(self._cond(c) for c in (conditions or []))

    def _cond(self, c):
        if "entity_on" in c:
            return self._s(c["entity_on"]) == "on"
        if "entity_off" in c:
            return self._s(c["entity_off"]) == "off"
        if "select_equals" in c:
            se = c["select_equals"]
            value = se.get("value")
            ents = se.get("any_of") or se.get("entities")
            if ents:
                return any(self._s(e) == value for e in ents)
            if "entity" in se:
                return self._s(se["entity"]) == value
        return True  # unknown condition → don't hide

    def _advanced_text(self, item):
        if not self._eval(item.get("show_when", {})):
            return None
        variants = item.get("variants")
        if variants:
            for v in variants:
                if self._eval(v.get("when", {})):
                    return v.get("text")
            return None
        return item.get("text", "")

    # ------------------------------------------------------- legacy: sections
    def render_section(self, s):
        t = s.get("type", "static")
        if t == "bottles": self._bottles(s)
        elif t == "conditional": self._cond_section(s)
        elif t == "static": self._static(s)

    def _bottles(self, s):
        for i in s.get("items", []):
            v = self._s(i["entity"])
            if v not in ("none", "unavailable"):
                self.print_item(f"{i['prefix']}{v}"); self.gap()

    def _cond_section(self, s):
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
        """Legacy condition grammar, also used by Advanced items."""
        if not c: return True
        if "any_on" in c: return any(self._on(e) for e in c["any_on"])
        if "all_on" in c: return all(self._on(e) for e in c["all_on"])
        if "entity_on" in c: return self._on(c["entity_on"])
        if "any_select_equals" in c:
            sp = c["any_select_equals"]
            return any(self._s(e) == sp["value"] for e in sp["entities"])
        return True
