import tkinter
import tkinter as tk
from pydoc import visiblename
from tkinter.messagebox import showinfo
from tkinter import ttk
from tkinter import messagebox
import time

# Импорт конфигураций
from configs import *

# === ВАЖНО: Импортируем общие переменные состояния из logic ===
# Это нужно, чтобы и GUI, и ESP32 (в esp32_core) работали с ОДНИМИ данными.

# База данных
from db import sync_add_route, sync_delete_route, sync_set_passed

# Импорт ядра ESP32
# Убедись, что файл esp32_core.py (который я дал в прошлом ответе) лежит рядом
from esp8266 import (
    send_servo_for_switch,
    recalc_signal_aspects,
    start_invite_mode
)

root = tk.Tk()
root.title("Станция (ESP32 Wi-Fi)")
canvas = tk.Canvas(root, width=1250, height=600, bg="#6C9092")

line_color_main = "white"


def quit_function():
    # Можно добавить закрытие сокета, если нужно
    exit()


CANVAS_W = 1200
CANVAS_H = 600
root.resizable(False, False)


def showInfo(title, msg):
    showinfo(title=title, message=msg)


#########################################        ФУНКЦИЯ ТУПИКОВ                ##############################################

def drawDeadEnd(name, direction, offset):
    x = positions[name][0]
    y = positions[name][1]
    if direction == "right":
        canvas.create_line(x, y, x + offset, y, width=4, fill=line_color_main)
        canvas.create_line(x + offset, y - 15, x + offset, y + 15, width=6)
    elif direction == "left":
        canvas.create_line(x, y, x - offset, y, width=4, fill=line_color_main)
        canvas.create_line(x - offset, y - 15, x - offset, y + 15, width=6, fill=line_color_main)


#########################################        МАССИВЫ ЭЛЕМЕНТОВ               ##############################################
selected_nodes = []
MAX_SELECTED = 2
node_ids = {}
switch_ids = {}
segment_ids = {}
diag_ids = {}
signal_ids = {}
segment_to_block = {}
split_diag_ids = {}
# active_routes = {} # УДАЛЕНО: Берем из logic
route_counter = 1
occupied_segments = set()
occupied_diagonals = set()
diagonal_modes = {}
switch_text_ids = {}
switch_indicator_ids = {}
blinking_diags = set()
blinking_routes = set()
last_switch_check = {}
current_mode = "maneuver"
btn_maneuver = None
btn_train = None

# Словари занятости (diag_occ_train) можно оставить тут, если они только для GUI,
# но seg_occ_train должен быть общим с logic.py
diag_occ_train = {
    "ALB_Turn1": 1,
    "ALB_Turn2": 1,
    "ALB_Turn8": 1,
    "ALB_Turn4": 1,
    "ALB_Turn6": 1,
}

for block, segs in segment_groups.items():
    for seg in segs:
        segment_to_block[seg] = block
        segment_to_block[(seg[1], seg[0])] = block

SIGNAL_OFF_COLOR = "#202020"
DEBUG_SIGNALS_FRAME = False


def _indices_for_color(sig_name: str, color: str) -> list[int]:
    cols = signals_config[sig_name]["colors"]
    return [i for i, c in enumerate(cols) if c == color]


def gui_lamps_for_aspect(sig_name: str, aspect: str) -> tuple[set[int], set[int]]:
    """
    Возвращает:
      lit   = какие лампы должны светиться (индексы)
      blink = какие лампы должны мигать (индексы)
    """
    lit: set[int] = set()
    blink: set[int] = set()

    reds = _indices_for_color(sig_name, "red")
    greens = _indices_for_color(sig_name, "green")
    whites = _indices_for_color(sig_name, "white")
    blues = _indices_for_color(sig_name, "blue")
    yellows = _indices_for_color(sig_name, "yellow")

    if aspect == "off":
        return set(), set()

    if aspect == "red" and reds:
        lit.add(reds[0])
    elif aspect == "green" and greens:
        lit.add(greens[0])
    elif aspect == "white" and whites:
        lit.add(whites[0])
    elif aspect == "blue" and blues:
        lit.add(blues[0])
    elif aspect == "one_yellow" and yellows:
        lit.add(yellows[0])
    elif aspect == "two_yellow" and yellows:
        for i in yellows:
            lit.add(i)
    elif aspect == "invite":
        if reds:
            lit.add(reds[0])
        if whites:
            lit.add(whites[0])
            blink.add(whites[0])

    return lit, blink


def gui_lamps_from_state(sig_name: str) -> tuple[set[int], set[int]]:
    lit: set[int] = set()
    blink: set[int] = set()

    st = signals_state.get(sig_name)
    if not st:
        return lit, blink

    lamps = st.get("lamps", {})

    # индексы физических ламп и их цвета
    lamp_indices = {
        color: _indices_for_color(sig_name, color)
        for color in lamps.keys()
    }

    for color, cfg in lamps.items():
        idxs = lamp_indices.get(color, [])
        if not idxs:
            continue
        if cfg.get("on", False):
            for i in idxs:
                lit.add(i)
                if cfg.get("blink", False):
                    blink.add(i)

    return lit, blink


def invite_signal_on_off(signalName):
    # Логика GUI
    if signalName in signals_state:
        is_on = signals_state[signalName]["lamps"]["white"]["on"]
        signals_state[signalName]["lamps"]["white"]["on"] = not is_on

        # Если включили - отправляем команду на ESP
        if not is_on:  # стало True
            start_invite_mode()
        # Если выключили - пересчитываем обычные сигналы
        else:
            recalc_signal_aspects()


def recalc_signals_to_red(rid) -> None:
    AdditionalSignals = ["ALB_Sect1-2", "ALB_Sect1-2_2", "ALB_Sect2"]
    if rid != None:
        data = active_routes.get(rid)
        if not data: return
        a = data.get("start")
        b = data.get("end")
        key = (a, b)
        cfg = ROUTE_SIGNAL_MAP.get(key)
        if not cfg: return

        for name in cfg:
            if name in AdditionalSignals:
                continue
            for lamp_name, lamp in signals_state[name]["lamps"].items():
                if lamp_name == "white" and name == "CH":
                    continue
                lamp["on"] = False
                lamp["blink"] = False
            if "red" in signals_state[name]["lamps"]:
                signals_state[name]["lamps"]["red"]["on"] = True
            elif "blue" in signals_state[name]["lamps"]:
                signals_state[name]["lamps"]["blue"]["on"] = True
            else:
                for colors in signals_state[name]["lamps"]:
                    signals_state[name]["lamps"][colors]["on"] = False
    else:
        for name in signals_state.keys():
            if name in AdditionalSignals:
                continue
            for lamp_name, lamp in signals_state[name]["lamps"].items():
                lamp["on"] = False
                lamp["blink"] = False
            if "red" in signals_state[name]["lamps"]:
                signals_state[name]["lamps"]["red"]["on"] = True
            elif "blue" in signals_state[name]["lamps"]:
                signals_state[name]["lamps"]["blue"]["on"] = True
            else:
                for colors in signals_state[name]["lamps"]:
                    if colors == "white":
                        continue
                    signals_state[name]["lamps"][colors]["on"] = False

    # После изменения логического состояния, обновляем ESP
    recalc_signal_aspects()


def recalc_signals_from_active_routes(route):
    key = route
    cfg = ROUTE_SIGNAL_MAP.get(key)
    if not cfg: return

    for name in cfg:
        if name in signals_state:
            for lamp_name, lamp in signals_state[name]["lamps"].items():
                if name == "CH" and lamp_name == "white":
                    continue
                lamp["on"] = False
                lamp["blink"] = False
        for color, lamp_cfg in cfg[name]["lamps"].items():
            if color not in signals_state[name]["lamps"]:
                continue
            if color == "white" and name == "CH":
                continue
            signals_state[name]["lamps"][color]["on"] = lamp_cfg.get("on", False)
            signals_state[name]["lamps"][color]["blink"] = lamp_cfg.get("blink", False)

    # После изменения логического состояния, обновляем ESP
    recalc_signal_aspects()


def update_signals_visual_v2() -> None:
    # 1. Синхронизируем состояние с ESP
    recalc_signal_aspects()

    # 2. Отрисовка на экране
    for name in signals_config.keys():
        if name not in signal_ids:
            continue

        ids = signal_ids[name]
        cfg_colors = signals_config[name]["colors"]

        from configs import signal_blink_phase as current_phase
        lit, blink = gui_lamps_from_state(name)

        if name == "CH":
            # ids[0] = индикатор белого (пригласительный)
            # ids[1] = индикатор основного аспекта (красный/жёлтый/зелёный)
            white_idx = cfg_colors.index("white") if "white" in cfg_colors else -1
            if white_idx >= 0 and white_idx in lit:
                fill0 = SIGNAL_OFF_COLOR if (white_idx in blink and not current_phase) else "white"
            else:
                fill0 = SIGNAL_OFF_COLOR
            if len(ids) > 0:
                canvas.itemconfig(ids[0], fill=fill0)

            main_fill = SIGNAL_OFF_COLOR
            for idx in lit:
                if idx < len(cfg_colors) and cfg_colors[idx] != "white":
                    c = cfg_colors[idx]
                    if c == "yellow2":
                        c = "yellow"
                    main_fill = SIGNAL_OFF_COLOR if (idx in blink and not current_phase) else c
                    break
            if len(ids) > 1:
                canvas.itemconfig(ids[1], fill=main_fill)
        else:
            # Один кружок — показывает текущий горящий цвет
            if len(ids) > 0:
                fill = SIGNAL_OFF_COLOR
                for idx in lit:
                    if idx < len(cfg_colors):
                        c = cfg_colors[idx]
                        if c == "yellow2":
                            c = "yellow"
                        fill = SIGNAL_OFF_COLOR if (idx in blink and not current_phase) else c
                        break
                canvas.itemconfig(ids[0], fill=fill)

    import configs as cfg
    cfg.signal_blink_phase = not cfg.signal_blink_phase

    if signals_state["CH"]["lamps"]["red"]["on"]:
        signals_state["ALB_Sect1-2_2"]["lamps"]["yellow"]["on"] = True
        signals_state["ALB_Sect1-2_2"]["lamps"]["green"]["on"] = False
    if signals_state["CH"]["lamps"]["yellow"]["on"]:
        signals_state["ALB_Sect1-2_2"]["lamps"]["yellow"]["on"] = False
        signals_state["ALB_Sect1-2_2"]["lamps"]["green"]["on"] = True

    root.after(350, update_signals_visual_v2)


def format_routes(routes_dict):
    if not routes_dict:
        return "Маршруты не заданы."
    seen = set()
    lines = []
    for a, b in routes_dict.keys():
        if (a, b) in seen:
            continue
        seen.add((a, b))
        lines.append(f"{a} \u2192 {b}")
    return "\n".join(lines)


def show_maneuver_routes():
    global settingRoute
    if settingRoute == True:
        return
    set_mode("maneuver")
    msg = "Маневровые маршруты:\n\n" + format_routes(routes)
    showInfo("МАНЕВРОВЫЕ", msg)


def show_train_routes():
    global settingRoute
    if settingRoute == True:
        return
    set_mode("train")
    msg = "Поездные маршруты:\n\n" + format_routes(train_routes)
    showInfo("ПОЕЗДНЫЕ", msg)


def make_signal_state(name, colors):
    if name not in signals_state:
        signals_state[name] = {
            "lamps": {
                color: {"on": False, "blink": False}
                for color in colors
            }
        }


#########################################       ОТРИСОВКА СВЕТОФОРОВ                ##############################################
def drawSignal(name, mount="bottom", pack_side="right", count=3, colors=None):
    x, y = positions[name]
    r = 7
    # Чтобы кружки касались, расстояние между центрами должно быть 2*r
    # Убираем +3, чтобы не было зазора
    gap = 2 * r
    stand_len = 15
    bar_len = 10

    dy_sign = -1 if mount == "top" else 1
    sx, sy = x, y + dy_sign * stand_len

    # Рисуем вертикальную стойку
    canvas.create_line(x, y, sx, sy, width=2, fill=line_color_main)

    hx_sign = 1 if pack_side == "right" else -1

    # Горизонтальная планка от стойки до края первого сигнала
    hx0, hy0 = sx, sy
    hx1, hy1 = sx + hx_sign * bar_len, sy
    canvas.create_line(hx0, hy0, hx1, hy1, width=2, fill=line_color_main)

    ids = []
    # Центр первого кружка (край планки + радиус)
    start_cx = hx1 + hx_sign * r
    draw_count = 2 if name == "CH" else 1

    for i in range(draw_count):
        cx = start_cx + hx_sign * i * gap
        cy = sy

        # Рисуем кружок. Теперь они будут плотно прилегать друг к другу
        oid = canvas.create_oval(
            cx - r, cy - r,
            cx + r, cy + r,
            outline=line_color_main, width=1, fill=SIGNAL_OFF_COLOR
        )
        ids.append(oid)

    make_signal_state(name, colors)
    signal_ids[name] = ids

#########################################        ФУНКЦИИ МАРШРУТОВ                ##############################################
def paint_diagonal(name, color):
    if name in split_diag_ids:
        return
    for line_id in diag_ids[name]:
        canvas.itemconfig(line_id, fill=color)


def paint_segment(key, color):
    seg_id = segment_ids.get(key)
    if seg_id is None:
        return
    canvas.itemconfig(seg_id, fill=color)


def paint_route(start, end, color="yellow"):
    key = (start, end)
    if current_mode == "maneuver":
        if key not in routes:
            key = (end, start)
            if key not in routes:
                print("Маршрут не найден")
                return

        for step in routes[key]:
            if step["type"] == "segment":
                paint_segment(step["id"], color)
            elif step["type"] == "diag":
                paint_diagonal(step["name"], color)
            else:
                print("Неизвестный тип шага:", step)
    if current_mode == "train":
        if key not in train_routes:
            key = (end, start)
            if key not in train_routes:
                print("Маршрут не найден")
                return

        for step in train_routes[key]:
            if step["type"] == "segment":
                paint_segment(step["id"], color)
            elif step["type"] == "diag":
                paint_diagonal(step["name"], color)
            else:
                print("Неизвестный тип шага:", step)


#########################################        ФУНКЦИИ ВКЛ/ОТКЛ СТРЕЛОК               ##############################################
def setBranchRight(nameDiag, offset):
    if nameDiag in split_diag_ids.keys():
        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partA'][0])
        canvas.coords(split_diag_ids[nameDiag]['partA'][0], x1, y1 - offset, x2, y2 - offset)

        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partA'][1])
        canvas.coords(split_diag_ids[nameDiag]['partA'][1], x1, y1 - offset, x2, y2)

        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partB'][0])
        canvas.coords(split_diag_ids[nameDiag]['partB'][0], x1, y1, x2, y2 + offset + 1)

        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partB'][1])
        canvas.coords(split_diag_ids[nameDiag]['partB'][1], x1, y1 + offset + 1, x2, y2 + offset + 1)
    else:
        x1, y1, x2, y2 = canvas.coords(diag_ids[nameDiag][0])
        canvas.coords(diag_ids[nameDiag][0], x1, y1 + offset, x2, y2 + offset)
        x1, y1, x2, y2 = canvas.coords(diag_ids[nameDiag][2])
        canvas.coords(diag_ids[nameDiag][2], x1, y1 + offset, x2, y2)


def setBranchLeft(nameDiag, offset):
    if nameDiag in split_diag_ids.keys():
        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partA'][0])  # 1
        canvas.coords(split_diag_ids[nameDiag]['partA'][0], x1, y1 + offset, x2, y2 + offset)

        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partA'][1])  # 3
        canvas.coords(split_diag_ids[nameDiag]['partA'][1], x1, y1 + offset, x2, y2)

        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partB'][0])  # 2
        canvas.coords(split_diag_ids[nameDiag]['partB'][0], x1, y1, x2, y2 - offset - 1)

        x1, y1, x2, y2 = canvas.coords(split_diag_ids[nameDiag]['partB'][1])  # 4
        canvas.coords(split_diag_ids[nameDiag]['partB'][1], x1, y1 - offset - 1, x2, y2 - offset - 1)
    else:
        x1, y1, x2, y2 = canvas.coords(diag_ids[nameDiag][1])
        canvas.coords(diag_ids[nameDiag][1], x1, y1 + offset, x2, y2 + offset)
        x1, y1, x2, y2 = canvas.coords(diag_ids[nameDiag][2])
        canvas.coords(diag_ids[nameDiag][2], x1, y1, x2, y2 + offset)


def branchWidth(namediag, width):
    if namediag in split_diag_ids.keys():
        for part, lines in split_diag_ids[namediag].items():
            for line_id in lines:
                canvas.itemconfig(line_id, width=width)
    else:
        for lines in range(len(diag_ids[(namediag)])):
            canvas.itemconfig(diag_ids[namediag][lines], width=width)


def apply_diagonal_mode(nameDiag, mode):
    cfg = diagonal_config.get(nameDiag)
    if cfg is None:
        print(f"No config for {nameDiag}")
        return

    # === ОТПРАВКА КОМАНДЫ НА ESP32 ===
    # Если мы физически переключаем стрелку, шлем команду
    send_servo_for_switch(nameDiag, mode)
    # ================================

    left_cfg = cfg["left"]
    if left_cfg["exists"]:
        if mode in ("left", "both"):
            setBranchLeft(nameDiag, left_cfg["connected"])
            branchWidth(nameDiag, 6)
            if nameDiag == "ALB_Turn8":
                canvas.itemconfig(segment_ids[("M6H2", "H2")], width=6)
        else:
            setBranchLeft(nameDiag, left_cfg["disconnected"])
            branchWidth(nameDiag, 2)
            if nameDiag == "ALB_Turn2":
                canvas.itemconfig(segment_ids[("M2H1_mid", "M2H1_third")], width=2)
                # canvas.itemconfig(segment_ids[("H1", "M2H1_third")], width=2)

    right_cfg = cfg["right"]
    if right_cfg["exists"]:
        if mode in ("right", "both"):
            setBranchRight(nameDiag, right_cfg["connected"])
            branchWidth(nameDiag, 6)

            if nameDiag == "ALB_Turn4-6":
                canvas.itemconfig(segment_ids[("H1", "M2H1_third")], width=2)
                canvas.itemconfig(segment_ids[("M6", "M6H2")], width=2)
            if nameDiag == "ALB_Turn1":
                canvas.itemconfig(segment_ids[("M8mid", "M8")], width=2)
            if nameDiag == "ALB_Turn8":
                canvas.itemconfig(segment_ids[("M6H2", "H2")], width=2)
        else:
            setBranchRight(nameDiag, right_cfg["disconnected"])
            branchWidth(nameDiag, 2)
            if nameDiag == "ALB_Turn2":
                canvas.itemconfig(segment_ids[("M2H1_mid", "M2H1_third")], width=6)
            if nameDiag == "ALB_Turn4-6":
                canvas.itemconfig(segment_ids[("H1", "M2H1_third")], width=6)
                canvas.itemconfig(segment_ids[("M6", "M6H2")], width=6)
                text = canvas.itemcget(switch_text_ids["ALB_Turn2"], "text")
                if text == "-":
                    canvas.itemconfig(segment_ids[("M2H1_mid", "M2H1_third")], width=2)

            if nameDiag == "ALB_Turn1":
                canvas.itemconfig(segment_ids[("M8mid", "M8")], width=6)


def get_switch_state_num(name):
    mode = diagonal_modes.get(name)
    normal = default_switch_mode.get(name, "left")
    if mode is None:
        return "None"
    if mode == normal:
        return "+"
    else:
        return "-"


def get_switch_state_color(name):
    mode = diagonal_modes.get(name)
    normal = default_switch_mode.get(name, "left")
    if mode is None:
        return "grey"
    if mode == normal:
        return "green"  # плюс, нормальное положение
    else:
        return "yellow"  # переведена


def update_switch_indicator(name):
    rect = switch_indicator_ids.get(name)
    labelSwitch = switch_text_ids.get(name)
    if rect is None:
        return
    color = get_switch_state_color(name)
    text = get_switch_state_num(name)
    canvas.itemconfig(rect, fill=color)
    canvas.itemconfig(labelSwitch, text=text)


#########################################  МИНИ-Таблица стрелок (правый нижний угол)  #########################################
def create_switch_table():
    w = int(canvas["width"])
    h = int(canvas["height"])

    dy = 25
    total_height = dy * len(switch_list)
    y_start = h - total_height - 20

    x_text = w - 220
    x_rect = w - 60

    for i, name in enumerate(switch_list, start=1):
        y = y_start + (i - 1) * dy
        switch = canvas.create_text(x_text, y, text=f"{i}. {name}", anchor="w", font=("Bahnschrift SemiBold", 12),
                                    tags=(f"switch_{name}", "switch"), )
        switch_ids[name] = switch
        label = canvas.create_text(x_rect - 30, y + 1, text="0", font=("Bahnschrift SemiBold", 12), )

        rect = canvas.create_rectangle(
            x_rect - 8, y - 8, x_rect + 8, y + 8,
            outline="black", fill="grey"
        )
        switch_text_ids[name] = label
        switch_indicator_ids[name] = rect
        update_switch_indicator(name)


create_switch_table()


def set_diagonal_mode(nameDiag, mode):
    apply_diagonal_mode(nameDiag, mode)
    diagonal_modes[nameDiag] = mode
    update_switch_indicator(nameDiag)


def blink_switches(diags, duration_ms=2000, interval_ms=200):
    if not diags:
        return

    end_time = time.time() + duration_ms / 1000.0
    final_colors = {d: get_switch_state_color(d) for d in diags}

    def _step(state=True):
        if time.time() >= end_time:
            for d in diags:
                rect = switch_indicator_ids.get(d)
                if rect is not None:
                    canvas.itemconfig(rect, fill=final_colors[d])
            return

        for d in diags:
            rect = switch_indicator_ids.get(d)
            if rect is not None:
                canvas.itemconfig(rect, fill="cyan" if state else final_colors[d])
        root.after(interval_ms, _step, not state)

    _step(True)


def blink_switches_table(diags, duration_ms=2000, interval_ms=200):
    if not diags:
        return

    end_time = time.time() + duration_ms / 1000.0
    final_colors = {d: get_switch_state_color(d) for d in diags}

    def _step(state=True):
        if time.time() >= end_time:
            for d in diags:
                rect = switch_indicator_ids.get(d)
                if rect is not None:
                    canvas.itemconfig(rect, fill=final_colors[d])
            return

        for d in diags:
            rect = switch_indicator_ids.get(d)
            if rect is not None:
                canvas.itemconfig(rect, fill="cyan" if state else final_colors[d])
        root.after(interval_ms, _step, not state)

    _step(True)


#########################################        СТРЕЛКИ/ДИАГОНАЛИ               ##############################################

# def AddDiagonal(x1, y1, x2, y2, offsetleft, offsetright, nameDiag):
#     l1 = canvas.create_line(x1, y1, x1 - offsetleft, y1, width=3, fill=line_color_main)
#     l2 = canvas.create_line(x2, y2, x2 + offsetright, y2, width=3, fill=line_color_main)
#     l3 = canvas.create_line(x1, y1, x2, y2, width=3, fill=line_color_main)
#     diag_ids[(nameDiag)] = [l1, l2, l3]

def AddDiagonal(x1, y1, x2, y2, offsetleft, offsetright, nameDiag):
    l1 = canvas.create_line(x1, y1, x1 - offsetleft, y1, width=3, fill=line_color_main)
    l2 = canvas.create_line(x2, y2, x2 + offsetright, y2, width=3, fill=line_color_main)
    l3 = canvas.create_line(x1, y1, x2, y2, width=3, fill=line_color_main)
    diag_ids[(nameDiag)] = [l1, l2, l3]
    canvas.tag_raise(l1)
    canvas.tag_raise(l2)
    canvas.tag_raise(l3)


def AddSplitDiagonal(x1, y1, x2, y2,
                     x3, y3, offset_left,
                     offset_right, nameDiag, namePart1, namePart2):
    l2 = canvas.create_line(x1, y1, x2, y2, width=3, fill=line_color_main)
    l3 = canvas.create_line(x2, y2, x3, y3, width=3, fill=line_color_main)
    l1 = canvas.create_line(x1, y1, x1 - offset_left, y1, width=3, fill=line_color_main)
    l4 = canvas.create_line(x3, y3, x3 + offset_right, y3, width=3, fill=line_color_main)
    split_diag_ids[nameDiag] = {
        'partA': [l1, l2],
        'partB': [l3, l4]
    }
    diag_ids[(namePart1)] = [l1, l2]
    diag_ids[(namePart2)] = [l3, l4]


#########################################       ЛИНИИ              ##############################################
for a, b in segments:
    x1, y1 = positions[a]
    x2, y2 = positions[b]
    seg = canvas.create_line(x1 - 5, y1, x2 + 5, y2, width=6, fill=line_color_main)
    segment_ids[(a, b)] = seg
    segment_ids[(b, a)] = seg

#########################################        ДИАГОНАЛИ/СТРЕЛКИ               ##############################################
AddDiagonal(250, 325, 350, 430, 0, 35, "ALB_Turn2")
AddDiagonal(965, 330, 890, 430, 0, -35, "ALB_Turn1")
AddDiagonal(550, 130, 470, 230, -65, 0, "ALB_Turn8")
AddSplitDiagonal(430, 230, 390, 280, 350, 330, 0, 0, "ALB_Turn4-6", "ALB_Turn4", "ALB_Turn6")

# начальное положение стрелок
set_diagonal_mode("ALB_Turn1", "left")
set_diagonal_mode("ALB_Turn2", "left")
set_diagonal_mode("ALB_Turn8", "left")
set_diagonal_mode("ALB_Turn4-6", "left")


def check_if_route_finished(seg, rev, diag):
    for rid in list(active_routes.keys()):
        data = active_routes[rid]
        segs = data["segments"]
        all_segs = []
        for steps in segs:
            if steps.get("type") == "diag":
                all_segs.append(steps)
            if steps.get("type") == "segment":
                all_segs.append(steps)
        last_all = all_segs[-1]
        if last_all.get("type") == "segment":
            if seg == last_all["id"] or rev == last_all["id"]:
                release_route(rid, by_train=True)
        if last_all.get("type") == "diag":
            if last_all["name"] == diag:
                release_route(rid, by_train=True)
        block = segment_to_block.get(seg)
        if block:
            for s in segment_groups[block]:
                if last_all.get("type") == "segment":
                    if s == last_all["id"] or rev == last_all["id"]:
                        release_route(rid, by_train=True)


part_to_split = {}

for split_name in split_parts_map:
    for part, logic_name in split_parts_map[split_name].items():
        part_to_split[logic_name] = (split_name, part)


def update_all_occupancy():
    # Эта функция только ЧИТАЕТ состояние seg_occ_train и обновляет цвета
    # Само состояние seg_occ_train обновляется в фоновом потоке в esp32_core.py

    for seg in seg_occ_train:
        rev = (seg[1], seg[0])
        if seg_occ_train.get(seg, 1) == 0:

            occupied_segments.discard(seg)
            signal_segment = segment_to_signal.get(seg)
            if signal_segment == None:
                signal_segment = segment_to_signal.get(rev)
            if signal_segment in signals_state:
                for colors in signals_state[signal_segment]["lamps"]:
                    if colors == "red":
                        signals_state[signal_segment]["lamps"][colors]["on"] = True
                    elif colors == "blue":
                        signals_state[signal_segment]["lamps"][colors]["on"] = True
                    else:
                        signals_state[signal_segment]["lamps"][colors]["on"] = False
            occupied_segments.discard(rev)
            check_if_route_finished(seg, rev, diag="")
            block = segment_to_block.get(seg)
            if block is None:
                continue
            segs_in_block = segment_groups[block]
            for s in segs_in_block:
                occupied_segments.discard(s)
                occupied_segments.discard((s[1], s[0]))
    for diag in diag_occ_train:
        if diag_occ_train.get(diag, 1) == 0:
            signal_diag = diag_to_signal.get(diag)
            if signal_diag in signals_state:
                for colors in signals_state[signal_diag]["lamps"]:
                    if colors == "red":
                        signals_state[signal_diag]["lamps"][colors]["on"] = True
                    elif colors == "blue":
                        signals_state[signal_diag]["lamps"][colors]["on"] = True
                    else:
                        signals_state[signal_diag]["lamps"][colors]["on"] = False

            occupied_diagonals.discard(diag)
            check_if_route_finished(seg="", rev="", diag=diag)
    for (a, b), seg_id in segment_ids.items():
        seg = (a, b)
        block = segment_to_block.get(seg)

        if block:
            # если ЛЮБОЙ сегмент блока занят → весь блок красный
            if any(seg_occ_train.get(s, 1) == 0 for s in segment_groups[block]):
                paint_segment(seg, "red")
                continue
            for s in segment_groups[block]:
                if s in occupied_segments:
                    paint_segment(seg, "yellow")
                    continue
            paint_segment(s, line_color_main)

        if seg_occ_train.get((a, b), 1) == 0 or seg_occ_train.get((b, a), 1) == 0:
            paint_segment((a, b), "red")
            continue
        if (a, b) in occupied_segments or (b, a) in occupied_segments:
            paint_segment((a, b), "yellow")
            continue

        paint_segment((a, b), line_color_main)

    for diag_name, lines in diag_ids.items():
        if diag_occ_train.get(diag_name, 1) == 0:
            paint_diagonal(diag_name, "red")
            continue
        if diag_name in occupied_diagonals:
            paint_diagonal(diag_name, "yellow")
            continue
        # 3) свободна -> чёрная
        paint_diagonal(diag_name, line_color_main)
    root.after(100, update_all_occupancy)


#########################################        ПОДСВЕТКА МАРШРУТОВ               ##############################################
def highlight_possible_targets(start):
    possible = set()
    if current_mode == "maneuver":
        for (a, b) in routes.keys():
            if a == start:
                possible.add(b)
    if current_mode == "train":
        for (a, b) in train_routes.keys():
            if a == start:
                possible.add(b)
    for name, item_id in node_ids.items():
        if name == start:
            canvas.itemconfig(item_id, fill="yellow")
            continue

        if name in possible:
            canvas.itemconfig(item_id, fill="green")
            canvas.itemconfig(item_id, state="normal")
        else:
            canvas.itemconfig(item_id, fill="grey")
            canvas.itemconfig(item_id, state="disabled")

    for name in possible:
        canvas.itemconfig(f"node_{name}", state="normal")


def reset_node_selection():
    for name, item_id in node_ids.items():
        canvas.itemconfig(item_id, fill=line_color_main, state="normal")
    selected_nodes.clear()


def disable_all_except_selected():
    for name, item in node_ids.items():
        if name in selected_nodes:
            canvas.itemconfig(item, state="normal")
        else:
            canvas.itemconfig(item, fill="grey", state="disabled")


#########################################        ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ УЗЛОВ      ##############################################
def get_node_name_from_event(event):
    items = canvas.find_withtag("current")
    if not items:
        return None

    item = items[0]
    tags = canvas.gettags(item)

    for t in tags:
        if t.startswith("node_"):
            return t.replace("node_", "")
    return None


def get_switch_name_from_event(event):
    items = canvas.find_withtag("current")
    if not items:
        return None
    item = items[0]
    tags = canvas.gettags(item)
    for t in tags:
        if t.startswith("switch_"):
            return t.replace("switch_", "")
    return None


def on_enter(event):
    name = get_node_name_from_event(event)
    if name is None:
        return

    if name not in selected_nodes:
        canvas.itemconfig(node_ids[name], fill="#e01dcd")


def on_leave(event):
    name = get_node_name_from_event(event)
    if name is None:
        return
    if name not in selected_nodes:
        if len(selected_nodes) == 1:
            canvas.itemconfig(node_ids[name], fill="green")
        else:
            canvas.itemconfig(node_ids[name], fill=line_color_main)


def switch_on_enter(event):
    name = get_switch_name_from_event(event)
    canvas.itemconfig(switch_ids[name], fill="pink")


def switch_on_leave(event):
    name = get_switch_name_from_event(event)
    canvas.itemconfig(switch_ids[name], fill=line_color_main)


#########################################        КОНФЛИКТЫ МАРШРУТОВ ПОСТРОЕНИЕ НОВЫХ               ##############################################
def next_route_id():
    global route_counter
    rid = route_counter
    route_counter += 1
    return rid


global settingRoute
settingRoute = False


def get_route(start, end):
    if current_mode == "maneuver":
        key = (start, end)
        if key in routes:
            return routes[key]
        return None
    if current_mode == "train":
        key = (start, end)
        if key in train_routes:
            return train_routes[key]
        return None


def has_switch_conflict(a, b):
    """
    Проверяет, можно ли построить маршрут a->b или он ломает существующие стрелки.
    """
    key = (a, b)
    if key not in route_switch_modes:
        key = (b, a)
        if key not in route_switch_modes:
            return False  # маршрут не использует стрелки вообще

    needed = route_switch_modes[key]

    # пробегаем по ВСЕМ активным маршрутам
    for rid, data in active_routes.items():

        other_key = (data["start"], data["end"])
        if other_key not in route_switch_modes:
            other_key = (data["end"], data["start"])
            if other_key not in route_switch_modes:
                continue

        other_needed = route_switch_modes[other_key]

        # теперь сравниваем стрелки
        for diag_name, mode_needed in needed.items():

            # если эта стрелка не используется другим маршрутом — всё норм
            if diag_name not in other_needed:
                continue
            other_mode = other_needed[diag_name]
            # если маршруты требуют РАЗНОЕ положение → конфликт
            if other_mode != mode_needed:
                print(f"КОНФЛИКТ: стрелка {diag_name} уже занята маршрутом #{rid}, "
                      f"она стоит в положении {other_mode}, "
                      f"а требуется {mode_needed}")
                return True

    return False


def check_route_conflict(start, end):
    if current_mode == "maneuver":
        if has_switch_conflict(start, end):
            return True
        for step in routes.get((start, end)):
            if step["type"] == "segment":
                a, b = step["id"]
                if step["id"] in occupied_segments or seg_occ_train.get((a, b), 1) == 0 or seg_occ_train.get((b, a),
                                                                                                             1) == 0:
                    return True
            elif step["type"] == "diag":
                if step["name"] in occupied_diagonals or diag_occ_train.get(step["name"], 1) == 0:
                    return True
        return False
    if current_mode == "train":
        if has_switch_conflict(start, end):
            return True
        for step in train_routes.get((start, end)):
            if step["type"] == "segment":
                a, b = step["id"]
                if step["id"] in occupied_segments or seg_occ_train.get((a, b), 1) == 0 or seg_occ_train.get((b, a),
                                                                                                             1) == 0:
                    return True
            elif step["type"] == "diag":
                if step["name"] in occupied_diagonals or diag_occ_train.get(step["name"], 1) == 0:
                    return True
        return False


def register_route(start, end):
    global route_counter
    rid = route_counter
    route_counter += 1

    route_name = f"{start}{end}"
    db_id = sync_add_route(route_name, current_mode)
    print(f"[DB] Маршрут {route_name} ({current_mode}) зарегистрирован, db_id={db_id}")

    if current_mode == "maneuver":
        for step in routes.get((start, end)):
            if step["type"] == "segment":
                a, b = step["id"]
                occupied_segments.add((a, b))
                occupied_segments.add((b, a))
            elif step["type"] == "diag":
                occupied_diagonals.add(step["name"])

        active_routes[rid] = {
            "start": start,
            "end": end,
            "segments": routes.get((start, end)),
            "db_id": db_id,
        }
        return rid
    if current_mode == "train":
        for step in train_routes.get((start, end)):
            if step["type"] == "segment":
                a, b = step["id"]
                occupied_segments.add((a, b))
                occupied_segments.add((b, a))
            elif step["type"] == "diag":
                occupied_diagonals.add(step["name"])

        active_routes[rid] = {
            "start": start,
            "end": end,
            "segments": train_routes.get((start, end)),
            "db_id": db_id,
        }
        return rid


def release_route(route_id, by_train: bool = False):
    """
    by_train=True  → маршрут сброшен детектором (поезд проехал): запись остаётся в БД с passed=True
    by_train=False → ручной снос кнопкой: запись удаляется из БД совсем
    """
    global route_counter
    if route_id not in active_routes:
        return
    data = active_routes[route_id]

    db_id = data.get("db_id")
    if db_id is not None:
        if by_train:
            print(f"[DB] Маршрут #{route_id} пройден поездом, db_id={db_id} → passed=True")
            sync_set_passed(db_id, passed=True)
        else:
            print(f"[DB] Маршрут #{route_id} снесён вручную, db_id={db_id} → удалён")
            sync_delete_route(db_id)

    for step in data["segments"]:
        if step["type"] == "segment":
            a, b = step["id"]
            paint_segment((a, b), line_color_main)

            occupied_segments.discard((a, b))
            occupied_segments.discard((b, a))
        elif step["type"] == "diag":
            paint_diagonal(step["name"], line_color_main)
            occupied_diagonals.discard(step["name"])
    recalc_signals_to_red(route_id)
    del active_routes[route_id]

    comboboxDelete(route_id)


#########################################        МИГАНИЕ МАРШРУТА               ##############################################
def is_segment_in_blinking_route(seg):
    a, b = seg
    for (start, end) in blinking_routes:
        route = routes.get((start, end)) or routes.get((end, start))
        if not route:
            continue
        for step in route:
            if step.get("type") == "segment":
                if step["id"] == (a, b) or step["id"] == (b, a):
                    return True
    return False


def blink_route(start, end, duration_ms=2000, interval_ms=200):
    blinking_routes.add((start, end))
    end_time = time.time() + duration_ms / 1000.0

    def _step(state=True):
        if time.time() >= end_time:
            paint_route(start, end, line_color_main)
            return

        color = "cyan" if state else line_color_main
        paint_route(start, end, color)
        root.after(interval_ms, _step, not state)

    _step(True)


#########################################        ОБРАБОТКА КЛИКА ПО УЗЛУ          ##############################################
def on_node_click(event):
    name = get_node_name_from_event(event)
    if name is None:
        return

    if name in selected_nodes:
        selected_nodes.remove(name)
        canvas.itemconfig(node_ids[name], fill=line_color_main)
        if len(selected_nodes) == 0:
            reset_node_selection()
        if len(selected_nodes) == 1:
            highlight_possible_targets(selected_nodes[0])
        return

    if len(selected_nodes) >= MAX_SELECTED:
        return

    selected_nodes.append(name)
    canvas.itemconfig(node_ids[name], fill="cyan")

    if len(selected_nodes) == 1:
        highlight_possible_targets(name)
    if len(selected_nodes) == 2:
        first = selected_nodes[0]
        second = selected_nodes[1]
        disable_all_except_selected()
        on_two_nodes_selected(first, second)


def blink_diag(name, duration_ms=2000, interval_ms=200):
    blinking_diags.add(name)
    end_time = time.time() + duration_ms / 1000.0

    def _step(state=True):
        if time.time() >= end_time:
            if name in split_diag_ids:
                for split_name in split_diag_ids.keys():
                    for part_name, lines in split_diag_ids[split_name].items():
                        logic_name = split_parts_map[split_name][part_name]
                        if logic_name in diag_ids:
                            paint_diagonal(logic_name, line_color_main)
            else:
                paint_diagonal(name, line_color_main)
            return

        color = "cyan" if state else line_color_main
        if name in split_diag_ids:
            for split_name in split_diag_ids.keys():
                for part_name, lines in split_diag_ids[split_name].items():
                    logic_name = split_parts_map[split_name][part_name]
                    if logic_name in diag_ids:
                        paint_diagonal(logic_name, color)
        else:
            paint_diagonal(name, color)
            root.after(interval_ms, _step, not state)

    _step(True)


global changingSwitches
changingSwitches = False


def on_switch_mode_selected(name, mode):
    text = canvas.itemcget(switch_text_ids[name], "text")
    global settingRoute
    global changingSwitches
    if changingSwitches:
        showInfo("Ошибка", "Одна из стерок меняется!")
        return
    if diag_occ_train.get(name, 1) == 0:
        showInfo("Ошибка", "Стрелка занята!")
        return

    textALB4_6 = canvas.itemcget(switch_text_ids["ALB_Turn4-6"], "text")
    ALB_Turn1banned = [("M8mid", "M8"), ("M8", "M8_mid")]
    ALB_Turn8banned = [("M6", "M6H2"), ("H2", "M6H2"), ("M6H2", "M6"), ("M6H2", "H2")]
    ALB_Turn4_6banned = [("M2", "M2H1_mid"), ("M2H1_mid", "M2"), ("M6", "M6H2"), ("M6H2", "M6"), ("H2", "M6H2"),
                         ("M6H2", "H2")]
    ALB_Turn2banned = [("M2", "M2H1_mid"), ("M2H1_mid", "M2")]

    for num in active_routes:
        for step in active_routes[num]["segments"]:
            if name == 'ALB_Turn1':
                if step["type"] == "segment":
                    if step["id"] in ALB_Turn1banned:
                        showInfo("Ошибка", "Стрелка на готовом маршруте!")
                        return
            if name == 'ALB_Turn2':
                if step["type"] == "segment":
                    if step["id"] in ALB_Turn2banned:
                        showInfo("Ошибка", "Стрелка на готовом маршруте!")
                        return
            if name == "ALB_Turn8":
                if step["type"] == "segment":
                    if step["id"] in ALB_Turn8banned:
                        showInfo("Ошибка", "Стрелка на готовом маршруте!")
                        return
            if name == "ALB_Turn4-6":
                if step["type"] == "segment":
                    if step["id"] in ALB_Turn4_6banned:
                        showInfo("Ошибка", "Стрелка на готовом маршруте!")
                        return
            if step["type"] == "diag":
                if step["name"] == name:
                    showInfo("Ошибка", "Используемая стрелка!")
                    return
    if settingRoute:
        showInfo("Ошибка", "Невозможно сменить стрелку")
        return
    changingSwitches = True
    blink_diag(name, duration_ms=2000, interval_ms=200)
    blink_switches([name], duration_ms=2000, interval_ms=200)

    def finalize():
        global changingSwitches
        if mode == 0 and text != "+":
            if name == "ALB_Turn2" and textALB4_6 == "+":
                canvas.itemconfig(segment_ids[("H1", "M2H1_third")], width=6)
            set_diagonal_mode(name, "left")
            changingSwitches = False
        elif mode == 1 and text != "-":
            set_diagonal_mode(name, "right")
            changingSwitches = False
        else:
            changingSwitches = False
            return

    root.after(2100, finalize)

menus = []

def on_switch_click(event):
    name = get_switch_name_from_event(event)
    menu = tk.Menu(root, tearoff=0, foreground="white")
    menu.add_command(
        label="-",
        command=lambda: on_switch_mode_selected(name, 1)
    )
    menu.add_command(
        label="+",
        command=lambda: on_switch_mode_selected(name, 0)
    )
    menu.tk_popup(event.x_root, event.y_root)
    menus.append(menu)


#########################################        ФУНКЦИЯ ПРИ ВЫБОРЕ ДВУХ ТОЧЕК   ##############################################
def visualSwitch(key):
    list = [("M2", "H1"), ("M2", "M8"), ("M2", "M1"), ("H1", "M2"), ("M1", "M2")]
    needRoutes = [('H1', 'M2H1_third')]
    if key in list:
        canvas.itemconfig(segment_ids[needRoutes[0]], width=6)


def on_two_nodes_selected(a, b):
    global last_switch_check
    global settingRoute

    if check_route_conflict(a, b):
        print("Маршрут конфликтует с уже установленными!")
        reset_node_selection()
        return
    if settingRoute == True:
        reset_node_selection()
        return
    # 2. Ищем настройки стрелок для этого маршрута
    key = (a, b)
    if key not in route_switch_modes:
        key = (b, a)
    if key not in route_switch_modes:
        print("Для этого маршрута нет настроек стрелок")
        reset_node_selection()
        return

    route_cfg = route_switch_modes[key]  # только нужные стрелки для ЭТОГО маршрута
    last_switch_check = {}
    changed = []
    main_diag = None  # какая стрелка будет мигать в табличке

    for diag_name, need_mode in route_cfg.items():
        current_mode = diagonal_modes.get(diag_name)
        ok = (current_mode == need_mode)
        last_switch_check[diag_name] = {
            "needed": need_mode,
            "current": current_mode,
            "ok": ok,
        }
        if not ok:
            if main_diag is None:
                main_diag = diag_name
            set_diagonal_mode(diag_name, need_mode)
            changed.append(f"{diag_name}: {current_mode} -> {need_mode}")

    if main_diag is None and route_cfg:
        main_diag = next(iter(route_cfg.keys()))
    settingRoute = True
    paint_route(a, b, "cyan")
    blink_route(a, b, duration_ms=2000, interval_ms=150)

    if main_diag is not None:
        blink_switches([main_diag], duration_ms=2000, interval_ms=150)
    if changed:
        print("Изменены стрелки:")
        for line in changed:
            print("  ", line)

    reset_node_selection()
    visualSwitch(key)

    def finalize():
        rid = register_route(a, b)
        paint_route(a, b, "yellow")
        global settingRoute
        settingRoute = False
        current_values = list(combobox1["values"])
        current_values.append(rid)
        combobox1["values"] = tuple(current_values)
        recalc_signals_from_active_routes((a, b))

    root.after(2100, finalize)


def comboboxDelete(ids):
    options = list(combobox1['values'])
    options.remove(str(ids))
    combobox1["values"] = options


def snos():
    selected_item = combobox1.get()
    if selected_item == "":
        return
    num = int(selected_item)
    release_route(num)
    combobox1.set('')


def snosAll():
    for active in list(active_routes.keys()):
        release_route(active)
    combobox1.set('')


def check():
    print("Активные маршруты")
    print(active_routes)


# === УДАЛЕНА ИНИЦИАЛИЗАЦИЯ ARDUINO И POLL ===
# Вместо этого работает фоновый поток в esp32_core.py, который сам слушает сокет
# и обновляет seg_occ_train (который мы импортировали из logic)


#########################################        ТУПИКИ               ##############################################
drawDeadEnd("pastM1", "right", 0)
drawDeadEnd("past2", "right", 0)
drawDeadEnd("past4", "right", 0)
drawDeadEnd("beforeM6", "left", 0)

#########################################        СТАНЦИИ(КРУГИ/ТЕКСТ)             ##############################################
bannedNames = ["pastM1", "beforeM6", "past2", "1STR", "past4", "M6H2", "M2H1_mid",
               "M8mid", "M2H1_third", "ALB_Sect1-2", "ALB_Sect1", "ALB_Sect2",
               "ALB_Sect0", "ALB_Sect1-2_2"]

for name, (x, y) in positions.items():
    if name in bannedNames:
        continue
    node = canvas.create_text(x, y - 25, text=name, tags=(f"node_{name}", "node"), fill=line_color_main,
                              font=("Bahnschrift SemiBold", 12))
    node_ids[name] = node

#########################################        РИСУЕМ ВСЕ СВЕТОФОРЫ            ##############################################
for name, cfg in signals_config.items():
    drawSignal(
        name,
        cfg["mount"],
        cfg["pack_side"],
        cfg["count"],
        cfg.get("colors")
    )


#########################################        ВИЗУАЛ РЕЖИМА                    ##############################################
def apply_mode_visuals():
    for name, item_id in node_ids.items():
        color = "black"
        state = "normal"
        if current_mode == "maneuver" and name == "CH":
            color = "grey"
            state = "disabled"
        canvas.itemconfig(item_id, fill=color, state=state)


def set_mode(mode):
    global current_mode
    current_mode = mode

    if btn_maneuver is not None and btn_train is not None:
        if mode == "maneuver":
            btn_maneuver.config(bg="#3996D5", fg="white")
            btn_train.config(bg="grey", fg="white")
        else:
            btn_train.config(bg="#3996D5", fg="white")
            btn_maneuver.config(bg="grey", fg="white")

    selected_nodes.clear()
    apply_mode_visuals()


def on_CH_click(event):
    name = get_node_name_from_event(event)
    if name == "CH":
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(
            label="Пригласительный",
            command=lambda: invite_signal_on_off(name)
        )
        menu.tk_popup(event.x_root, event.y_root)


#########################################        БИНДЫ И НАЧАЛЬНАЯ ОКРАСКА        ##############################################
canvas.tag_bind("node", "<Button-1>", on_node_click)
canvas.tag_bind("node", "<Enter>", on_enter)
canvas.tag_bind("node", "<Leave>", on_leave)

canvas.tag_bind("node", "<Button-3>", on_CH_click)
canvas.tag_bind("switch", "<Button-1>", on_switch_click)
canvas.tag_bind("switch", "<Enter>", switch_on_enter)
canvas.tag_bind("switch", "<Leave>", switch_on_leave)

n = tkinter.StringVar()
combobox1 = ttk.Combobox(root, width=25, textvariable=n, state='readonly', font=("Bahnschrift bold", 9))
combobox1.place(x=510, y=20)

button = tkinter.Button(root, text="Снести", command=snos, relief="flat", bg="#D50063", fg="white",
                        font=("Bahnschrift", 10), )
button.place(x=720, y=18)
buttonAll = tkinter.Button(root, text="Убрать всё", command=snosAll, relief="flat", bg="#D50063", fg="white",
                           font=("Bahnschrift", 10))
buttonAll.place(x=790, y=18)
button = tkinter.Button(root, text="Проверка", command=check, relief="flat", bg="#D50063", fg="white",
                        font=("Bahnschrift", 10))
button.place(x=880, y=18)

# ========== КНОПКА ПРИГЛАСИТЕЛЬНОГО CH ==========
_ch_invite_active = False

def toggle_ch_invite():
    global _ch_invite_active
    _ch_invite_active = not _ch_invite_active
    if _ch_invite_active:
        btn_ch_invite.config(bg="#FF8C00", text="ПРИГЛАСИТЕЛЬный CH ВКЛ")

        def on_invite_expired():
            global _ch_invite_active
            _ch_invite_active = False
            btn_ch_invite.config(bg="#555555", text="Пригласительный CH")

        start_invite_mode(on_expired=on_invite_expired)
    else:
        btn_ch_invite.config(bg="#555555", text="Пригласительный CH")
        import esp8266
        esp8266.invite_until = 0.0          # сбрасываем таймер
        esp8266._on_invite_expired = None   # отменяем колбэк
        esp8266.send_invite_to_esp8266(False)  # ESP гасит пригласительный
        recalc_signal_aspects()

btn_ch_invite = tkinter.Button(
    root,
    text="Пригласительный CH",
    command=toggle_ch_invite,
    relief="flat",
    bg="#555555",
    fg="white",
    font=("Bahnschrift", 10),
)
btn_ch_invite.place(x=960, y=18)
# =================================================

buttons_y = CANVAS_H - 80

btn_maneuver = tkinter.Button(
    root,
    text="МАНЕВРОВЫЕ",
    font=("Bahnschrift", 15),
    bg="#3996D5",
    fg="white",
    width=15,
    height=2,
    relief="flat",
    command=show_maneuver_routes
)
btn_train = tkinter.Button(
    root,
    text="ПОЕЗДНЫЕ",
    font=("Bahnschrift", 15),
    bg="grey",
    fg="white",
    relief="flat",
    width=15,
    height=2,
    command=show_train_routes
)

center_x = CANVAS_W // 2
offset = 140

btn_maneuver.place(x=center_x + offset - 70, y=buttons_y)
btn_train.place(x=center_x - offset - 60, y=buttons_y)


def do(button_id):
    if 0 <= button_id < len(SEGMENT_ORDER):
        seg = SEGMENT_ORDER[button_id]
        seg_occ_train[seg] = 1 if seg_occ_train[seg] == 0 else 0
    else:
        keys = list(diag_occ_train.keys())
        seg = keys[button_id - len(SEGMENT_ORDER)]
        diag_occ_train[seg] = 1 if diag_occ_train[seg] == 0 else 0

buttons = []

for i in range(17):
    button69 = tkinter.Button(root, text=f"{[i]}", command=lambda id=i: do(id), relief="flat")
    buttons.append(button69)
    button69.place(x=1220, y=50 + i * 25)

# Переменная для отслеживания состояния тестового режима
test_mode_active = True  # по умолчанию кнопки видны


def toggle_test_mode():
    """
    Переключает тестовый режим: скрывает/показывает кнопки детекторов занятости
    и таблицу стрелок (текст + индикаторы на canvas).
    """
    global test_mode_active
    test_mode_active = not test_mode_active

    canvas_state = "normal" if test_mode_active else "hidden"

    if test_mode_active:
        # Показываем кнопки занятости
        for i, btn in enumerate(buttons):
            btn.place(x=1220, y=40 + i * 25)
        test_mode_button.config(text="Выкл. тест. режим", bg="#D50063", fg="white")
    else:
        # Скрываем кнопки занятости
        for btn in buttons:
            btn.place_forget()
        test_mode_button.config(text="Вкл. тест. режим", bg="#555555", fg="white")

    # Скрываем/показываем таблицу стрелок на canvas
    for name in switch_list:
        if name in switch_ids:
            canvas.itemconfig(switch_ids[name], state=canvas_state)
        if name in switch_text_ids:
            canvas.itemconfig(switch_text_ids[name], state=canvas_state)
        if name in switch_indicator_ids:
            canvas.itemconfig(switch_indicator_ids[name], state=canvas_state)


# Кнопка переключения тестового режима — через place, как и все остальные
test_mode_button = tk.Button(
    root,
    text="Выкл. тест. режим",
    command=toggle_test_mode,
    relief="flat",
    bg="#D50063",
    fg="white",
    font=("Bahnschrift", 10),
)
test_mode_button.place(x=1110, y=18)

#########################################        ЗАПУСК ЦИКЛОВ                    ##############################################

canvas.create_text(80, 80, text="Станция Алгабас", anchor="nw", font=("Arial", 30), fill="black")

# Подключаемся к ESP32 при старте
# Запускаем обновление GUI (отрисовка)
update_all_occupancy()
update_signals_visual_v2()
recalc_signals_to_red(None)

root.protocol('WM_DELETE_WINDOW', quit_function)
canvas.pack()

root.mainloop()