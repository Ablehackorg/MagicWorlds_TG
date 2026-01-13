from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

class Command(BaseCommand):
    help = "Создание ролей пользователей"

    def handle(self, *args, **kwargs):
        for role in ["moderator", "advertiser"]:
            group, created = Group.objects.get_or_create(name=role)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Роль '{role}' создана"))
            else:
                self.stdout.write(f"Роль '{role}' уже существует")

