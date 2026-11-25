from django.db import migrations
from django.utils import timezone

from attendance import co_holidays


def set_colombia_holidays(apps, schema_editor):
    """Reemplaza los festivos iniciales de Pakistán por los festivos oficiales de Colombia (2026 y próximos años)."""
    Holiday = apps.get_model("attendance", "Holiday")
    Holiday.objects.filter(name__in=co_holidays.LEGACY_PK_HOLIDAY_NAMES).delete()
    end_year = max(2031, timezone.localdate().year + 4)
    co_holidays.seed_default_holidays(Holiday, start_year=2026, end_year=end_year)


class Migration(migrations.Migration):
    dependencies = [("attendance", "0009_alter_correction_options_alter_employee_options_and_more")]

    operations = [migrations.RunPython(set_colombia_holidays, migrations.RunPython.noop)]
