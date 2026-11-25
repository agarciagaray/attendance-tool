import os

from django.core.management.base import BaseCommand

from attendance.models import Employee


class Command(BaseCommand):
    help = "Crea el primer administrador a partir de ADMIN_USERNAME y ADMIN_PASSWORD si aún no existe."

    def handle(self, *args, **options):
        if Employee.objects.filter(is_superuser=True).exists():
            return
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if not username or not password:
            self.stdout.write(
                "Aún no hay cuenta de administrador. Configura ADMIN_USERNAME y ADMIN_PASSWORD, "
                "o ejecuta: python manage.py createsuperuser"
            )
            return
        Employee.objects.create_superuser(username=username, password=password)
        self.stdout.write(self.style.SUCCESS(f"Cuenta de administrador '{username}' creada."))
