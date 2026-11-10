from django.conf import settings
from django.db import models
from django.utils import timezone

NAME_MIN, NAME_MAX = 2, 40  # «Фамилия Имя» одним полем


def default_auto_next_seconds():
    """Задержка авто-раунда новой комнаты: settings.AUTO_NEXT_SECONDS (в тестах — доли секунды)."""
    return getattr(settings, "AUTO_NEXT_SECONDS", 3)


class Room(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "Ожидание"  # устарело: урок начинается сразу, старые комнаты → active
        ACTIVE = "active", "Идёт урок"
        FINISHED = "finished", "Завершён"

    code = models.CharField("код", max_length=6, unique=True)
    teacher_token = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    round = models.PositiveIntegerField("раунд", default=1)
    # начало текущего раунда: «Когда» у записавших = Mark.created_at − это время
    round_started_at = models.DateTimeField(default=timezone.now)
    timer_seconds = models.PositiveIntegerField(null=True, blank=True)  # null = без таймера
    # все записали → новый раунд через N с (агай меняет в меню «Авто-раунд»; из панели — целые 1–60)
    auto_next_enabled = models.BooleanField(default=True)
    auto_next_seconds = models.FloatField(default=default_auto_next_seconds)
    # «Внимание»: агай позвал всех посмотреть на доску; null — режим выключен
    attention_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "комната"
        verbose_name_plural = "комнаты"

    def __str__(self):
        return f"{self.code} ({self.status})"


class Student(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="students")
    name = models.CharField("фамилия и имя", max_length=NAME_MAX)
    token = models.CharField(max_length=64)
    is_online = models.BooleanField(default=False)  # есть открытый сокет (по счётчику соединений)
    last_seen = models.DateTimeField(null=True, blank=True)  # последний ping/действие; онлайн = свежий
    # «Я вышел»: не считается в «Записали X / Y» и не мешает авто-раунду; null — на уроке
    away_since = models.DateTimeField(null=True, blank=True)
    # «✋ Вопрос»: тип открытого вопроса и когда задан (очередь). Новый раунд вопрос не снимает;
    # снимают «Разобрался» (студент), «Ответил» (агай), «Я вышел» и удаление.
    QUESTION_TYPES = (("question", "Вопрос"), ("bug", "Ошибка в коде"))
    question_type = models.CharField(max_length=10, choices=QUESTION_TYPES, null=True, blank=True)
    question_at = models.DateTimeField(null=True, blank=True)
    # «Я слушаю» на текущее «Внимание»; сбрасывается у всех при новом «Внимании»
    attention_ack_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "студент"
        verbose_name_plural = "студенты"
        ordering = ["joined_at", "id"]
        # Уникальность имени без учёта регистра проверяется в services.join_room:
        # SQLite lower() не работает с кириллицей, поэтому DB-констрейнт тут бесполезен.
        constraints = [
            models.UniqueConstraint(fields=["room", "token"], name="uniq_student_token_in_room"),
        ]

    def __str__(self):
        return self.name


class Mark(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="marks")
    round = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "отметка"
        verbose_name_plural = "отметки"
        constraints = [
            models.UniqueConstraint(fields=["student", "round"], name="uniq_mark_student_round"),
        ]
