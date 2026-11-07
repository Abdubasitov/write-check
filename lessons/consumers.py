"""RoomConsumer: /ws/rooms/<code>/teacher/ и /ws/rooms/<code>/student/.

Роль задаёт путь (то есть страница), а не «что нашлось в cookies»:
teacher-сокет авторизуется только сессией агая, student-сокет — только cookie студента.
Вся логика — в services, timers и presence.
"""
import hmac

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from . import presence, services, timers
from .models import Student
from .views import STUDENT_TOKEN_COOKIE

# коды закрытия, по которым клиент НЕ переподключается
CLOSE_FINISHED = 4000
CLOSE_KICKED = 4001
CLOSE_DENIED = 4003

TEACHER_ACTIONS = {"start", "reset", "kick", "finish", "set_timer", "hold_auto"}
STUDENT_ACTIONS = {"mark", "unmark"}
TIMER_ACTIONS = {"start", "reset", "set_timer"}


def _same(a, b):
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


class RoomConsumer(AsyncJsonWebsocketConsumer):
    role = None  # "teacher" | "student" — из пути
    code = None
    room_id = None
    student_id = None
    group = None
    counted = False  # учтён ли сокет в presence

    # --- подключение ------------------------------------------------------

    async def connect(self):
        kwargs = self.scope["url_route"]["kwargs"]
        code, role = kwargs["code"], kwargs["role"]
        await self.accept()  # принимаем, чтобы отдать понятный код закрытия
        room_info = await database_sync_to_async(self._authenticate)(code, role)
        if room_info is None:
            await self.close(code=CLOSE_DENIED)
            return
        self.code, self.role = code, role
        self.group = timers.group_name(code)
        await self.channel_layer.group_add(self.group, self.channel_name)
        presence.room_opened(code)
        self.counted = True
        if role == "teacher":
            # после перезапуска сервера таймер потерян — поднимаем, не сбрасывая идущий
            timers.sync_with_room(code, *room_info, restart=False)
        else:
            await presence.student_connected(self.student_id)
        await timers.broadcast_state(code)

    def _authenticate(self, code, role):
        room = services.open_room(code)
        if room is None:
            return None
        info = (room.status, room.timer_seconds, room.round)
        if role == "teacher":
            session = self.scope.get("session")
            if session is not None and session.get("teacher_room") == code:
                if _same(room.teacher_token, session.get("teacher_token") or ""):
                    self.room_id = room.pk
                    return info
            return None
        token = self.scope.get("cookies", {}).get(STUDENT_TOKEN_COOKIE)
        if token:
            student = Student.objects.filter(room=room, token=token).first()
            if student:
                self.room_id, self.student_id = room.pk, student.pk
                return info
        return None

    async def disconnect(self, close_code):
        if not self.counted:
            return
        self.counted = False
        await self.channel_layer.group_discard(self.group, self.channel_name)
        presence.room_closed(self.code)
        if self.role == "student" and self.student_id:
            gone = await presence.student_disconnected(self.student_id)
            if gone:  # другие вкладки этого студента ещё открыты — ничего не меняем
                await timers.broadcast_state(self.code)

    # --- входящие действия ------------------------------------------------

    async def receive_json(self, content, **kwargs):
        action = content.get("type") if isinstance(content, dict) else None
        if action == "ping":
            if self.role == "student" and self.student_id:
                came_back = await database_sync_to_async(services.touch)(self.student_id)
                if came_back:
                    await timers.broadcast_state(self.code)
            return
        allowed = TEACHER_ACTIONS if self.role == "teacher" else STUDENT_ACTIONS
        if action not in allowed:
            await self.send_json({"type": "error", "action": action, "message": "Действие недоступно"})
            return
        result = await database_sync_to_async(self._apply)(action, content)
        if result == "gone":  # комната завершена/устарела или студент удалён
            await self.close(code=CLOSE_DENIED)
            return
        if result == "bad":
            await self.send_json({"type": "error", "action": action, "message": "Неверное значение"})
            return
        if action == "finish":
            timers.cancel(self.code)
            timers.cancel_auto(self.code)
            await self.channel_layer.group_send(self.group, {"type": "room.finished"})
            return
        if action in TIMER_ACTIONS:
            timers.sync_with_room(self.code, *result)
        if action == "hold_auto":
            timers.hold_auto(self.code, result[2])
        if action == "kick" and result:
            presence.forget_student(result)
            await self.channel_layer.group_send(self.group, {"type": "room.kicked", "student_id": result})
        await timers.broadcast_state(self.code)

    def _apply(self, action, content):
        room = services.live_rooms().filter(pk=self.room_id).first()
        if room is None:
            return "gone"
        if self.role == "teacher":
            if action == "start":
                services.start(room)
            elif action == "reset":
                # клиент шлёт раунд, который видит: если он уже сменился (таймер/авто-раунд
                # успели раньше) — второй раз не сбрасываем
                expected = content.get("round")
                try:
                    expected = int(expected) if expected is not None else None
                except (TypeError, ValueError):
                    return "bad"
                services.reset(room, expected)
            elif action == "finish":
                services.finish(room)
                return None
            elif action == "set_timer":
                try:
                    seconds = services.parse_timer(content.get("seconds"))
                except (TypeError, ValueError):
                    return "bad"
                services.set_timer(room, seconds)
            elif action == "kick":
                try:
                    sid = int(content.get("student_id"))
                except (TypeError, ValueError):
                    return None
                return sid if services.kick(room, sid) else None
            room.refresh_from_db()
            return (room.status, room.timer_seconds, room.round)
        student = Student.objects.filter(pk=self.student_id, room=room).first()
        if student is None:
            return "gone"
        services.touch(student.pk)  # любое действие = «видели сейчас»
        if action == "mark":
            if not services.mark(student):
                return "bad"  # урок не идёт — клиент откатит «Отмечаю…»
        else:
            services.unmark(student)
        return None

    # --- рассылка ---------------------------------------------------------

    async def room_state(self, event):
        state = event["state"]
        if self.role == "teacher":
            await self.send_json({"type": "state", **state})
        else:
            await self.send_json({"type": "state", **services.student_view(state, self.student_id)})

    async def room_kicked(self, event):
        if self.role == "student" and event["student_id"] == self.student_id:
            await self.send_json({"type": "kicked"})
            self.student_id = None  # не трогаем is_online удалённой записи
            await self.close(code=CLOSE_KICKED)

    async def room_finished(self, event):
        await self.send_json({"type": "finished"})
        self.student_id = None
        await self.close(code=CLOSE_FINISHED)
