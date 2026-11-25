"""Motor de cálculo de jornada laboral colombiana, recargos y trabajo suplementario.

Implementa:
- Regla combinada (Art. 159 CST): reconoce el exceso sobre la jornada diaria
  pactada y controla el límite semanal sin contar dos veces una misma hora.
- Separación ortogonal entre jornada (ordinaria vs. extra), naturaleza del día
  (normal vs. descanso obligatorio/festivo) y franja horaria (diurna vs. nocturna).
- Alertas sin bloqueo cuando las horas extras superan los límites configurados
  (p. ej. 2 h diarias / 12 h semanales).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from . import dates
from .models import (
    DailyAttendanceSummary,
    Holiday,
    LaborRuleConfig,
    LeaveRequest,
    RequestStatus,
    TimeEntry,
    TimeEntryBreakdown,
)

BUCKET_FIELDS = (
    "ordinary_day_minutes",
    "ordinary_night_minutes",
    "ordinary_holiday_day_minutes",
    "ordinary_holiday_night_minutes",
    "overtime_day_minutes",
    "overtime_night_minutes",
    "overtime_holiday_day_minutes",
    "overtime_holiday_night_minutes",
)


def _round_seconds_map(seconds_by_bucket):
    """Convierte segundos de las 8 cubetas a minutos enteros preservando la suma total."""
    total_minutes = round(sum(seconds_by_bucket.values()) / 60)
    raw = {key: seconds_by_bucket.get(key, 0.0) / 60.0 for key in BUCKET_FIELDS}
    floors = {key: int(val) for key, val in raw.items()}
    diff = total_minutes - sum(floors.values())
    if diff > 0:
        remainders = sorted(BUCKET_FIELDS, key=lambda key: raw[key] - floors[key], reverse=True)
        for key in remainders[:diff]:
            floors[key] += 1
    return floors


def _is_night(local_time, night_start, night_end):
    if night_start > night_end:
        return local_time >= night_start or local_time < night_end
    return night_start <= local_time < night_end


def _split_by_clock_and_day(start_utc, end_utc, night_start, night_end, holidays):
    """Divide [start_utc, end_utc) en tramos homogéneos según franja diurna/nocturna y festivo/domingo."""
    tz = timezone.get_current_timezone()
    cursor = timezone.localtime(start_utc, tz)
    end_local = timezone.localtime(end_utc, tz)
    segments = []

    while cursor < end_local:
        day = cursor.date()
        # Próximos cortes locales relevantes: cambio de franja y medianoche.
        candidates = [timezone.make_aware(datetime.combine(day + dates.ONE_DAY, time.min), tz)]
        for boundary in (night_start, night_end):
            candidate = timezone.make_aware(datetime.combine(day, boundary), tz)
            if candidate > cursor:
                candidates.append(candidate)
        next_cut = min(end_local, *candidates)
        seconds = (next_cut - cursor).total_seconds()
        if seconds > 0:
            is_night = _is_night(cursor.time(), night_start, night_end)
            is_rest = (day.weekday() == 6) or (day in holidays)
            segments.append((seconds, is_night, is_rest))
        cursor = next_cut

    return segments


def _holiday_dates(start, end):
    """Conjunto de fechas festivas entre `start` y `end`, expandiendo los festivos anuales."""
    found = set(Holiday.objects.filter(recurs_yearly=False, date__range=(start, end)).values_list("date", flat=True))
    for holiday in Holiday.objects.filter(recurs_yearly=True, date__lte=end):
        for year in range(max(start.year, holiday.date.year), end.year + 1):
            try:
                day = holiday.date.replace(year=year)
            except ValueError:
                continue
            if start <= day <= end:
                found.add(day)
    return found


def _leave_by_day(employee, start, end):
    leaves = LeaveRequest.objects.filter(
        employee=employee, status=RequestStatus.APPROVED, start_date__lte=end, end_date__gte=start
    )
    days = {}
    for leave in leaves:
        day = max(leave.start_date, start)
        while day <= min(leave.end_date, end):
            days[day] = leave
            day += dates.ONE_DAY
    return days


@dataclass
class DayBreakdown:
    date: object
    schedule_name: str
    daily_target_minutes: int
    is_rest_or_holiday: bool
    is_holiday: bool
    is_sunday: bool
    buckets: dict = field(default_factory=lambda: {key: 0 for key in BUCKET_FIELDS})
    leave_minutes: int = 0
    leave_type: str = ""
    daily_overtime_exceeded: bool = False
    daily_overtime_excess_minutes: int = 0
    weekly_overtime_exceeded: bool = False
    weekly_overtime_excess_minutes: int = 0

    @property
    def total_ordinary_minutes(self):
        return sum(self.buckets[k] for k in BUCKET_FIELDS[:4])

    @property
    def total_overtime_minutes(self):
        return sum(self.buckets[k] for k in BUCKET_FIELDS[4:])

    @property
    def total_worked_minutes(self):
        return self.total_ordinary_minutes + self.total_overtime_minutes


@dataclass
class WeekCalculation:
    monday: object
    rule: LaborRuleConfig
    days: dict  # date -> DayBreakdown
    entry_breakdowns: dict  # entry_id -> (work_date, dict_of_buckets, total_ordinary, total_overtime, total_worked)
    weekly_overtime_minutes: int
    weekly_overtime_exceeded: bool
    weekly_overtime_excess_minutes: int


def _day_ordinary_cap_seconds(terms, day, rule):
    if terms is None:
        return float(rule.max_daily_hours) * 3600.0
    if day.weekday() in terms.working_weekdays:
        return float(terms.hours_for_weekday(day.weekday())) * 3600.0
    # Si trabaja en un día que no está en su horario habitual, el tope diario ordinario
    # corresponde al promedio diario del horario, acotado al máximo legal diario.
    avg = terms.daily_target.total_seconds()
    return min(avg, float(rule.max_daily_hours) * 3600.0)


def calculate_week(employee, monday, holidays=None, include_open=False, now=None):
    """Calcula el desglose completo de la semana de lunes a domingo para `employee`."""
    monday = dates.week_start(monday)
    sunday = monday + timedelta(days=6)
    rule = LaborRuleConfig.for_date(monday)
    if holidays is None:
        # Incluye el lunes siguiente por si un turno del domingo cruza la medianoche hacia festivo.
        holidays = _holiday_dates(monday, sunday + dates.ONE_DAY)
    leaves = _leave_by_day(employee, monday, sunday)
    terms = employee.terms_on(monday)

    entries_qs = TimeEntry.objects.filter(
        employee=employee,
        clock_in__gte=dates.day_start(monday),
        clock_in__lt=dates.day_start(sunday + dates.ONE_DAY),
        missing_out=False,
    ).order_by("clock_in")

    entries_by_day = defaultdict(list)
    for entry in entries_qs:
        if entry.clock_out is None and not include_open:
            continue
        entries_by_day[dates.work_date(entry.clock_in)].append(entry)

    # Tope semanal ordinario pactado (no excede max_weekly_hours de la regla).
    if terms is not None:
        base_weekly_hours = min(terms.weekly_hours, rule.max_weekly_hours)
        base_weekly_seconds = float(base_weekly_hours) * 3600.0
    else:
        base_weekly_seconds = float(rule.max_weekly_hours) * 3600.0

    # Días laborables acreditados (permiso aprobado o festivo sin trabajo registrado) reducen el cupo semanal.
    credited_seconds = 0.0
    for offset in range(7):
        day = monday + timedelta(days=offset)
        if day < employee.start_date:
            continue
        is_working = day.weekday() in (terms.working_weekdays if terms else range(5))
        if not is_working:
            continue
        if day in leaves or (day in holidays and not entries_by_day.get(day)):
            if terms is not None:
                credited_seconds += float(terms.hours_for_weekday(day.weekday())) * 3600.0
            else:
                credited_seconds += base_weekly_seconds / 5.0

    week_ordinary_remaining = max(0.0, base_weekly_seconds - credited_seconds)

    days_map = {}
    entry_breakdowns = {}
    now = now or timezone.now()

    for offset in range(7):
        day = monday + timedelta(days=offset)
        day_entries = entries_by_day.get(day, [])
        is_holiday = day in holidays
        is_sunday = day.weekday() == 6
        is_rest = is_holiday or is_sunday

        if terms is not None and day >= employee.start_date and day.weekday() in terms.working_weekdays:
            target_minutes = round(float(terms.hours_for_weekday(day.weekday())) * 60)
        else:
            target_minutes = 0

        day_ordinary_remaining = _day_ordinary_cap_seconds(terms, day, rule) if day >= employee.start_date else 0.0
        day_seconds = {key: 0.0 for key in BUCKET_FIELDS}

        for entry in day_entries:
            end_moment = entry.clock_out or max(now, entry.clock_in)
            if end_moment <= entry.clock_in:
                continue
            entry_seconds = {key: 0.0 for key in BUCKET_FIELDS}
            for seg_seconds, is_night, seg_is_rest in _split_by_clock_and_day(
                entry.clock_in, end_moment, rule.night_start, rule.night_end, holidays
            ):
                ord_seconds = min(seg_seconds, day_ordinary_remaining, week_ordinary_remaining)
                ot_seconds = seg_seconds - ord_seconds

                if ord_seconds > 0:
                    day_ordinary_remaining -= ord_seconds
                    week_ordinary_remaining -= ord_seconds
                    if seg_is_rest:
                        key = "ordinary_holiday_night_minutes" if is_night else "ordinary_holiday_day_minutes"
                    else:
                        key = "ordinary_night_minutes" if is_night else "ordinary_day_minutes"
                    entry_seconds[key] += ord_seconds
                    day_seconds[key] += ord_seconds

                if ot_seconds > 0:
                    if seg_is_rest:
                        key = "overtime_holiday_night_minutes" if is_night else "overtime_holiday_day_minutes"
                    else:
                        key = "overtime_night_minutes" if is_night else "overtime_day_minutes"
                    entry_seconds[key] += ot_seconds
                    day_seconds[key] += ot_seconds

            rounded_entry = _round_seconds_map(entry_seconds)
            ord_min = sum(rounded_entry[k] for k in BUCKET_FIELDS[:4])
            ot_min = sum(rounded_entry[k] for k in BUCKET_FIELDS[4:])
            entry_breakdowns[entry.pk] = (day, rounded_entry, ord_min, ot_min, ord_min + ot_min)

        rounded_day = _round_seconds_map(day_seconds)
        day_ot_minutes = sum(rounded_day[k] for k in BUCKET_FIELDS[4:])
        max_daily_ot_min = round(float(rule.max_daily_overtime_hours) * 60)
        daily_exceeded = day_ot_minutes > max_daily_ot_min
        daily_excess = max(0, day_ot_minutes - max_daily_ot_min)

        leave = leaves.get(day)
        leave_minutes = target_minutes if (leave and not is_holiday) else 0

        days_map[day] = DayBreakdown(
            date=day,
            schedule_name=terms.name if terms else "",
            daily_target_minutes=target_minutes,
            is_rest_or_holiday=is_rest,
            is_holiday=is_holiday,
            is_sunday=is_sunday,
            buckets=rounded_day,
            leave_minutes=leave_minutes,
            leave_type=leave.leave_type if leave else "",
            daily_overtime_exceeded=daily_exceeded,
            daily_overtime_excess_minutes=daily_excess,
        )

    weekly_ot_minutes = sum(day_data.total_overtime_minutes for day_data in days_map.values())
    max_weekly_ot_min = round(float(rule.max_weekly_overtime_hours) * 60)
    weekly_exceeded = weekly_ot_minutes > max_weekly_ot_min
    weekly_excess = max(0, weekly_ot_minutes - max_weekly_ot_min)

    for day_data in days_map.values():
        if day_data.total_overtime_minutes > 0 or day_data.total_worked_minutes > 0:
            day_data.weekly_overtime_exceeded = weekly_exceeded
            day_data.weekly_overtime_excess_minutes = weekly_excess if weekly_exceeded else 0

    return WeekCalculation(
        monday=monday,
        rule=rule,
        days=days_map,
        entry_breakdowns=entry_breakdowns,
        weekly_overtime_minutes=weekly_ot_minutes,
        weekly_overtime_exceeded=weekly_exceeded,
        weekly_overtime_excess_minutes=weekly_excess,
    )


@transaction.atomic
def sync_week(employee, day_in_week):
    """Recalcula y persiste `TimeEntryBreakdown` y `DailyAttendanceSummary` para una semana."""
    if employee.is_staff:
        return None
    monday = dates.week_start(day_in_week)
    sunday = monday + timedelta(days=6)
    calc = calculate_week(employee, monday, include_open=False)

    # Limpia desgloses de marcaciones cerradas o eliminadas/sin salida de esa semana.
    week_entries = TimeEntry.objects.filter(
        employee=employee,
        clock_in__gte=dates.day_start(monday),
        clock_in__lt=dates.day_start(sunday + dates.ONE_DAY),
    ).values_list("pk", flat=True)
    TimeEntryBreakdown.objects.filter(entry_id__in=week_entries).exclude(
        entry_id__in=calc.entry_breakdowns.keys()
    ).delete()

    for entry_id, (w_date, buckets, ord_min, ot_min, tot_min) in calc.entry_breakdowns.items():
        TimeEntryBreakdown.objects.update_or_create(
            entry_id=entry_id,
            defaults={
                "employee": employee,
                "work_date": w_date,
                **buckets,
                "total_ordinary_minutes": ord_min,
                "total_overtime_minutes": ot_min,
                "total_worked_minutes": tot_min,
            },
        )

    for day, data in calc.days.items():
        if data.total_worked_minutes == 0 and data.leave_minutes == 0:
            DailyAttendanceSummary.objects.filter(employee=employee, date=day).delete()
            continue
        DailyAttendanceSummary.objects.update_or_create(
            employee=employee,
            date=day,
            defaults={
                "week_start": monday,
                "schedule_name": data.schedule_name,
                "daily_target_minutes": data.daily_target_minutes,
                "is_rest_or_holiday": data.is_rest_or_holiday,
                "is_holiday": data.is_holiday,
                "is_sunday": data.is_sunday,
                **data.buckets,
                "total_ordinary_minutes": data.total_ordinary_minutes,
                "total_overtime_minutes": data.total_overtime_minutes,
                "total_worked_minutes": data.total_worked_minutes,
                "leave_minutes": data.leave_minutes,
                "leave_type": data.leave_type,
                "daily_overtime_exceeded": data.daily_overtime_exceeded,
                "daily_overtime_excess_minutes": data.daily_overtime_excess_minutes,
                "weekly_overtime_exceeded": data.weekly_overtime_exceeded,
                "weekly_overtime_excess_minutes": data.weekly_overtime_excess_minutes,
            },
        )
    return calc


def sync_range(employee, start, end):
    """Recalcula todas las semanas entre `start` y `end` para un empleado."""
    monday = dates.week_start(start)
    last_monday = dates.week_start(end)
    while monday <= last_monday:
        sync_week(employee, monday)
        monday += timedelta(days=7)
