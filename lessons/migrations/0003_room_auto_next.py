from django.db import migrations, models

import lessons.models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0002_student_last_seen"),
    ]

    operations = [
        migrations.AlterField(
            model_name="room",
            name="status",
            field=models.CharField(
                choices=[("waiting", "Ожидание"), ("active", "Идёт урок"), ("finished", "Завершён")],
                default="active",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="room",
            name="auto_next_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="room",
            name="auto_next_seconds",
            field=models.FloatField(default=lessons.models.default_auto_next_seconds),
        ),
    ]
