from django.db import models


class Room(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "Ожидание"
        ACTIVE = "active", "Идёт урок"
        FINISHED = "finished", "Завершён"

    code = models.CharField("код", max_length=6, unique=True)
    teacher_token = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)
    round = models.PositiveIntegerField("раунд", default=1)
    timer_seconds = models.PositiveIntegerField(null=True, blank=True)  # null = без таймера
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
