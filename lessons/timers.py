"""Фоновые отсчёты комнаты: таймер автосброса и авто-новый раунд, когда записали все.

Оба — asyncio-задачи (не больше одной каждого вида на комнату), отсчёт ведёт сервер,
поэтому работают, даже если панель агая закрыта. Раунд меняется только условно
(services.reset с expected_round) — таймер, авто-раунд и ручной «Сбросить» не дают двойного сброса.

Реестр живёт в памяти процесса: при перезапуске сервера таймер теряется
(его заново запускает первое подключение агая), авто-раунд — пересчитывается
при первой же рассылке состояния.
"""
import asyncio
import time

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from . import services

_timers = {}  # code -> (task, deadline_epoch, round)
_auto = {}  # code -> (task, at_epoch, round) — отсчёт до авто-нового раунда
_held = {}  # code -> round, в котором агай нажал «Подождать»


def group_name(code):
    return f"room_{code}"


def auto_next_seconds():
    return getattr(settings, "AUTO_NEXT_SECONDS", 3)


def deadline(code):
    entry = _timers.get(code)
    return entry[1] if entry else None


def auto_next_at(code, round_no):
    entry = _auto.get(code)
    return entry[1] if entry and entry[2] == round_no else None


def state_for(room):
    """Состояние комнаты + моменты автосброса / авто-раунда и серверное время для отсчёта."""
    state = services.room_state(room)
    active = state["status"] == "active"
    state["deadline"] = deadline(room.code) if active else None
    state["auto_next_at"] = auto_next_at(room.code, state["round"]) if active else None
    state["auto_held"] = active and _held.get(room.code) == state["round"]
    state["now"] = time.time()
    return state


def _cancel_task(registry, code):
    entry = registry.pop(code, None)
    if not entry:
        return
    try:
        if entry[0] is not asyncio.current_task():
            entry[0].cancel()
    except RuntimeError:  # вне цикла событий / цикл уже закрыт (тесты)
        pass


def cancel(code):
    _cancel_task(_timers, code)


def cancel_auto(code):
    _cancel_task(_auto, code)


def schedule(code, seconds, round_no):
    """Запускает (или перезапускает) таймер комнаты. Вызывать из event loop."""
    cancel(code)
    if not seconds:
        return
    task = asyncio.get_running_loop().create_task(_run(code, seconds, round_no))
    _timers[code] = (task, time.time() + seconds, round_no)


def sync_with_room(code, status, timer_seconds, round_no, restart=True):
    """Приводит таймер в соответствие с комнатой: active + timer → идёт, иначе выключен."""
    if status == "active" and timer_seconds:
        if restart or code not in _timers:
            schedule(code, timer_seconds, round_no)
    else:
        cancel(code)


async def _run(code, seconds, round_no):
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return
    entry = _timers.get(code)
    if entry and entry[0] is asyncio.current_task():
        _timers.pop(code, None)
    # сброс только если раунд не сменился вручную за это время; таймер при этом выключается
    room = await database_sync_to_async(services.timer_reset)(code, round_no)
    if room is None:
        return
    await broadcast_state(code)


# --- авто-новый раунд -------------------------------------------------------


def hold_auto(code, round_no):
    """«Подождать»: в этом раунде авто-новый раунд не начнётся, агай сбросит сам."""
    _held[code] = round_no
    cancel_auto(code)


def sync_auto(code, state):
    """Запускает/отменяет отсчёт по свежему состоянию. Вызывать из event loop.

    Все записали (marked == total > 0) и агай не нажал «Подождать» → отсчёт идёт
    (уже идущий для этого раунда не перезапускаем). Иначе — отменяем: кто-то нажал
    «Отменить», вошёл новый ученик, раунд сменился, урок закончился.
    """
    round_no = state["round"]
    if _held.get(code) not in (None, round_no):
        _held.pop(code, None)  # «Подождать» действует только на свой раунд
    want = (
        state["status"] == "active"
        and state["total"] > 0
        and state["marked"] == state["total"]
        and _held.get(code) != round_no
    )
    entry = _auto.get(code)
    if not want:
        cancel_auto(code)
    elif not (entry and entry[2] == round_no):
        cancel_auto(code)
        seconds = auto_next_seconds()
        task = asyncio.get_running_loop().create_task(_auto_run(code, seconds, round_no))
        _auto[code] = (task, time.time() + seconds, round_no)
    state["auto_next_at"] = auto_next_at(code, round_no) if want else None
    state["auto_held"] = state["status"] == "active" and _held.get(code) == round_no


async def _auto_run(code, seconds, round_no):
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return
    entry = _auto.get(code)
    if entry and entry[0] is asyncio.current_task():
        _auto.pop(code, None)
    # сервер ещё раз проверяет «записали все» — например, ученик вошёл по HTTP, но сокет ещё не открыл
    room = await database_sync_to_async(services.auto_next)(code, round_no)
    if room is not None:
        cancel(code)  # таймер был на этот раунд — выключен вместе с ним
    await broadcast_state(code)


# --- рассылка ---------------------------------------------------------------


async def broadcast_state(code):
    room = await database_sync_to_async(services.open_room)(code)
    if room is None:
        cancel_auto(code)
        return
    state = await database_sync_to_async(state_for)(room)
    sync_auto(code, state)
    state["now"] = time.time()  # после планирования: иначе «через 3» в первый тик покажется как «через 4»
    await get_channel_layer().group_send(group_name(code), {"type": "room.state", "state": state})


def clear_all():
    for code in list(_timers):
        cancel(code)
    for code in list(_auto):
        cancel_auto(code)
    _held.clear()
