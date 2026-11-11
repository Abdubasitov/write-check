from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Mark, Room, Student
from .views import STUDENT_NAME_COOKIE, STUDENT_TOKEN_COOKIE

PASSWORD = "test-pass"


@override_settings(TEACHER_PASSWORD=PASSWORD)
class TeacherPasswordTests(TestCase):
    url = reverse("teacher_new")

    def setUp(self):
        cache.clear()  # счётчик неверных паролей по IP живёт в кэше

    def test_rate_limit_by_ip_without_cookies(self):
        # перебор без cookie: каждая попытка — новая сессия, но IP тот же → после 5 блок
        for _ in range(5):
            self.client_class().post(self.url, {"password": "nope"})
        r = self.client_class().post(self.url, {"password": PASSWORD})
        self.assertEqual(r.status_code, 429)
        self.assertFalse(Room.objects.exists())
        # другой IP не заблокирован
        r = self.client_class().post(self.url, {"password": PASSWORD}, REMOTE_ADDR="10.0.0.2")
        self.assertEqual(r.status_code, 302)

    def test_csrf_required(self):
        c = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(c.post(self.url, {"password": PASSWORD}).status_code, 403)
        self.assertFalse(Room.objects.exists())

    def test_correct_password_creates_room_and_redirects(self):
        r = self.client.post(self.url, {"password": PASSWORD})
        room = Room.objects.get()
        self.assertRedirects(r, reverse("teacher", args=[room.code]))
        self.assertTrue(room.code.isdigit() and 4 <= len(room.code) <= 6)
        self.assertEqual(self.client.session["teacher_token"], room.teacher_token)
        # урок начинается сразу, авто-раунд по умолчанию включён на 3 с
        self.assertEqual((room.status, room.round), (Room.Status.ACTIVE, 1))
        self.assertEqual((room.auto_next_enabled, room.auto_next_seconds), (True, 3))
        page = self.client.get(reverse("teacher", args=[room.code]))
        self.assertContains(page, room.code)
        self.assertNotContains(page, "Начать урок")

    def test_wrong_password(self):
        r = self.client.post(self.url, {"password": "nope"})
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "Неверный пароль", status_code=403)
        self.assertFalse(Room.objects.exists())

    def test_rate_limit_five_per_minute(self):
        for _ in range(5):
            self.client.post(self.url, {"password": "nope"})
        r = self.client.post(self.url, {"password": PASSWORD})  # даже верный блокируется
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Слишком много", status_code=429)
        self.assertFalse(Room.objects.exists())

    def test_rate_limit_expires(self):
        session = self.client.session
        session["teacher_fails"] = [0.0] * 5  # старые попытки (1970 год)
        session.save()
        r = self.client.post(self.url, {"password": PASSWORD})
        self.assertEqual(r.status_code, 302)

    def test_returning_teacher_skips_password(self):
        self.client.post(self.url, {"password": PASSWORD})
        room = Room.objects.get()
        r = self.client.get(self.url)
        self.assertRedirects(r, reverse("teacher", args=[room.code]))
        self.assertEqual(Room.objects.count(), 1)

    def test_finished_room_asks_password_again(self):
        self.client.post(self.url, {"password": PASSWORD})
        Room.objects.update(status=Room.Status.FINISHED)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Пароль агая")

    def test_teacher_panel_requires_session_token(self):
        room = Room.objects.create(code="1234", teacher_token="secret")
        r = self.client.get(reverse("teacher", args=[room.code]))
        self.assertRedirects(r, self.url)

    @override_settings(TEACHER_PASSWORD="")
    def test_empty_password_setting_blocks_login(self):
        r = self.client.post(self.url, {"password": ""})
        self.assertContains(r, "не настроен")
        self.assertFalse(Room.objects.exists())


class JoinTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(code="4321", teacher_token="t")
        self.url = reverse("join")

    def join(self, name, code=None, client=None):
        return (client or self.client).post(self.url, {"name": name, "code": code or self.room.code})

    def test_join_ok(self):
        r = self.join("  Айдар  ")
        self.assertRedirects(r, reverse("room", args=[self.room.code]))
        self.assertEqual(Student.objects.get().name, "Айдар")
        page = self.client.get(reverse("room", args=[self.room.code]))
        # урок идёт сразу: никакого «ждём агая», сразу кнопка «Записал»
        self.assertContains(page, "Записал")
        self.assertNotContains(page, "Ждём, когда агай")
        self.assertEqual(page.context["state"]["status"], "active")

    def test_name_unique_case_and_spaces(self):
        self.join("Айдар")
        other = self.client_class()
        r = self.join("  айДАР ", client=other)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Имя занято, добавьте фамилию")
        self.assertEqual(Student.objects.count(), 1)

    def test_same_name_in_other_room_ok(self):
        Room.objects.create(code="5555", teacher_token="t2")
        self.join("Айдар")
        r = self.join("Айдар", code="5555", client=self.client_class())
        self.assertEqual(r.status_code, 302)

    def test_name_length(self):
        self.assertContains(self.join("А"), "от 2 до 40")
        self.assertContains(self.join("Б" * 41), "от 2 до 40")
        self.assertFalse(Student.objects.exists())
        r = self.join("Токтогулова Айпери Бекмаматовна")  # 31 символ — теперь можно
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Student.objects.get().name, "Токтогулова Айпери Бекмаматовна")

    def test_single_full_name_field(self):
        r = self.client.get(self.url)
        self.assertContains(r, "Фамилия и имя")
        self.assertContains(r, 'placeholder="Иванов Иван"')
        self.assertContains(r, 'maxlength="40"')

    def test_full_name_unique_case_insensitive(self):
        self.join("Иванов Иван")
        r = self.join("  иванов   ИВАН ", client=self.client_class())  # пробелы схлопываются
        self.assertContains(r, "Имя занято")
        self.assertEqual(Student.objects.count(), 1)
        r = self.join("Иванов Пётр", client=self.client_class())  # другое имя — можно
        self.assertEqual(r.status_code, 302)

    def test_wrong_code(self):
        r = self.join("Айдар", code="999999")
        self.assertContains(r, "Урок не найден")

    def test_finished_room_code(self):
        Room.objects.filter(pk=self.room.pk).update(status=Room.Status.FINISHED)
        r = self.join("Айдар")
        self.assertContains(r, "Урок не найден")

    def test_reconnect_by_token(self):
        self.join("Айдар")
        student = Student.objects.get()
        self.assertEqual(self.client.cookies[STUDENT_TOKEN_COOKIE].value, student.token)
        # снова тот же браузер (тот же токен) — тот же студент, имя не «занято»
        r = self.join("Айдар")
        self.assertRedirects(r, reverse("room", args=[self.room.code]))
        self.assertEqual(Student.objects.count(), 1)
        # и прямой заход в комнату пускает
        self.assertEqual(self.client.get(reverse("room", args=[self.room.code])).status_code, 200)

    def test_other_browser_cannot_take_name(self):
        self.join("Айдар")
        r = self.join("Айдар", client=self.client_class())
        self.assertContains(r, "Имя занято")

    def test_name_prefilled_from_cookie(self):
        self.join("Айдар Асанов")
        r = self.client.get(self.url)
        self.assertContains(r, 'value="Айдар Асанов"')

    def test_join_csrf_required(self):
        c = self.client_class(enforce_csrf_checks=True)
        r = c.post(self.url, {"name": "Айдар", "code": self.room.code})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Student.objects.exists())

    def test_name_with_html_is_escaped(self):
        for name in ("<img src=x onerror=alert(1)>", "<b>жир</b>"):
            client = self.client_class()
            self.join(name, client=client)
            page = client.get(reverse("room", args=[self.room.code]))
            self.assertNotContains(page, name)  # сырого HTML на странице нет
            self.assertContains(page, name.replace("<", "&lt;").replace(">", "&gt;"))

    def test_empty_name_and_wrong_code(self):
        self.assertContains(self.join(""), "Введите фамилию и имя")
        r = self.client.post(self.url, {"name": "Айдар", "code": ""})
        self.assertContains(r, "Введите код урока")
        self.assertContains(self.join("Айдар", code="12ab"), "Урок не найден")
        self.assertFalse(Student.objects.exists())

    def test_room_without_token_redirects_to_join(self):
        r = self.client.get(reverse("room", args=[self.room.code]))
        self.assertRedirects(r, f"{self.url}?code={self.room.code}")


class ModelTests(TestCase):
    def test_mark_unique_per_round(self):
        room = Room.objects.create(code="1111", teacher_token="t")
        s = Student.objects.create(room=room, name="Айдар", token="x")
        Mark.objects.create(student=s, round=1)
        Mark.objects.create(student=s, round=2)
        with self.assertRaises(IntegrityError):
            Mark.objects.create(student=s, round=1)
