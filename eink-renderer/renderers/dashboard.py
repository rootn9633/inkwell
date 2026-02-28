"""Dashboard Renderer — Grid-based sensor/status cards."""
from PIL import Image, ImageDraw, ImageFont


def render(image, draw, config, hardware, states, fonts):
    width, height = hardware["width"], hardware["height"]
    bg, fg = hardware.get("bg_color", 0), hardware.get("fg_color", 255)
    f_title = fonts.get("title", next(iter(fonts.values())))
    f_label = fonts.get("label", fonts.get("body", next(iter(fonts.values()))))
    f_value = fonts.get("value", fonts.get("header", next(iter(fonts.values()))))

    title = config.get("dashboard_title", "")
    title_h = 0
    if title:
        title_h = 60
        draw.text((20, 10), title, font=f_title, fill=fg)
        draw.line([(20, title_h), (width - 20, title_h)], fill=fg, width=1)
        title_h += 10

    grid = config.get("grid", {})
    cols = grid.get("columns", 3)
    row_h = grid.get("row_height", 120)
    pad = grid.get("padding", 10)
    cell_w = (width - pad * (cols + 1)) // cols

    for i, card in enumerate(config.get("cards", [])):
        col, row = i % cols, i // cols
        x = pad + col * (cell_w + pad)
        y = title_h + pad + row * (row_h + pad)
        if y + row_h > height: break
        ct = card.get("type", "text")
        if ct == "spacer": continue
        draw.rectangle([(x, y), (x + cell_w, y + row_h)], outline=fg, width=1)
        if card.get("label"):
            draw.text((x + 8, y + 5), card["label"], font=f_label, fill=fg)
        state = states.get(card.get("entity", ""), "?")
        if ct == "sensor":
            draw.text((x+8, y+row_h//3), f"{state}{card.get('unit','')}", font=f_value, fill=fg)
        elif ct == "status":
            txt = card.get("on_text","ON") if state == "on" else card.get("off_text","OFF")
            draw.text((x+8, y+row_h//3), txt, font=f_value, fill=fg)
        elif ct == "text":
            draw.text((x+8, y+row_h//3), card.get("text",""), font=f_value, fill=fg)
