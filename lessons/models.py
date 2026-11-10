from django.conf import settings
from django.db import models


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
    timer_seconds = models.PositiveIntegerField(null=True, blank=True)  # null = без таймера
    # все записали → новый раунд через N с (агай меняет в меню «Авто-раунд»; из панели — целые 1–60)
    auto_next_enabled = models.BooleanField(default=True)
    auto_next_seconds = models.FloatField(default=default_auto_next_seconds)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "комната"
        verbose_name_plural = "комнаты"

    def __str__(self):
        return f"{self.code} ({self.status})"


class Student(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="students")
    name = models.CharField("имя", max_length=30)
    token = models.CharField(max_length=64)
    is_online = models.BooleanField(default=False)  # есть открытый сокет (по счётчику соединений)
    last_seen = models.DateTimeField(null=True, blank=True)  # последний ping/действие; онлайн = свежий
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
