"""Этапы 2–3: services живого урока, таймер, срок жизни комнат и RoomConsumer."""
from datetime import timedelta
from io import StringIO

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
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

    def test_room_active_right_away_mark_works(self):
        self.assertEqual(self.room.status, Room.Status.ACTIVE)  # кнопки «Начать урок» больше нет
        self.assertTrue(services.mark(self.a))
        services.mark(self.a)  # повторный клик не ломает
        state = services.room_state(self.room)
        self.assertEqual((state["marked"], state["total"]), (1, 2))
        self.assertEqual(state["status"], "active")

    def test_no_mark_after_finish(self):
        services.finish(self.room)
        self.assertFalse(services.mark(self.a))

    def test_old_waiting_room_becomes_active(self):
        Room.objects.filter(pk=self.room.pk).update(status=Room.Status.WAITING)
        room = services.open_room(self.room.code)
        self.assertEqual(room.status, Room.Status.ACTIVE)
        self.assertEqual(Room.objects.get(pk=self.room.pk).status, Room.Status.ACTIVE)
        self.assertTrue(services.mark(self.a))

    def test_parse_auto_next(self):
        self.assertEqual(services.parse_auto_next(True, 5), (True, 5))
        self.assertEqual(services.parse_auto_next(False, "10"), (False, 10))
        self.assertEqual(services.parse_auto_next(False, None), (False, None))  # задержку не меняем
        for enabled, seconds in ((1, 5), ("yes", 5), (None, 5), (True, 0), (True, 61), (True, 2.5),
                                 (True, "abc"), (True, True), (True, [3])):
            with self.assertRaises((ValueError, TypeError)):
                services.parse_auto_next(enabled, seconds)

    def test_auto_next_disabled_keeps_round(self):
        services.set_auto_next(self.room, False)
        services.mark(self.a)
        services.mark(self.b)
        self.assertIsNone(services.auto_next(self.room.code, 1))
        self.assertEqual(Room.objects.get(pk=self.room.pk).round, 1)

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


class RoundEndServiceTests(TestCase):
    """Конец раунда: таймер выключается, двойного сброса нет, авто-раунд только если записали все."""

    def setUp(self):
        self.room = Room.objects.create(code="9753", teacher_token="t", timer_seconds=60)
        self.a = Student.objects.create(room=self.room, name="Айдар", token="a")
        self.b = Student.objects.create(room=self.room, name="Бек", token="b")
        services.start(self.room)

    def test_reset_turns_timer_off(self):
        room = services.reset(self.room)
        self.assertEqual((room.round, room.timer_seconds), (2, None))

    def test_timer_reset_turns_timer_off(self):
        room = services.timer_reset(self.room.code, 1)
        self.assertEqual((room.round, room.timer_seconds), (2, None))

    def test_reset_with_stale_round_does_nothing(self):
        self.assertEqual(services.reset(self.room, 1).round, 2)
        self.assertIsNone(services.reset(self.room, 1))  # второй сброс того же раунда
        self.assertEqual(Room.objects.get(pk=self.room.pk).round, 2)

    def test_reset_keeps_marks_of_new_round(self):
        services.mark(self.a)
        services.reset(self.room, 1)
        services.mark(self.a)  # уже раунд 2
        self.assertIsNone(services.reset(self.room, 1))  # опоздавший сброс не трогает раунд 2
        self.assertTrue(Mark.objects.filter(student=self.a, round=2).exists())

    def test_auto_next_only_when_all_marked(self):
        services.mark(self.a)
        self.assertIsNone(services.auto_next(self.room.code, 1))  # Бек ещё не записал
        services.mark(self.b)
        room = services.auto_next(self.room.code, 1)
        self.assertEqual((room.round, room.timer_seconds), (2, None))
        self.assertFalse(Mark.objects.exists())
        self.assertIsNone(services.auto_next(self.room.code, 1))  # раунд уже другой

    def test_auto_next_empty_room(self):
        Student.objects.all().delete()
        self.assertIsNone(services.auto_next(self.room.code, 1))  # 0 из 0 — не «все записали»


ORIGIN = (b"origin", b"http://localhost")
AUTO = 0.3  # settings.AUTO_NEXT_SECONDS в тестах


@override_settings(AUTO_NEXT_SECONDS=AUTO)
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

    async def test_student_marks_right_away(self):
        # урок идёт сразу после создания: без «start» студент отмечается
        teacher, student, s_state, t_state = await self.connect_both()
        self.assertEqual((s_state["status"], t_state["status"]), ("active", "active"))
        await database_sync_to_async(Room.objects.filter(pk=self.room.pk).update)(auto_next_enabled=False)
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertEqual((t["marked"], t["total"]), (1, 1))
        self.assertTrue(s["marked"])
        await teacher.disconnect()
        await student.disconnect()

    async def test_old_waiting_room_ws_is_active(self):
        await database_sync_to_async(Room.objects.filter(pk=self.room.pk).update)(status=Room.Status.WAITING)
        teacher, student, s_state, t_state = await self.connect_both()
        self.assertEqual((s_state["status"], t_state["status"]), ("active", "active"))
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
        self.assertEqual(s_state["status"], "active")
        self.assertTrue(t_state["students"][0]["online"])
        self.assertNotIn("students", s_state)  # студенту не шлём чужие имена

        await teacher.send_json_to({"type": "start"})  # старая вкладка: событие принимается, ничего не меняет
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
        s = await student.receive_json_from()
        self.assertEqual(t["timer_seconds"], 1)
        self.assertIsNotNone(t["deadline"])  # урок идёт сразу — таймер действует на текущий раунд
        self.assertEqual(s["deadline"], t["deadline"])  # студент видит тот же отсчёт

        self.assertEqual(len(timers._timers), 1)  # один таймер на комнату

        s = await student.receive_json_from(timeout=3)  # автосброс сервера (никто не отметился)
        t = await teacher.receive_json_from(timeout=3)
        self.assertEqual((s["round"], s["marked"]), (2, False))
        self.assertEqual((t["round"], t["marked"]), (2, 0))
        # таймер был только на тот раунд: выключен, сегмент снова «Выкл»
        self.assertIsNone(t["timer_seconds"])
        self.assertIsNone(t["deadline"])
        self.assertIsNone(s["deadline"])
        self.assertNotIn(self.room.code, timers._timers)
        self.assertTrue(await teacher.receive_nothing(timeout=1.3))  # второго автосброса нет
        room = await database_sync_to_async(Room.objects.get)(pk=self.room.pk)
        self.assertEqual((room.round, room.timer_seconds), (2, None))
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

    # --- таймер только на один раунд + авто-новый раунд ---------------------

    async def room_now(self):
        return await database_sync_to_async(Room.objects.get)(pk=self.room.pk)

    async def started_and_marked(self, timer=None):
        """Агай + студент, урок идёт, студент отметился (он единственный → записали все)."""
        teacher, student, _, _ = await self.connect_both()
        if timer:
            await teacher.send_json_to({"type": "set_timer", "seconds": timer})
            await teacher.receive_json_from()
            await student.receive_json_from()
        await teacher.send_json_to({"type": "start"})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        return teacher, student, t, s

    async def test_manual_reset_turns_timer_off(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "set_timer", "seconds": 60})  # до «Начать урок»
        await teacher.receive_json_from()
        await student.receive_json_from()
        await teacher.send_json_to({"type": "start"})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertIsNotNone(t["deadline"])  # таймер действует на первый раунд
        await teacher.send_json_to({"type": "reset", "round": 1})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertEqual((t["round"], t["timer_seconds"], t["deadline"]), (2, None, None))
        self.assertNotIn(self.room.code, timers._timers)
        await teacher.disconnect()
        await student.disconnect()

    async def test_all_marked_starts_new_round(self):
        teacher, student, t, s = await self.started_and_marked(timer=60)
        self.assertEqual((t["marked"], t["total"]), (1, 1))
        self.assertIsNotNone(t["auto_next_at"])
        self.assertEqual(s["auto_next_at"], t["auto_next_at"])  # студент видит тот же отсчёт
        self.assertAlmostEqual(t["auto_next_at"] - t["now"], AUTO, delta=0.2)

        t = await teacher.receive_json_from(timeout=AUTO + 1)  # сервер сам начал новый раунд
        s = await student.receive_json_from(timeout=1)
        self.assertEqual((t["round"], t["marked"], t["auto_next_at"]), (2, 0, None))
        self.assertEqual((s["round"], s["marked"], s["auto_next_at"]), (2, False, None))
        self.assertEqual((t["timer_seconds"], t["deadline"]), (None, None))  # таймер выключился
        self.assertEqual(timers._timers, {})
        self.assertEqual(timers._auto, {})
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.3))  # ровно один новый раунд
        self.assertEqual((await self.room_now()).round, 2)
        await teacher.disconnect()
        await student.disconnect()

    async def test_auto_next_works_without_teacher_panel(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "start"})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await teacher.disconnect()  # агай закрыл панель
        await student.send_json_to({"type": "mark"})
        s = await student.receive_json_from()
        self.assertIsNotNone(s["auto_next_at"])
        s = await student.receive_json_from(timeout=AUTO + 1)
        self.assertEqual((s["round"], s["marked"]), (2, False))
        await student.disconnect()

    async def test_unmark_cancels_auto_next(self):
        teacher, student, t, _ = await self.started_and_marked()
        self.assertIsNotNone(t["auto_next_at"])
        await student.send_json_to({"type": "unmark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertIsNone(t["auto_next_at"])
        self.assertIsNone(s["auto_next_at"])
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.3))
        self.assertEqual((await self.room_now()).round, 1)
        await teacher.disconnect()
        await student.disconnect()

    async def test_new_student_cancels_auto_next(self):
        teacher, student, t, _ = await self.started_and_marked()
        self.assertIsNotNone(t["auto_next_at"])
        # вошёл по HTTP, сокет ещё не открыл — сервер всё равно перепроверит «записали все»
        await database_sync_to_async(services.join_room)(self.room, "Бек", "bek-token")
        t = await teacher.receive_json_from(timeout=AUTO + 1)
        self.assertEqual((t["round"], t["marked"], t["total"]), (1, 1, 2))
        self.assertIsNone(t["auto_next_at"])
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.3))
        await teacher.disconnect()
        await student.disconnect()

    async def test_hold_auto_by_teacher(self):
        teacher, student, _, _ = await self.started_and_marked()
        await teacher.send_json_to({"type": "hold_auto"})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertIsNone(t["auto_next_at"])
        self.assertTrue(t["auto_held"])
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.3))
        self.assertEqual((await self.room_now()).round, 1)
        # unmark → mark в том же раунде — «Подождать» всё ещё действует
        await student.send_json_to({"type": "unmark"})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertIsNone(t["auto_next_at"])
        # агай сбрасывает сам → в новом раунде авто-раунд снова работает
        await teacher.send_json_to({"type": "reset", "round": 1})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertEqual((t["round"], t["auto_held"]), (2, False))
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertIsNotNone(t["auto_next_at"])
        await teacher.disconnect()
        await student.disconnect()

    async def test_student_cannot_hold_auto(self):
        teacher, student, _, _ = await self.started_and_marked()
        await student.send_json_to({"type": "hold_auto"})
        msg = await student.receive_json_from()
        self.assertEqual((msg["type"], msg["action"]), ("error", "hold_auto"))
        t = await teacher.receive_json_from(timeout=AUTO + 1)  # отсчёт не отменился
        self.assertEqual(t["round"], 2)
        await teacher.disconnect()
        await student.disconnect()

    async def test_manual_reset_during_countdown_no_double(self):
        teacher, student, t, _ = await self.started_and_marked()
        self.assertIsNotNone(t["auto_next_at"])
        await teacher.send_json_to({"type": "reset", "round": 1})  # «Сбросить» в окне 3 с
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertEqual((t["round"], t["auto_next_at"]), (2, None))
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.3))  # авто-раунд не догнал
        self.assertEqual((await self.room_now()).round, 2)
        await teacher.disconnect()
        await student.disconnect()

    async def test_late_manual_reset_after_auto_next_ignored(self):
        teacher, student, _, _ = await self.started_and_marked()
        t = await teacher.receive_json_from(timeout=AUTO + 1)  # авто-раунд успел первым
        await student.receive_json_from()
        self.assertEqual(t["round"], 2)
        await teacher.send_json_to({"type": "reset", "round": 1})  # клик агая «опоздал»
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertEqual(t["round"], 2)
        self.assertEqual((await self.room_now()).round, 2)
        await teacher.disconnect()
        await student.disconnect()

    # --- настройка авто-раунда (set_auto_next) -------------------------------

    async def test_set_auto_next_by_teacher(self):
        teacher, student, _, t_state = await self.connect_both()
        self.assertEqual((t_state["auto_next_enabled"], t_state["auto_next_seconds"]), (True, AUTO))
        await teacher.send_json_to({"type": "set_auto_next", "enabled": False, "seconds": 10})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertEqual((t["auto_next_enabled"], t["auto_next_seconds"]), (False, 10))
        room = await self.room_now()
        self.assertEqual((room.auto_next_enabled, room.auto_next_seconds), (False, 10))
        # без seconds — задержка остаётся
        await teacher.send_json_to({"type": "set_auto_next", "enabled": True})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertEqual((t["auto_next_enabled"], t["auto_next_seconds"]), (True, 10))
        await teacher.disconnect()
        await student.disconnect()

    async def test_student_cannot_set_auto_next(self):
        teacher, student, _, _ = await self.connect_both()
        await student.send_json_to({"type": "set_auto_next", "enabled": False, "seconds": 10})
        msg = await student.receive_json_from()
        self.assertEqual((msg["type"], msg["action"]), ("error", "set_auto_next"))
        self.assertTrue(await teacher.receive_nothing())
        room = await self.room_now()
        self.assertEqual((room.auto_next_enabled, room.auto_next_seconds), (True, AUTO))
        await teacher.disconnect()
        await student.disconnect()

    async def test_bad_auto_next_rejected(self):
        teacher, student, _, _ = await self.connect_both()
        for bad in ({"enabled": "yes"}, {"enabled": True, "seconds": 0}, {"enabled": True, "seconds": 61},
                    {"enabled": True, "seconds": "abc"}, {"enabled": True, "seconds": 2.5}):
            await teacher.send_json_to({"type": "set_auto_next", **bad})
            msg = await teacher.receive_json_from()
            self.assertEqual((msg["type"], msg["action"]), ("error", "set_auto_next"), bad)
        room = await self.room_now()
        self.assertEqual((room.auto_next_enabled, room.auto_next_seconds), (True, AUTO))
        await teacher.disconnect()
        await student.disconnect()

    async def test_auto_next_disabled_round_stays(self):
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "set_auto_next", "enabled": False})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertEqual((t["marked"], t["total"]), (1, 1))  # все записали…
        self.assertIsNone(t["auto_next_at"])  # …но отсчёта нет
        self.assertIsNone(s["auto_next_at"])
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.5))
        self.assertEqual((await self.room_now()).round, 1)
        self.assertEqual(timers._auto, {})
        await teacher.disconnect()
        await student.disconnect()

    async def test_disable_during_countdown_cancels(self):
        teacher, student, t, _ = await self.started_and_marked()
        self.assertIsNotNone(t["auto_next_at"])
        await teacher.send_json_to({"type": "set_auto_next", "enabled": False})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertIsNone(t["auto_next_at"])
        self.assertTrue(await teacher.receive_nothing(timeout=AUTO + 0.5))
        self.assertEqual((await self.room_now()).round, 1)
        await teacher.disconnect()
        await student.disconnect()

    async def test_auto_next_delay_from_room(self):
        # задержка берётся из комнаты: 5 с в отсчёте; ждём по-настоящему маленькое значение
        teacher, student, _, _ = await self.connect_both()
        await teacher.send_json_to({"type": "set_auto_next", "enabled": True, "seconds": 5})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        s = await student.receive_json_from()
        self.assertAlmostEqual(t["auto_next_at"] - t["now"], 5, delta=0.2)
        self.assertEqual(s["auto_next_at"], t["auto_next_at"])
        timers.cancel_auto(self.room.code)  # 5 с не ждём — проверим соблюдение на 0.6 с

        delay = 0.6
        await database_sync_to_async(Room.objects.filter(pk=self.room.pk).update)(auto_next_seconds=delay)
        await student.send_json_to({"type": "unmark"})
        await teacher.receive_json_from()
        await student.receive_json_from()
        await student.send_json_to({"type": "mark"})
        t = await teacher.receive_json_from()
        await student.receive_json_from()
        self.assertAlmostEqual(t["auto_next_at"] - t["now"], delay, delta=0.15)
        self.assertTrue(await teacher.receive_nothing(timeout=delay - 0.25))  # раньше срока — ничего
        self.assertEqual((await self.room_now()).round, 1)
        t = await teacher.receive_json_from(timeout=1)
        self.assertEqual(t["round"], 2)
        await teacher.disconnect()
        await student.disconnect()

    async def test_auto_next_and_timer_same_moment_no_double(self):
        # таймер и авто-раунд сработали одновременно — раунд увеличился один раз
        teacher, student, _, _ = await self.started_and_marked()
        r1 = await database_sync_to_async(services.auto_next)(self.room.code, 1)
        r2 = await database_sync_to_async(services.timer_reset)(self.room.code, 1)
        self.assertEqual(r1.round, 2)
        self.assertIsNone(r2)
        self.assertEqual((await self.room_now()).round, 2)
        await teacher.disconnect()
        await student.disconnect()
