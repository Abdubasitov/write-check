import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0003_room_auto_next"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="round_started_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="student",
            name="away_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="student",
            name="name",
            field=models.CharField(max_length=40, verbose_name="фамилия и имя"),
        ),
    ]
