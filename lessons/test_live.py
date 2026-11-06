"""Этапы 2–3: services живого урока, таймер, срок жизни комнат и RoomConsumer."""
from datetime import timedelta
from io import StringIO

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from config.asgi import application

from . import presence, services, timers
from .models import Mark, Room, Student
from .views import STUDENT_TOKEN_COOKIE


class ServiceTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(code="2468", teacher_token="t")
        self.a = Student.objects.create(room=self.room, name="Айдар", token="a")
        self.b = Student.objects.create(room=self.room, name="Бек", token="b")

    def test_mark_only_when_active(self):
        self.assertFalse(services.mark(self.a))
        services.start(self.room)
        self.assertTrue(services.mark(self.a))
        services.mark(self.a)  # повторный клик не ломает
        state = services.room_state(self.room)
        self.assertEqual((state["marked"], state["total"]), (1, 2))
        self.assertEqual(state["status"], "active")

    def test_unmark(self):
        services.start(self.room)
        services.mark(self.a)
        services.unmark(self.a)
        self.assertEqual(services.room_state(self.room)["marked"], 0)

    def test_reset_next_round_clears_marks(self):
        services.start(self.room)
        services.mark(self.a)
        services.mark(self.b)
        services.reset(self.room)
        state = services.room_state(self.room)
        self.assertEqual(state["round"], 2)
        self.assertEqual(state["marked"], 0)
        self.assertFalse(Mark.objects.exists())
        services.mark(self.a)
        self.assertTrue(Mark.objects.filter(student=self.a, round=2).exists())

    def test_kick_frees_name(self):
        self.assertTrue(services.kick(self.room, self.a.id))
        self.assertEqual(services.room_state(self.room)["total"], 1)
        student, reconnected = services.join_room(self.room, "айдар", "new-token")
        self.assertFalse(reconnected)

    def test_kick_other_room_student_ignored(self):
        other = Room.objects.create(code="1357", teacher_token="x")
        self.assertFalse(services.kick(other, self.a.id))
        self.assertTrue(Student.objects.filter(pk=self.a.pk).exists())

    def test_finish_closes_code(self):
        services.finish(self.room)
        self.assertIsNone(services.open_room(self.room.code))

    def test_student_view(self):
        services.start(self.room)
        services.mark(self.a)
        state = services.room_state(self.room)
        self.assertTrue(services.student_view(state, self.a.id)["marked"])
        self.assertFalse(services.student_view(state, self.b.id)["marked"])


class ExpiryAndTimerServiceTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(code="3579", teacher_token="t")

    def age(self, room, hours):
        Room.objects.filter(pk=room.pk).update(created_at=timezone.now() - timedelta(hours=hours))

    def test_room_older_than_12h_not_found(self):
        self.age(self.room, 12.1)
        self.assertIsNone(services.open_room(self.room.code))
        r = self.client.post(reverse("join"), {"name": "Айдар", "code": self.room.code})
        self.assertContains(r, "Урок не найден")

    def test_room_younger_than_12h_found(self):
        self.age(self.room, 11.9)
        self.assertIsNotNone(services.open_room(self.room.code))

    def test_cleanup_command(self):
        old = Room.objects.create(code="1111", teacher_token="o")
        Student.objects.create(room=old, name="Старый", token="s")
        self.age(old, 13)
        out = StringIO()
        call_command("cleanup_rooms", stdout=out)
        self.assertFalse(Room.objects.filter(pk=old.pk).exists())
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())
        self.assertFalse(Student.objects.exists())

    def test_create_room_cleans_old_lazily(self):
        self.age(self.room, 24)
        services.create_room()
        self.assertFalse(Room.objects.filter(pk=self.room.pk).exists())

    def test_online_needs_fresh_last_seen(self):
        s = Student.objects.create(room=self.room, name="Айдар", token="a", is_online=True,
                                   last_seen=timezone.now() - timedelta(seconds=60))
        self.assertFalse(services.room_state(self.room)["students"][0]["online"])  # сокет «висит», ping нет
        self.assertTrue(services.touch(s.id))  # вернулся
        self.assertTrue(services.room_state(self.room)["students"][0]["online"])
        self.assertFalse(services.touch(s.id))  # и так был в сети
        services.set_online(s.id, False)
        self.assertFalse(services.room_state(self.room)["students"][0]["online"])

    def test_parse_timer(self):
        self.assertIsNone(services.parse_timer(None))
        self.assertIsNone(services.parse_timer(""))
        self.assertEqual(services.parse_timer("120"), 120)
        for bad in ("abc", -5, 99999):
            with self.assertRaises(ValueError):
                services.parse_timer(bad)

    def test_timer_reset_respects_round(self):
        services.start(self.room)
        self.assertIsNone(services.timer_reset(self.room.code, 5))  # раунд уже другой
        self.assertEqual(services.timer_reset(self.room.code, 1).round, 2)


ORIGIN = (b"origin", b"http://localhost")


class ConsumerTests(TransactionTestCase):
    def setUp(self):
        self.room = Room.objects.create(code="8642", teacher_token="teacher-secret")
        self.student = Student.objects.create(room=self.room, name="Айдар", token="stud-token")
        session = SessionStore()
        session["teacher_room"] = self.room.code
        session["teacher_token"] = self.room.teacher_token
        session.create()
        self.teacher_cookie = f"{settings.SESSION_COOKIE_NAME}={session.session_key}".encode()
        self.student_cookie = f"{STUDENT_TOKEN_COOKIE}=stud-token".encode()
        self.both_cookie = self.teacher_cookie + b"; " + self.student_cookie  # один браузер, две роли

    def tearDown(self):
        timers.clear_all()
        presence.clear_all()

    def comm(self, cookie=None, role=None):
        """role — какая страница подключается; по умолчанию по cookie."""
        role = role or ("teacher" if cookie == self.teacher_cookie else "student")
        headers = [ORIGIN] + ([(b"cookie", cookie)] if cookie else [])
        return WebsocketCommunicator(application, f"/ws/rooms/{self.room.code}/{role}/", headers=headers)

    # --- роль задаёт страница (баг: агай и студент в одном браузере) ---

    async def test_student_tab_in_teacher_browser_marks(self):
        teacher = self.comm(self.both_cookie, role="teacher")
        self.assertTrue((await teacher.connect())[0])
        await teacher.receive_json_from()
        student = self.comm(self.both_cookie, role="student")  # та же сессия агая в cookies!
        self.assertTrue((await student.connect())[0])
        s = await student.receive_json_from()
        t = await teacher.receive_json_from()
        self.assertNotIn("students", s)  # пришёл студенческий state — значит роль student
        self.assertTrue(t["students"][0]["online"])
        await teacher.send_json_to({"type": "start"})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertEqual(t["marked"], 1)
        self.assertTrue(s["marked"])
        await teacher.disconnect()
        await student.disconnect()

    async def test_teacher_path_without_session_denied(self):
        c = self.comm(self.student_cookie, role="teacher")
        await c.connect()
        self.assertEqual((await c.receive_output())["code"], 4003)

    async def test_student_path_without_student_cookie_denied(self):
        c = self.comm(self.teacher_cookie, role="student")
        await c.connect()
        self.assertEqual((await c.receive_output())["code"], 4003)

    async def test_f5_race_keeps_student_online(self):
        teacher, old_tab, _, _ = await self.connect_both()
        new_tab = self.comm(self.student_cookie)  # F5: новый сокет раньше закрытия старого
        await new_tab.connect()
        await new_tab.receive_json_from()
        await old_tab.receive_json_from()
        t = await teacher.receive_json_from()
        self.assertTrue(t["students"][0]["online"])
        await old_tab.disconnect()  # старый закрылся последним
        self.assertTrue(await teacher.receive_nothing())  # «не в сети» не прилетело
        student = await database_sync_to_async(Student.objects.get)(pk=self.student.pk)
        self.assertTrue(services.is_online(student))
        await new_tab.disconnect()  # закрыли последнюю вкладку → теперь не в сети
        t = await teacher.receive_json_from()
        self.assertFalse(t["students"][0]["online"])
        await teacher.disconnect()

    async def test_ping_brings_stale_student_back(self):
        teacher, student, _, _ = await self.connect_both()
        await database_sync_to_async(Student.objects.filter(pk=self.student.pk).update)(
            last_seen=timezone.now() - timedelta(seconds=60)
        )
        await student.send_json_to({"type": "ping"})
        t = await teacher.receive_json_from()
        self.assertTrue(t["students"][0]["online"])
        await student.send_json_to({"type": "ping"})  # свежий ping — лишней рассылки нет
        self.assertTrue(await teacher.receive_nothing())
        await teacher.disconnect()
        await student.disconnect()

    async def test_mark_before_start_is_error(self):
        teacher, student, _, _ = await self.connect_both()
        await student.send_json_to({"type": "mark"})
        msg = await student.receive_json_from()
        self.assertEqual((msg["type"], msg["action"]), ("error", "mark"))
        await teacher.disconnect()
        await student.disconnect()

    async def connect_both(self):
        teacher = self.comm(self.teacher_cookie)
        self.assertTrue((await teacher.connect())[0])
        await teacher.receive_json_from()  # state после подключения агая
        student = self.comm(self.student_cookie)
        self.assertTrue((await student.connect())[0])
        s_state = await student.receive_json_from()
        t_state = await teacher.receive_json_from()
        return teacher, student, s_state, t_state

    async def test_start_reaches_student_and_marks_reach_teacher(self):
        teacher, student, s_state, t_state = await self.connect_both()
        self.assertEqual(s_state["status"], "waiting")
        self.assertTrue(t_state["students"][0]["online"])
        self.assertNotIn("students", s_state)  # студенту не шлём чужие имена

        await teacher.send_json_to({"type": "start"})
        self.assertEqual((await student.receive_json_from())["status"], "active")
        await teacher.receive_json_from()

        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertEqual((t["marked"], t["total"]), (1, 1))
        self.assertTrue(s["marked"])

        await teacher.send_json_to({"type": "reset"})
        s = await student.receive_json_from()
        self.assertEqual((s["round"], s["marked"]), (2, False))
        await teacher.disconnect()
        await student.disconnect()

    async def test_student_cannot_reset(self):
        teacher, student, _, _ = await self.connect_both()
        await student.send_json_to({"type": "reset"})
        self.assertEqual((await student.receive_json_from())["type"], "error")
        self.assertTrue(await teacher.receive_nothing())
        room = await database_sync_to_async(Room.objects.get)(pk=self.room.pk)
        self.assertEqual(room.round, 1)
        await teacher.disconnect()
        await student.disconnect()

    async def test_kick_closes_student(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "kick", "student_id": self.student.id})
        self.assertEqual((await student.receive_json_from())["type"], "kicked")
        self.assertEqual((await student.receive_output())["code"], 4001)
        t = await teacher.receive_json_from()
        self.assertEqual(t["total"], 0)
        await teacher.disconnect()

    async def test_finish_disconnects_everyone(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "finish"})
        self.assertEqual((await student.receive_json_from())["type"], "finished")
        self.assertEqual((await student.receive_output())["code"], 4000)
        self.assertEqual((await teacher.receive_json_from())["type"], "finished")
        room = await database_sync_to_async(Room.objects.get)(pk=self.room.pk)
        self.assertEqual(room.status, "finished")

    async def test_unknown_visitor_denied(self):
        c = self.comm()
        await c.connect()
        self.assertEqual((await c.receive_output())["code"], 4003)

    async def test_timer_auto_reset(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "set_timer", "seconds": 1})
        t = await teacher.receive_json_from()
        self.assertEqual(t["timer_seconds"], 1)
        self.assertIsNone(t["deadline"])  # урок ещё не начат — отсчёта нет
        await student.receive_json_from()

        await teacher.send_json_to({"type": "start"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertIsNotNone(t["deadline"])
        self.assertEqual(s["deadline"], t["deadline"])  # студент видит тот же отсчёт

        await student.send_json_to({"type": "mark"})
        await teacher.receive_json_from()
        await student.receive_json_from()

        s = await student.receive_json_from(timeout=3)  # автосброс сервера
        t = await teacher.receive_json_from(timeout=3)
        self.assertEqual((s["round"], s["marked"]), (2, False))
        self.assertEqual((t["round"], t["marked"]), (2, 0))
        self.assertGreater(t["deadline"], t["now"])  # следующий раунд снова с таймером
        self.assertEqual(len(timers._timers), 1)  # один таймер на комнату

        await teacher.send_json_to({"type": "set_timer", "seconds": None})
        t = await teacher.receive_json_from()
        self.assertIsNone(t["deadline"])
        self.assertNotIn(self.room.code, timers._timers)
        await teacher.disconnect()
        await student.disconnect()

    async def test_two_teacher_tabs_one_timer(self):
        t1 = self.comm(self.teacher_cookie)
        await t1.connect()
        await t1.receive_json_from()
        t2 = self.comm(self.teacher_cookie)
        await t2.connect()
        await t1.receive_json_from()
        await t2.receive_json_from()
        await t1.send_json_to({"type": "set_timer", "seconds": 60})
        await t1.send_json_to({"type": "start"})
        for _ in range(2):
            await t1.receive_json_from()
            await t2.receive_json_from()
        self.assertEqual(len(timers._timers), 1)
        await t1.send_json_to({"type": "finish"})
        await t1.receive_json_from()
        self.assertEqual(timers._timers, {})
        await t2.disconnect()

    async def test_student_cannot_set_timer(self):
        teacher, student, _, _ = await self.connect_both()
        await student.send_json_to({"type": "set_timer", "seconds": 5})
        self.assertEqual((await student.receive_json_from())["type"], "error")
        self.assertTrue(await teacher.receive_nothing())
        room = await database_sync_to_async(Room.objects.get)(pk=self.room.pk)
        self.assertIsNone(room.timer_seconds)
        await teacher.disconnect()
        await student.disconnect()

    async def test_bad_timer_value_rejected(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "set_timer", "seconds": "abc"})
        self.assertEqual((await teacher.receive_json_from())["type"], "error")
        await teacher.disconnect()
        await student.disconnect()

    async def test_expired_room_ws_denied(self):
        await database_sync_to_async(Room.objects.filter(pk=self.room.pk).update)(
            created_at=timezone.now() - timedelta(hours=13)
        )
        c = self.comm(self.teacher_cookie)
        await c.connect()
        self.assertEqual((await c.receive_output())["code"], 4003)

    async def test_disconnect_marks_offline(self):
        teacher, student, _, _ = await self.connect_both()
        await student.disconnect()
        t = await teacher.receive_json_from()
        self.assertFalse(t["students"][0]["online"])
        await teacher.disconnect()
