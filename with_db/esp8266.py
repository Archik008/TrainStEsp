import socket
import threading
import time
from typing import Dict, List
from configs import *

# Импортируем переменные состояния из logic
from db import sync_set_passed

# ===================== НАСТРОЙКИ СЕТИ =====================
ESP8266_IP = "192.168.43.113"  # IP ESP8266
ESP8266_PORT = 80

sock_esp8266 = None
stop_threads = False


# ===================== УПРАВЛЕНИЕ ПОДКЛЮЧЕНИЯМИ =====================

def connect_sockets():
    """Подключается к ESP8266"""
    return
    global sock_esp8266

    if sock_esp8266 is None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect((ESP8266_IP, ESP8266_PORT))
            sock_esp8266 = s
            print(f"[NET] Подключено к ESP8266 (CH) ({ESP8266_IP}:{ESP8266_PORT})")
            
            # Запускаем слушатель кнопок для ESP8266
            t = threading.Thread(target=listen_esp8266_buttons_loop, daemon=True)
            t.start()
            print("[NET] Поток прослушивания ESP8266 запущен")
        except Exception as e:
            print(f"[NET] Ошибка подключения к ESP8266: {e}")
            sock_esp8266 = None


def send_bytes(target_sock, data: bytes, device_name="ESP8266"):
    """Универсальная отправка на ESP8266"""
    if target_sock:
        try:
            target_sock.sendall(data)
        except Exception as e:
            print(f"[NET] Ошибка отправки на {device_name}: {e}")
            global sock_esp8266
            if target_sock == sock_esp8266:
                sock_esp8266.close()
                sock_esp8266 = None
            connect_sockets()
    else:
        connect_sockets()


# ===================== ЛОГИКА КНОПОК (ESP8266) =====================

def listen_esp8266_buttons_loop():
    """Слушаем кнопки от ESP8266"""
    global sock_esp8266
    buffer = b""
    while not stop_threads:
        if sock_esp8266 is None:
            time.sleep(1)
            continue

        try:
            chunk = sock_esp8266.recv(32)
            if not chunk:
                sock_esp8266.close()
                sock_esp8266 = None
                continue

            buffer += chunk
            while len(buffer) >= 3:
                if buffer[0] != 66:  # 'B'
                    buffer = buffer[1:]
                    continue
                btn_idx = buffer[1]
                btn_state = buffer[2]
                buffer = buffer[3:]

                print(f"[ESP8266] Кнопка {btn_idx}: {'нажата' if btn_state == 1 else 'отпущена'}")
                
                # Здесь добавь свою логику обработки кнопок ESP8266
                # Например, аналогично ESP32:
                if btn_idx < len(SEGMENT_ORDER):
                    seg_name = SEGMENT_ORDER[btn_idx]
                    occ = 0 if btn_state == 1 else 1
                    if seg_occ_train.get(seg_name) != occ:
                        seg_occ_train[seg_name] = occ
                        print(f"[BTN-8266] {seg_name} -> {occ}")

        except socket.timeout:
            continue
        except Exception as e:
            print(f"[ESP8266] Ошибка приёма: {e}")
            sock_esp8266 = None
# ===================== ЛОГИКА СВЕТОФОРОВ =====================

# Маски битов H2
H2_MASKS = {
    "red":        0x01,
    "green":      0x02,
    "yellow":     0x04,
    "white":      0x08,
    "two_yellow": 0x04,
    "one_yellow": 0x04,
}
# Маски битов M2
M2_MASKS = {"blue": 0, "white": 1}

# Маски битов CH (для ESP8266)
# Соответствие Arduino коду:
# Bit 0=W, 1=Y, 2=R, 3=G, 4=Y1
CH_BIT_MAP = {
    "white":   0x01,  # бит 0
    "yellow":  0x02,  # верхний
    "red":     0x04,
    "green":   0x08,
    "yellow2": 0x10,  # нижний
}
invite_until = 0.0
_on_invite_expired = None  # опциональный колбэк — вызывается когда 60 сек истекли


def start_invite_mode(on_expired=None):
    global invite_until, _on_invite_expired
    invite_until = time.time() + 60.0
    if on_expired is not None:
        _on_invite_expired = on_expired
    send_invite_to_esp8266(True)   # ESP8266 начинает мигать сам
    recalc_signal_aspects()


def is_route_occupied_for_CH(dest: str | None) -> bool:
    if dest is None: return False
    segs = ROUTE_PROTECT_SEGMENTS_FOR_CH.get(dest, [])
    for a, b in segs:
        if seg_occ_train.get((a, b), 1) == 0: return True
        if seg_occ_train.get((b, a), 1) == 0: return True
    return False


ch_route_passed = {"1": False, "2": False, "3": False, "4": False}


def get_current_ch_aspect() -> str:
    global _on_invite_expired
    dest = find_active_entry_route_from_CH()

    if time.time() < invite_until:
        return "invite"
    else:
        # Таймер истёк — если был колбэк, вызываем его один раз
        if _on_invite_expired is not None:
            cb = _on_invite_expired
            _on_invite_expired = None
            try:
                send_invite_to_esp8266(False)  # ESP8266 возвращается к обычному режиму
                cb()
            except Exception:
                pass

    if dest is None:
        return "red"

    if dest in ch_route_passed:
        if is_route_occupied_for_CH(dest):
            ch_route_passed[dest] = True
        if ch_route_passed[dest]:
            return "red"

    # ← ВОТ ЗДЕСЬ: логика по направлению
    if dest == "1":
        return "one_yellow"  # один жёлтый → путь 1
    else:
        return "two_yellow"  # два жёлтых → пути 2, 3, 4


def is_maneuver_route_active() -> bool:
    for rid, data in active_routes.items():
        if "M2" == data["start"] or "M2" == data["end"]:
            return True
    return False


def recalc_signal_aspects():
    """Вычисляет состояния всех сигналов и рассылает на ESP8266"""

    # 1. Рассчитываем аспект CH
    ch_aspect_name = get_current_ch_aspect()

    # Обновляем логическое состояние для GUI (signals_state)
    # Это нужно, чтобы на экране компьютера тоже менялись цвета
    update_gui_state_ch(ch_aspect_name)

    # 2. ОТПРАВКА НА ESP8266 (CH)
    send_ch_to_esp8266(ch_aspect_name)

    # 3. ОТПРАВКА НА ESP8266 (M2, H2)
    send_h2_m2_to_esp8266(ch_aspect_name)


def update_gui_state_ch(aspect_name):
    """Синхронизирует signals_state['CH'] с текущим аспектом"""
    if "CH" not in signals_state: return

    # Сброс всех ламп CH
    for k in signals_state["CH"]["lamps"]:
        signals_state["CH"]["lamps"][k]["on"] = False
        signals_state["CH"]["lamps"][k]["blink"] = False

    if aspect_name == "invite":
        signals_state["CH"]["lamps"]["red"]["on"] = True
        signals_state["CH"]["lamps"]["white"]["on"] = True
        signals_state["CH"]["lamps"]["white"]["blink"] = True
    elif aspect_name == "two_yellow":
        signals_state["CH"]["lamps"]["yellow"]["on"] = True
    elif aspect_name == "one_yellow":
        signals_state["CH"]["lamps"]["yellow"]["on"] = True
    elif aspect_name == "green":
        signals_state["CH"]["lamps"]["green"]["on"] = True
    elif aspect_name == "red":
        signals_state["CH"]["lamps"]["red"]["on"] = True


def send_invite_to_esp8266(enable: bool):
    """
    Отправляет команду пригласительного напрямую на ESP8266.
    ESP8266 сам управляет миганием белого + красного 60 сек.
    Формат: ['I', 0x01] — включить, ['I', 0x00] — выключить.
    """
    flag = 0x01 if enable else 0x00
    msg = bytes([ord('I'), flag])
    send_bytes(sock_esp8266, msg, "ESP8266(CH-Invite)")


def send_ch_to_esp8266(aspect_name):
    # Пригласительный — управляется командой 'I', не маской 'L'
    # ESP8266 сам мигает; здесь просто ничего не шлём
    if aspect_name == "invite":
        return

    mask = 0

    if aspect_name == "two_yellow":
        mask |= CH_BIT_MAP["yellow"]
        mask |= CH_BIT_MAP["yellow2"]

    elif aspect_name in ("one_yellow", "yellow"):
        mask |= CH_BIT_MAP["yellow"]

    elif aspect_name == "green":
        mask |= CH_BIT_MAP["green"]

    else:
        mask |= CH_BIT_MAP["red"]

    msg = bytes([ord('L'), 3, mask])
    send_bytes(sock_esp8266, msg, "ESP8266(CH)")



def send_h2_m2_to_esp8266(ch_aspect_name):
    """Отправка сигналов H2 и M2 на ESP8266"""

    # --- H2 ---
    mask_h2 = 0

    if ch_aspect_name == "invite":
        mask_h2 |= H2_MASKS["red"]
        if signal_blink_phase:
            mask_h2 |= H2_MASKS["white"]

    elif ch_aspect_name == "two_yellow":
        mask_h2 |= H2_MASKS["yellow"]

    elif ch_aspect_name == "one_yellow":
        mask_h2 |= H2_MASKS["green"]

    elif ch_aspect_name == "green":
        mask_h2 |= H2_MASKS["green"]

    else:
        mask_h2 |= H2_MASKS["red"]

    # УБРАТЬ если H2 не через инвертор:
    mask_h2 ^= 0x0F

    send_bytes(sock_esp8266, bytes([ord('L'), 2, mask_h2]), "ESP8266(H2)")

    # --- M2 ---
    if is_maneuver_route_active():
        mask_m2 = M2_MASKS["white"]
    else:
        mask_m2 = M2_MASKS["blue"]

    send_bytes(sock_esp8266, bytes([ord('L'), 1, mask_m2]), "ESP8266(M2)")



# ===================== СЕРВОПРИВОДЫ (ESP8266) =====================

def send_servo_for_switch(nameDiag: str, mode: str):
    """Отправка команд серво на ESP8266"""
    if mode not in ("left", "right"): return
    cmds: List[str] = []

    if nameDiag == "ALB_Turn1":
        cmds.append('A' if mode == "left" else 'a')
    elif nameDiag == "ALB_Turn2-4":
        cmds.append('D' if mode == "left" else 'd')
    elif nameDiag == "ALB_Turn6-8":
        cmds.append('B' if mode == "left" else 'b')
    elif nameDiag == "ALB_Turn4-6":
        cmds.append('C' if mode == "left" else 'c')

    for c in cmds:
        send_bytes(sock_esp8266, c.encode('ascii'), "ESP8266(Servo)")


# ===================== УТИЛИТЫ =====================

def find_active_entry_route_from_CH():
    for rid, data in active_routes.items():
        a, b = data["start"], data["end"]
        if (a == "CH" and b in ("1", "2", "3", "4")) or \
                (b == "CH" and a in ("1", "2", "3", "4")):
            return b if a == "CH" else a
    return None


# Старт при импорте
connect_sockets()