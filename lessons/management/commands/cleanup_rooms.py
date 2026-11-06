from django.core.management.base import BaseCommand

from lessons import services


class Command(BaseCommand):
    help = "Удаляет комнаты старше 12 часов вместе со студентами и отметками."

    def handle(self, *args, **options):
        deleted = services.cleanup_old_rooms()
        self.stdout.write(f"Удалено объектов: {deleted}")
