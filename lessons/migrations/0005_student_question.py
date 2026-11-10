from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0004_round_started_away"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="question_type",
            field=models.CharField(
                blank=True, choices=[("question", "Вопрос"), ("bug", "Ошибка в коде")], max_length=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="question_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
