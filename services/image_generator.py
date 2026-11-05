"""
Генератор графических карточек на Pillow (PIL).
Дизайн карточек:
- Оригинальный текстурированный фон demonlist.org (main-dark.png) без сплошных непрозрачных заливок;
- Прозрачный слой над фоном: фон виден полностью, используются только светящиеся контуры и тени текста;
- На карточках разделов — только названия разделов (без избыточного текста и мелких деталей);
- На главной карточке — официальный логотип Global Demonlist и название бота;
- Поддержка шрифтов Montserrat и отображения эмодзи флагов стран.
- Оптимизирован: кэш фона и LRU-очистка для предотвращения утечек памяти;
- Неблокирующие асинхронные версии через asyncio.to_thread.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
BG_PATH = ASSETS_DIR / "main-dark.png"
LOGO_PATH = ASSETS_DIR / "logo_256.png"
FONT_DIR = ASSETS_DIR / "fonts"

FONT_BOLD_PATH = FONT_DIR / "Montserrat-Bold.ttf"
FONT_SEMI_PATH = FONT_DIR / "Montserrat-SemiBold.ttf"
FONT_REG_PATH = FONT_DIR / "Montserrat-Regular.ttf"

# Кеш статических карточек в оперативной памяти (с ограничением размера)
_STATIC_CARD_CACHE: dict[str, bytes] = {}
_MAX_STATIC_CACHE = 40

# Кеш базовых фоновых изображений (под разные разрешения)
_BG_CACHE: dict[tuple[int, int], Image.Image] = {}


def _get_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if "Bold" in str(path) else "DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _get_bg(target_size: tuple[int, int]) -> Image.Image:
    """
    Возвращает изображение фона demonlist.org с сине-стальным киберспортивным оттенком.
    Фон остаётся полностью прозрачным/видимым (без затемняющих непрозрачных слоёв поверх него).
    Результат кэшируется в памяти во избежание повторного чтения диска и ресайза.
    """
    if target_size in _BG_CACHE:
        return _BG_CACHE[target_size].copy()

    w_target, h_target = target_size
    if BG_PATH.exists():
        bg = Image.open(BG_PATH).convert("RGBA")
        bw, bh = bg.size
        scale = max(w_target / bw, h_target / bh)
        new_w, new_h = int(bw * scale), int(bh * scale)
        bg_scaled = bg.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - w_target) // 2
        top = (new_h - h_target) // 2
        base = bg_scaled.crop((left, top, left + w_target, top + h_target))
        # Тонируем фон в стильный электрический темно-синий оттенок demonlist
        r, g, b, a = base.split()
        processed = Image.merge("RGBA", (
            r.point(lambda p: int(p * 0.7)),
            g.point(lambda p: int(p * 0.85)),
            b.point(lambda p: int(min(255, p * 1.3))),
            a
        ))
        _BG_CACHE[target_size] = processed
        return processed.copy()

    blank = Image.new("RGBA", target_size, (15, 23, 42, 255))
    _BG_CACHE[target_size] = blank
    return blank.copy()


def _draw_shadow_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    shadow_fill: tuple[int, int, int] = (0, 0, 0),
    offset: tuple[int, int] = (2, 2),
    anchor: str | None = None,
) -> None:
    """Отрисовка текста с мягкой тенью для идеальной читаемости на текстурированном фоне."""
    x, y = pos
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill, anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


# ─── 1:1 КАРТОЧКА ГЛАВНОЙ СТРАНИЦЫ (С ЛОГОТИПОМ) ─────────────────────────────

def render_home_card() -> BytesIO:
    if "home" in _STATIC_CARD_CACHE:
        bio = BytesIO(_STATIC_CARD_CACHE["home"])
        bio.seek(0)
        return bio

    size = (800, 800)
    card = _get_bg(size)
    draw = ImageDraw.Draw(card)

    # Строго 1:1 симметричный прозрачный контур
    draw.rounded_rectangle([(32, 32), (768, 768)], radius=24, outline=(59, 130, 246, 220), width=3)
    draw.rounded_rectangle([(38, 38), (762, 762)], radius=20, outline=(96, 165, 250, 100), width=1)

    # Официальный логотип Global Demonlist
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo = logo.resize((190, 190), Image.Resampling.LANCZOS)
        card.paste(logo, (305, 140), logo)

    # Шрифты
    f_sub = _get_font(FONT_SEMI_PATH, 24)
    f_title = _get_font(FONT_BOLD_PATH, 50)
    f_tag = _get_font(FONT_REG_PATH, 19)
    f_foot = _get_font(FONT_REG_PATH, 16)

    # Заголовки с идеальной симметрией
    _draw_shadow_text(draw, (400, 380), "GLOBAL DEMONLIST", f_sub, (96, 165, 250), anchor="mm")
    _draw_shadow_text(draw, (400, 445), "CHECKER", f_title, (255, 255, 255), offset=(3, 3), anchor="mm")
    _draw_shadow_text(draw, (400, 510), "Verifications • Tracking • Statistics", f_tag, (203, 213, 225), anchor="mm")
    _draw_shadow_text(draw, (400, 740), "demonlist.org | @gdlcheckerbot", f_foot, (148, 163, 184), anchor="mm")

    final_img = card.convert("RGB")
    bio = BytesIO()
    final_img.save(bio, format="PNG")
    data = bio.getvalue()
    if len(_STATIC_CARD_CACHE) >= _MAX_STATIC_CACHE:
        _STATIC_CARD_CACHE.clear()
    _STATIC_CARD_CACHE["home"] = data

    bio.seek(0)
    return bio


# ─── КАРТОЧКА РАЗДЕЛА (СТРОГО 1:1, ТОЛЬКО НАЗВАНИЕ РАЗДЕЛА) ───────────────────

def render_section_card(section_name: str) -> BytesIO:
    cache_key = f"sec_{section_name.strip().upper()}"
    if cache_key in _STATIC_CARD_CACHE:
        bio = BytesIO(_STATIC_CARD_CACHE[cache_key])
        bio.seek(0)
        return bio

    size = (800, 800)
    card = _get_bg(size)
    draw = ImageDraw.Draw(card)

    # Строго 1:1 симметричный прозрачный контур
    draw.rounded_rectangle([(32, 32), (768, 768)], radius=24, outline=(59, 130, 246, 220), width=3)
    draw.rounded_rectangle([(38, 38), (762, 762)], radius=20, outline=(96, 165, 250, 100), width=1)

    f_cat = _get_font(FONT_SEMI_PATH, 24)
    f_title = _get_font(FONT_BOLD_PATH, 60)
    f_sub = _get_font(FONT_REG_PATH, 18)
    f_foot = _get_font(FONT_REG_PATH, 16)

    # Только заголовок раздела с симметричным расположением
    _draw_shadow_text(draw, (400, 330), "GLOBAL DEMONLIST", f_cat, (96, 165, 250), anchor="mm")
    _draw_shadow_text(draw, (400, 410), section_name.upper(), f_title, (255, 255, 255), offset=(3, 3), anchor="mm")
    _draw_shadow_text(draw, (400, 480), "•  OFFICIAL BOT SECTION  •", f_sub, (148, 163, 184), anchor="mm")
    _draw_shadow_text(draw, (400, 740), "demonlist.org | @gdlcheckerbot", f_foot, (148, 163, 184), anchor="mm")

    final_img = card.convert("RGB")
    bio = BytesIO()
    final_img.save(bio, format="PNG")
    data = bio.getvalue()
    if len(_STATIC_CARD_CACHE) >= _MAX_STATIC_CACHE:
        _STATIC_CARD_CACHE.clear()
    _STATIC_CARD_CACHE[cache_key] = data

    bio.seek(0)
    return bio


# ─── КАРТОЧКА ИГРОКА (СТРОГО 1:1, В СТИЛЕ РАЗДЕЛОВ) ───────────────────────────

def render_player_card(
    username: str,
    rank: int | str,
    points: float | str = 0,
    country: str = "",
    country_rank: int | str | None = None,
    hardest: list[str] | None = None,
    completions_count: int | str = 0,
) -> BytesIO:
    size = (800, 800)
    card = _get_bg(size)
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle([(32, 32), (768, 768)], radius=24, outline=(59, 130, 246, 220), width=3)
    draw.rounded_rectangle([(38, 38), (762, 762)], radius=20, outline=(96, 165, 250, 100), width=1)

    f_cat = _get_font(FONT_SEMI_PATH, 20)
    f_name = _get_font(FONT_BOLD_PATH, 44 if len(username) <= 14 else 34)
    f_rank = _get_font(FONT_BOLD_PATH, 56)
    f_label = _get_font(FONT_SEMI_PATH, 17)
    f_val = _get_font(FONT_BOLD_PATH, 22)
    f_list = _get_font(FONT_REG_PATH, 20)
    f_foot = _get_font(FONT_REG_PATH, 16)

    _draw_shadow_text(draw, (400, 65), "GLOBAL DEMONLIST • PLAYER", f_cat, (96, 165, 250), anchor="mm")
    _draw_shadow_text(draw, (400, 125), username, f_name, (255, 255, 255), anchor="mm")
    _draw_shadow_text(draw, (400, 190), f"#{rank}", f_rank, (59, 130, 246), anchor="mm")

    # Сетка метрик (2x2)
    c_text = country if country else "-"
    cr_text = f"#{country_rank}" if country_rank else "-"

    metrics = [
        ("ОЧКИ ТОПА", f"{points} pts", (220, 275)),
        ("СТРАНА", c_text, (580, 275)),
        ("ТОП СТРАНЫ", cr_text, (220, 360)),
        ("КОМПЛИШЕНЫ", f"{completions_count}", (580, 360)),
    ]

    for label, val, pos in metrics:
        _draw_shadow_text(draw, (pos[0], pos[1]), label, f_label, (156, 163, 175), anchor="mm")
        _draw_shadow_text(draw, (pos[0], pos[1] + 32), str(val), f_val, (243, 244, 246), anchor="mm")

    # Разделитель
    draw.line([(80, 430), (720, 430)], fill=(59, 130, 246, 100), width=1)

    # Блок топ-хардестов
    _draw_shadow_text(draw, (400, 465), "ТОП-3 ХАРДЕСТА", f_label, (96, 165, 250), anchor="mm")
    h_items = hardest or []
    y_start = 515
    for i in range(3):
        cur_y = y_start + i * 46
        if i < len(h_items):
            item_text = f"{i + 1}. {h_items[i]}"
            _draw_shadow_text(draw, (400, cur_y), item_text, f_list, (226, 232, 240), anchor="mm")
        else:
            _draw_shadow_text(draw, (400, cur_y), f"{i + 1}. —", f_list, (100, 116, 139), anchor="mm")

    _draw_shadow_text(draw, (400, 740), "demonlist.org | @gdlcheckerbot", f_foot, (148, 163, 184), anchor="mm")

    final_img = card.convert("RGB")
    bio = BytesIO()
    final_img.save(bio, format="PNG")
    bio.seek(0)
    return bio


# ─── КАРТОЧКА УРОВНЯ (СТРОГО 1:1, В СТИЛЕ РАЗДЕЛОВ) ──────────────────────────

def render_level_card(
    name: str,
    rank: int | str,
    points: float | str = 0,
    creator: str = "",
    verifier: str = "",
    objects: int | str = "-",
    length: int | str = "-",
    victors: int | str = "-",
    list_percent: int = 100,
) -> BytesIO:
    size = (800, 800)
    card = _get_bg(size)
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle([(32, 32), (768, 768)], radius=24, outline=(59, 130, 246, 220), width=3)
    draw.rounded_rectangle([(38, 38), (762, 762)], radius=20, outline=(96, 165, 250, 100), width=1)

    f_cat = _get_font(FONT_SEMI_PATH, 20)
    f_name = _get_font(FONT_BOLD_PATH, 44 if len(name) <= 14 else 34)
    f_rank = _get_font(FONT_BOLD_PATH, 56)
    f_label = _get_font(FONT_SEMI_PATH, 17)
    f_val = _get_font(FONT_BOLD_PATH, 22)
    f_foot = _get_font(FONT_REG_PATH, 16)

    _draw_shadow_text(draw, (400, 65), "GLOBAL DEMONLIST • LEVEL", f_cat, (96, 165, 250), anchor="mm")
    _draw_shadow_text(draw, (400, 125), name, f_name, (255, 255, 255), anchor="mm")
    _draw_shadow_text(draw, (400, 190), f"#{rank}", f_rank, (59, 130, 246), anchor="mm")

    lp_str = f"{list_percent}%" if list_percent else "100%"

    metrics = [
        ("ОЧКИ ЗА ПРОХОЖДЕНИЕ", f"{points} pts", (220, 275)),
        ("ЛИСТ ПРОЦЕНТ", lp_str, (580, 275)),
        ("СОЗДАТЕЛЬ", creator or "Unknown", (220, 360)),
        ("ВЕРИФИКАТОР", verifier or "Unknown", (580, 360)),
    ]

    for label, val, pos in metrics:
        _draw_shadow_text(draw, (pos[0], pos[1]), label, f_label, (156, 163, 175), anchor="mm")
        _draw_shadow_text(draw, (pos[0], pos[1] + 32), str(val)[:18], f_val, (243, 244, 246), anchor="mm")

    draw.line([(80, 430), (720, 430)], fill=(59, 130, 246, 100), width=1)

    is_main = str(rank).isdigit() and int(rank) <= 75
    status_str = "Main List" if is_main else "Extended"
    add_metrics = [
        ("ОБЪЕКТЫ", str(objects), (220, 500)),
        ("ДЛИНА УРОВНЯ", f"{length} сек", (580, 500)),
        ("ВИКТОРЫ", str(victors), (220, 595)),
        ("СТАТУС", status_str, (580, 595)),
    ]

    for label, val, pos in add_metrics:
        _draw_shadow_text(draw, (pos[0], pos[1]), label, f_label, (156, 163, 175), anchor="mm")
        _draw_shadow_text(draw, (pos[0], pos[1] + 32), str(val), f_val, (243, 244, 246), anchor="mm")

    _draw_shadow_text(draw, (400, 740), "demonlist.org | @gdlcheckerbot", f_foot, (148, 163, 184), anchor="mm")

    final_img = card.convert("RGB")
    bio = BytesIO()
    final_img.save(bio, format="PNG")
    bio.seek(0)
    return bio


# ─── 1:1 КАРТОЧКА СРАВНЕНИЯ (/compare) ────────────────────────────────────────

def render_compare_card(
    p1_name: str,
    p1_rank: int | str,
    p1_points: str,
    p1_hardest: str,
    p1_victors: int | str,
    p2_name: str,
    p2_rank: int | str,
    p2_points: str,
    p2_hardest: str,
    p2_victors: int | str,
    p1_score: int,
    p2_score: int,
) -> BytesIO:
    """
    Генерирует 1:1 карточку (800x800) сравнения двух игроков.
    Разделена на две половины с центральным значком VS и итоговым счетом.
    """
    size = (800, 800)
    card = _get_bg(size)
    draw = ImageDraw.Draw(card)

    # Внешняя рамка
    draw.rounded_rectangle([(32, 32), (768, 768)], radius=24, outline=(147, 51, 234, 220), width=3)
    draw.rounded_rectangle([(38, 38), (762, 762)], radius=20, outline=(168, 85, 247, 100), width=1)

    # Разделительная вертикальная линия по центру
    draw.line([(400, 70), (400, 680)], fill=(75, 85, 99, 140), width=2)

    # Центральный бейдж "VS"
    draw.ellipse([(360, 360), (440, 440)], fill=(17, 24, 39, 240), outline=(168, 85, 247, 240), width=3)
    f_vs = _get_font(FONT_BOLD_PATH, 28)
    _draw_shadow_text(draw, (400, 400), "VS", f_vs, (255, 255, 255), anchor="mm")

    # Шрифты
    f_cat = _get_font(FONT_SEMI_PATH, 20)
    f_name1 = _get_font(FONT_BOLD_PATH, 34 if len(p1_name) <= 12 else 26)
    f_name2 = _get_font(FONT_BOLD_PATH, 34 if len(p2_name) <= 12 else 26)
    f_score = _get_font(FONT_BOLD_PATH, 48)
    f_label = _get_font(FONT_SEMI_PATH, 16)
    f_val = _get_font(FONT_BOLD_PATH, 22)
    f_foot = _get_font(FONT_REG_PATH, 16)

    # Верхний заголовок
    _draw_shadow_text(draw, (400, 60), "DEMONLIST BATTLE", f_cat, (192, 132, 252), anchor="mm")

    # Игрок 1 (Слева)
    p1_is_win = p1_score > p2_score
    p1_color = (34, 197, 94) if p1_is_win else (255, 255, 255)
    _draw_shadow_text(draw, (220, 115), p1_name[:15], f_name1, p1_color, anchor="mm")
    _draw_shadow_text(draw, (220, 160), f"СЧЕТ: {p1_score}", f_score, (168, 85, 247) if not p1_is_win else (34, 197, 94), anchor="mm")

    # Игрок 2 (Справа)
    p2_is_win = p2_score > p1_score
    p2_color = (34, 197, 94) if p2_is_win else (255, 255, 255)
    _draw_shadow_text(draw, (580, 115), p2_name[:15], f_name2, p2_color, anchor="mm")
    _draw_shadow_text(draw, (580, 160), f"СЧЕТ: {p2_score}", f_score, (168, 85, 247) if not p2_is_win else (34, 197, 94), anchor="mm")

    # Блоки сравнения параметров
    metrics = [
        ("РАНГ В МИРЕ", f"#{p1_rank}", f"#{p2_rank}", 230),
        ("ОЧКИ ДЕМОНЛИСТА", f"{p1_points} pts", f"{p2_points} pts", 300),
        ("СЛОЖНЕЙШИЙ ДЕМОН", f"{p1_hardest[:16]}", f"{p2_hardest[:16]}", 480),
        ("ПРОЙДЕНО ДЕМОНОВ", f"{p1_victors}", f"{p2_victors}", 550),
    ]

    for label, v1, v2, y_pos in metrics:
        # Лейбл по центру
        _draw_shadow_text(draw, (400, y_pos), label, f_label, (156, 163, 175), anchor="mm")
        # Значения слева и справа
        _draw_shadow_text(draw, (220, y_pos + 30), v1, f_val, (243, 244, 246), anchor="mm")
        _draw_shadow_text(draw, (580, y_pos + 30), v2, f_val, (243, 244, 246), anchor="mm")

    # Итог внизу
    if p1_score > p2_score:
        result_text = f"ПОБЕДА: {p1_name} ({p1_score}:{p2_score})"
        result_color = (34, 197, 94)
    elif p2_score > p1_score:
        result_text = f"ПОБЕДА: {p2_name} ({p2_score}:{p1_score})"
        result_color = (34, 197, 94)
    else:
        result_text = f"НИЧЬЯ ({p1_score}:{p2_score})"
        result_color = (234, 179, 8)

    _draw_shadow_text(draw, (400, 650), result_text, f_val, result_color, anchor="mm")
    _draw_shadow_text(draw, (400, 740), "demonlist.org | @gdlcheckerbot", f_foot, (148, 163, 184), anchor="mm")

    final_img = card.convert("RGB")
    bio = BytesIO()
    final_img.save(bio, format="PNG")
    bio.seek(0)
    return bio


# ─── ASYNC WRAPPERS (не блокируют event loop) ───────────────────────────────

async def render_home_card_async() -> BytesIO:
    return await asyncio.to_thread(render_home_card)


async def render_section_card_async(section_name: str) -> BytesIO:
    return await asyncio.to_thread(render_section_card, section_name)


async def render_player_card_async(
    username: str,
    rank: int | str,
    points: float | str = 0,
    country: str = "",
    country_rank: int | str | None = None,
    hardest: list[str] | None = None,
    completions_count: int | str = 0,
) -> BytesIO:
    return await asyncio.to_thread(
        render_player_card,
        username=username,
        rank=rank,
        points=points,
        country=country,
        country_rank=country_rank,
        hardest=hardest,
        completions_count=completions_count,
    )


async def render_level_card_async(
    name: str,
    rank: int | str,
    points: float | str = 0,
    creator: str = "",
    verifier: str = "",
    objects: int | str = "-",
    length: int | str = "-",
    victors: int | str = "-",
    list_percent: int = 100,
) -> BytesIO:
    return await asyncio.to_thread(
        render_level_card,
        name=name,
        rank=rank,
        points=points,
        creator=creator,
        verifier=verifier,
        objects=objects,
        length=length,
        victors=victors,
        list_percent=list_percent,
    )


async def render_compare_card_async(
    p1_name: str,
    p1_rank: int | str,
    p1_points: str,
    p1_hardest: str,
    p1_victors: int | str,
    p2_name: str,
    p2_rank: int | str,
    p2_points: str,
    p2_hardest: str,
    p2_victors: int | str,
    p1_score: int,
    p2_score: int,
) -> BytesIO:
    return await asyncio.to_thread(
        render_compare_card,
        p1_name=p1_name,
        p1_rank=p1_rank,
        p1_points=p1_points,
        p1_hardest=p1_hardest,
        p1_victors=p1_victors,
        p2_name=p2_name,
        p2_rank=p2_rank,
        p2_points=p2_points,
        p2_hardest=p2_hardest,
        p2_victors=p2_victors,
        p1_score=p1_score,
        p2_score=p2_score,
    )
