"""Кто «в сети»: счётчик открытых сокетов на студента + фоновая проверка по last_seen.

Почему счётчик: при F5 новый сокет подключается раньше, чем закрывается старый.
Раньше disconnect старого вслепую ставил is_online=False — и студент на открытой
вкладке выглядел «не в сети». Теперь False ставится, только когда сокетов не осталось.

Почему last_seen: если вкладка «умерла» без закрытия сокета (сон ноутбука, обрыв сети),
счётчик не уменьшится — такого студента выдаёт отсутствие ping дольше 40 с.
Раз в 15 с на комнату пересчитываем онлайн и рассылаем state, если что-то изменилось.
Всё в памяти процесса — как и InMemoryChannelLayer (один сервер).
"""
import asyncio
from collections import defaultdict

from channels.db import database_sync_to_async

from . import services, timers

SWEEP_SECONDS = 15

_student_sockets = defaultdict(int)  # student_id -> открытых сокетов
_room_sockets = defaultdict(int)  # code -> открытых сокетов любой роли
_watchers = {}  # code -> asyncio.Task


async def student_connected(student_id):
    _student_sockets[student_id] += 1
    await database_sync_to_async(services.set_online)(student_id, True)


async def student_disconnected(student_id):
    """Уменьшает счётчик. True — сокетов не осталось, студент ушёл."""
    _student_sockets[student_id] -= 1
    if _student_sockets[student_id] > 0:
        return False
    _student_sockets.pop(student_id, None)
    await database_sync_to_async(services.set_online)(student_id, False)
    return True


def forget_student(student_id):
    """Студента удалили — его сокеты больше не считаем."""
    _student_sockets.pop(student_id, None)


def socket_count(student_id):
    return _student_sockets.get(student_id, 0)


def room_opened(code):
    _room_sockets[code] += 1
    if code not in _watchers:
        _watchers[code] = asyncio.get_running_loop().create_task(_watch(code))


def room_closed(code):
    _room_sockets[code] -= 1
    if _room_sockets[code] <= 0:
        _room_sockets.pop(code, None)
        task = _watchers.pop(code, None)
        if task and task is not asyncio.current_task():
            task.cancel()


async def _watch(code):
    last = await database_sync_to_async(services.online_ids)(code)
    try:
        while last is not None:
            await asyncio.sleep(SWEEP_SECONDS)
            now = await database_sync_to_async(services.online_ids)(code)
            if now is not None and now != last:
                await timers.broadcast_state(code)
            last = now
    except asyncio.CancelledError:
        pass
    finally:
        if _watchers.get(code) is asyncio.current_task():
            _watchers.pop(code, None)


def clear_all():
    for task in list(_watchers.values()):
        try:
            task.cancel()
        except RuntimeError:
            pass
    _watchers.clear()
    _student_sockets.clear()
    _room_sockets.clear()
