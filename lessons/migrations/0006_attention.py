from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0005_student_question"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="attention_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="student",
            name="attention_ack_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
