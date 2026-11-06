"""Таймер автосброса: один asyncio-таймер на комнату, отсчёт ведёт сервер.

Реестр живёт в памяти процесса: при перезапуске сервера таймер теряется
(его заново запускает первое подключение агая).
"""
import asyncio
import time

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer

from . import services

_timers = {}  # code -> (task, deadline_epoch, round)


def group_name(code):
    return f"room_{code}"


def deadline(code):
    entry = _timers.get(code)
    return entry[1] if entry else None


def state_for(room):
    """Состояние комнаты + момент автосброса и серверное время для отсчёта."""
    state = services.room_state(room)
    state["deadline"] = deadline(room.code) if state["status"] == "active" else None
    state["now"] = time.time()
    return state


def cancel(code):
    entry = _timers.pop(code, None)
    if entry and entry[0] is not asyncio.current_task():
        try:
            entry[0].cancel()
        except RuntimeError:  # цикл событий уже закрыт (тесты)
            pass


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
    # сброс только если раунд не сменился вручную за это время
    room = await database_sync_to_async(services.timer_reset)(code, round_no)
    if room is None:
        return
    schedule(code, room.timer_seconds, room.round)
    await broadcast_state(code)


async def broadcast_state(code):
    room = await database_sync_to_async(services.open_room)(code)
    if room is None:
        return
    state = await database_sync_to_async(state_for)(room)
    await get_channel_layer().group_send(group_name(code), {"type": "room.state", "state": state})


def clear_all():
    for code in list(_timers):
        cancel(code)
