"""Días y semanas de asistencia en la zona horaria de la empresa."""

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format

ONE_DAY = timedelta(days=1)

# Un lunes muy anterior a cualquier dato real. Los horarios y asignaciones que
# aplican "desde siempre" se fechan desde aquí.
BEGINNING = date(2000, 1, 3)


def _rollover():
    return timedelta(hours=settings.DAY_ROLLOVER_HOUR)


def work_date(moment):
    """El día de asistencia al que pertenece un momento, en la zona horaria de la empresa."""
    return (timezone.localtime(moment) - _rollover()).date()


def day_start(day):
    """El momento en que comienza un día de asistencia."""
    return timezone.make_aware(datetime.combine(day, time.min) + _rollover())


def today(now=None):
    return work_date(now or timezone.now())


def week_start(day):
    """El lunes de la semana en que cae un día."""
    return day - timedelta(days=day.weekday())


def date_text(value, fmt="D j M Y"):
    """Una fecha o momento formateado en español en la zona horaria de la empresa."""
    if isinstance(value, datetime):
        value = timezone.localtime(value)
    return date_format(value, fmt)


def clock_text(moment):
    """Un momento como hora del día en formato de 12 horas, p. ej. "9:05 AM"."""
    return timezone.localtime(moment).strftime("%I:%M %p").lstrip("0")


def duration_text(length):
    """Una duración en horas y minutos, p. ej. "7h 05m"."""
    minutes = int(max(length, timedelta(0)).total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"
