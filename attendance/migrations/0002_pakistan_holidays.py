from django.db import migrations


def add_holidays(apps, schema_editor):
    """Migración histórica reemplazada por 0010_colombia_holidays."""


class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]

    operations = [migrations.RunPython(add_holidays, migrations.RunPython.noop)]
