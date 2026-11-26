"""Páginas para administradores: revisar asistencia, decidir solicitudes y configurar el sistema."""

import calendar
import csv
from collections import Counter
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, ProtectedError, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import co_holidays, erp, lockout, notifications, services
from .dates import clock_text, date_text
from .forms import (
    EmployeeCreateForm,
    EmployeeForm,
    EntryForm,
    HolidayForm,
    LaborRuleConfigForm,
    LeaveAllowanceForm,
    RemoveEntryForm,
    ResetPasswordForm,
    ReviewFilterForm,
    ScheduleForm,
    TestEmailForm,
    TimerCheckForm,
)
from .middleware import SIGNED_IN_AS
from .models import (
    Employee,
    Holiday,
    LaborRuleConfig,
    LeaveAllowance,
    LeaveRequest,
    MissingTimeRequest,
    RequestStatus,
    Schedule,
    TimeEntry,
    TimerCheck,
)
from .navigation import crumb
from .views import get_request_or_404, month_context, parse_month

MAX_REVIEW_DAYS = 371  # 53 semanas
COUNTED_STATES = ("met", "short", "progress")
SCHEDULE_HISTORY = "assignments__schedule__versions"  # todo lo necesario para calcular el horario de cualquiera


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def tracked_employees():
    """Personas activas cuya asistencia se registra. Los administradores no marcan entrada."""
    return Employee.objects.filter(is_active=True, is_staff=False).prefetch_related(SCHEDULE_HISTORY)


def form_page(request, form, title, cancel_url, submit_label="Guardar", intro="", danger=False, trail=()):
    """Una página que contiene solo un formulario. `trail` son los pasos de migas de pan bajo la sección."""
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": title,
            "intro": intro,
            "submit_label": submit_label,
            "cancel_url": cancel_url,
            "danger": danger,
            "breadcrumbs": list(trail),
        },
    )


def person_crumb(employee, query=""):
    """Paso de miga de pan para un empleado. Los administradores no tienen página de asistencia propia."""
    if employee.is_staff:
        return crumb(employee.display_name)
    return crumb(employee.display_name, "manage_employee_detail", employee.pk, query=query)


# --- Panel principal --------------------------------------------------------


def _holiday_notices(current):
    notices = []
    soon = services.holiday_occurrences(current, current + timedelta(days=45))
    tentative = sorted({holiday.name for _, holiday in soon if holiday.tentative})
    if tentative:
        notices.append(
            f"{', '.join(tentative)}: las fechas siguen marcadas como tentativas. "
            "Confírmalas en la página de Festivos cuando estén definidas."
        )
    for year in sorted({current.year, (current + timedelta(days=60)).year}):
        if not Holiday.objects.filter(recurs_yearly=False, date__year=year).exists():
            notices.append(
                f"No hay festivos variables cargados para {year}. Agrégalos en la página de Festivos."
            )
    return notices


@admin_required
def dashboard(request):
    now = timezone.now()
    current = services.today(now)
    first = services.week_start(current)
    holidays = services.holiday_map(first, first + timedelta(days=6))
    rows = []
    for employee in tracked_employees():
        week = services.build_weeks(employee, current, current, now=now, holidays=holidays)[0]
        day = next(day for day in week.days if day.date == current)
        open_entry = next((entry for entry in day.entries if entry.is_open), None)
        last_entry = day.entries[-1] if day.entries else None
        rows.append(
            {"employee": employee, "week": week, "day": day, "open_entry": open_entry, "last_entry": last_entry}
        )
    timeline = services.build_timeline(
        [
            (row["employee"].display_name, reverse("manage_employee_detail", args=[row["employee"].pk]), row["day"])
            for row in rows
        ],
        now=now,
    )
    # Los nombres completos de clases se escriben explícitos para que Tailwind los detecte.
    for row, bar in zip(rows, timeline.rows):
        if row["open_entry"]:
            bar.note = f"Entrada · {services.clock_text(row['open_entry'].clock_in)}"
            bar.note_style = "text-emerald-700"
        elif row["last_entry"] and row["last_entry"].clock_out:
            bar.note = f"Salida · {services.clock_text(row['last_entry'].clock_out)}"
            bar.note_style = "text-slate-500"
        elif row["day"].status == "today":
            bar.note, bar.note_style = "Sin entrada aún", "text-amber-700"
        else:
            bar.note, bar.note_style = row["day"].label, "text-slate-500"
    return render(
        request,
        "attendance/manage/dashboard.html",
        {
            "today": current,
            "rows": rows,
            "timeline": timeline,
            "clocked_in": sum(1 for row in rows if row["open_entry"]),
            "notices": _holiday_notices(current),
        },
    )


# --- Revisión y exportación -------------------------------------------------


def _review(request):
    """Datos filtrados compartidos entre la página de revisión y ambas exportaciones."""
    current = services.today()
    start, end, employee = current.replace(day=1), current, None
    form = ReviewFilterForm(
        request.GET if "start" in request.GET else None, initial={"start": start, "end": end}
    )
    if form.is_bound and form.is_valid():
        start, end, employee = (form.cleaned_data[key] for key in ("start", "end", "employee"))
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_REVIEW_DAYS:
        end = start + timedelta(days=MAX_REVIEW_DAYS)
        messages.warning(request, "El rango se acortó a 53 semanas.")

    employees = [employee] if employee else tracked_employees()
    first = services.week_start(start)
    last = services.week_start(end) + timedelta(days=6)
    holidays = services.holiday_map(first, last)

    rows = []
    for person in employees:
        weeks = services.build_weeks(person, start, end, holidays=holidays)
        counted = [week for week in weeks if week.state in COUNTED_STATES]
        rows.append(
            {
                "employee": person,
                "weeks": weeks,
                "worked": sum((week.worked for week in weeks), timedelta(0)),
                "target": sum((week.target for week in counted), timedelta(0)) if counted else None,
                "met": sum(1 for week in weeks if week.state == "met"),
                "short": sum(1 for week in weeks if week.state == "short"),
            }
        )
    week_starts = [first + timedelta(weeks=index) for index in range((last - first).days // 7 + 1)]
    return {"form": form, "start": first, "end": last, "rows": rows, "week_starts": week_starts}


@admin_required
def review(request):
    context = _review(request)
    context["query"] = request.GET.urlencode()
    return render(request, "attendance/manage/review.html", context)


def _hours(value):
    return f"{value.total_seconds() / 3600:.2f}"


def _clock(moment):
    return clock_text(moment) if moment else ""


def _csv_safe(value):
    """Evita que las hojas de cálculo ejecuten texto escrito por empleados como una fórmula."""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")  # BOM para que Excel reconozca UTF-8
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


@admin_required
def export_daily(request):
    data = _review(request)
    rows = []
    for row in data["rows"]:
        employee = row["employee"]
        for week in row["weeks"]:
            for day in week.days:
                if day.status in ("before", "future", "none"):
                    continue
                punches = "; ".join(
                    f"{_clock(entry.clock_in)}-{_clock(entry.clock_out) or '?'}" for entry in day.entries
                )
                reasons = "; ".join(
                    entry.note or entry.request.reason for entry in day.entries if entry.note or entry.request
                )
                rows.append(
                    [
                        _csv_safe(employee.display_name),
                        _csv_safe(employee.username),
                        day.date.isoformat(),
                        date_text(day.date, "D"),
                        "Sin entrada aún" if day.status == "today" else _csv_safe(day.label),
                        _hours(day.worked),
                        punches,
                        _csv_safe(reasons),
                        _csv_safe("; ".join(entry.memo for entry in day.entries if entry.memo)),
                    ]
                )
    header = ["Empleado", "Usuario", "Fecha", "Día", "Estado", "Horas", "Marcaciones", "Motivo de ajustes manuales", "Notas"]
    return _csv_response(f"asistencia-diaria-{data['start']}-a-{data['end']}.csv", header, rows)


@admin_required
def export_weekly(request):
    results = {
        "met": "Completada",
        "short": "Incompleta",
        "progress": "En curso",
        "future": "Próxima",
        "untracked": "Sin meta",
    }
    data = _review(request)
    rows = []
    for row in data["rows"]:
        employee = row["employee"]
        for week in row["weeks"]:
            if week.state == "before":
                continue
            rows.append(
                [
                    _csv_safe(employee.display_name),
                    _csv_safe(employee.username),
                    week.start.isoformat(),
                    week.end.isoformat(),
                    _hours(week.worked),
                    _hours(week.target) if week.target is not None else "",
                    _hours(week.difference) if week.target is not None else "",
                    results[week.state],
                ]
            )
    header = ["Empleado", "Usuario", "Inicio de semana", "Fin de semana", "Horas trabajadas", "Meta", "Diferencia", "Resultado"]
    return _csv_response(f"asistencia-semanal-{data['start']}-a-{data['end']}.csv", header, rows)


# --- Resumen mensual --------------------------------------------------------


def _monthly(request):
    """Una fila por empleado para un mes. Si el mes sigue en curso, se cuenta hasta ayer."""
    current = services.today()
    month = parse_month(request.GET.get("month"), current)
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    through = min(last, current - services.ONE_DAY)
    rows = []
    if through >= month:
        # Todos los activos, más cualquiera desactivado que tenga tiempo registrado en el mes.
        recorded = Q(
            entries__clock_in__gte=services.day_start(month),
            entries__clock_in__lt=services.day_start(through + services.ONE_DAY),
        )
        employees = (
            Employee.objects.filter(is_staff=False)
            .filter(Q(is_active=True) | recorded)
            .distinct()
            .prefetch_related(SCHEDULE_HISTORY)
        )
        holidays = services.holiday_map(month, through)
        rows = [services.month_summary(employee, month, through, holidays=holidays) for employee in employees]
    return {
        "month": month,
        "through": through,
        "complete": through == last,
        "rows": rows,
        "prev_month": (month - services.ONE_DAY).replace(day=1),
        "next_month": last + services.ONE_DAY,
    }


@admin_required
def monthly(request):
    context = _monthly(request)
    context["breadcrumbs"] = [crumb("Por mes")]
    return render(request, "attendance/manage/monthly.html", context)


@admin_required
def export_monthly(request):
    data = _monthly(request)
    rows = [
        [
            _csv_safe(row.employee.display_name),
            _csv_safe(row.employee.username),
            _csv_safe(row.terms.name) if row.terms else "",
            row.expected_days,
            row.present,
            row.missing,
            row.absent,
            row.leave,
            row.holidays,
            _hours(row.worked),
            _hours(row.target) if row.target is not None else "",
            _hours(row.difference) if row.target is not None else "",
        ]
        for row in data["rows"]
    ]
    header = [
        "Empleado",
        "Usuario",
        "Horario",
        "Días esperados",
        "Presente",
        "Sin salida",
        "Ausente",
        "Permisos",
        "Festivos",
        "Horas trabajadas",
        "Meta",
        "Diferencia",
    ]
    return _csv_response(f"asistencia-{data['month']:%Y-%m}.csv", header, rows)


# --- Solicitudes ------------------------------------------------------------


@admin_required
def requests_inbox(request):
    pending = RequestStatus.PENDING
    waiting = sorted(
        [
            *MissingTimeRequest.objects.filter(status=pending).select_related("employee", "entry"),
            *LeaveRequest.objects.filter(status=pending).select_related("employee"),
        ],
        key=lambda item: item.created_at,
    )
    allowances = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
    for item in waiting:
        if item.kind == "leave":
            item.days_used = len(services.working_leave_days(item.employee, item.start_date, item.end_date))
            item.balance = services.leave_balance_for(item, allowances)
    decided = sorted(
        [
            *MissingTimeRequest.objects.exclude(status=pending).select_related("employee", "decided_by")[:30],
            *LeaveRequest.objects.exclude(status=pending).select_related("employee", "decided_by")[:30],
        ],
        key=lambda item: item.decided_at or item.created_at,
        reverse=True,
    )
    return render(
        request,
        "attendance/manage/requests.html",
        {"pending": waiting, "decided": decided[:30]},
    )


@admin_required
@require_POST
def request_decide(request, kind, pk):
    item = get_request_or_404(kind, pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        decided = services.decide(item, request.user, approve, request.POST.get("note", ""))
    except ValidationError as error:
        messages.error(request, f"No se guardó. {' '.join(error.messages)}")
    else:
        notifications.request_decided(request, decided)
        messages.success(request, f"Solicitud {'aprobada' if approve else 'rechazada'}.")
    return redirect("manage_requests")


@admin_required
@require_POST
def leave_cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    try:
        cancelled = services.cancel(leave, by=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        notifications.request_decided(request, cancelled)
        messages.success(request, "Permiso cancelado.")
    return redirect("manage_requests")


# --- Empleados --------------------------------------------------------------


@admin_required
def employees(request):
    people = Employee.objects.prefetch_related(SCHEDULE_HISTORY).order_by("-is_active", "first_name", "username")
    locked = lockout.locked_usernames()
    for person in people:
        person.locked_until = locked.get(person.username.lower())
    return render(request, "attendance/manage/employees.html", {"employees": people})


@admin_required
def employee_new(request):
    form = EmployeeCreateForm(request.POST or None, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        messages.success(request, f"{employee.display_name} ya puede iniciar sesión como {employee.username}.")
        return redirect("manage_employees")
    return form_page(
        request, form, "Agregar empleado", reverse("manage_employees"), "Agregar empleado", trail=[crumb("Agregar empleado")]
    )


@admin_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Empleado guardado.")
        return redirect("manage_employees")
    return form_page(
        request,
        form,
        f"Editar a {employee.display_name}",
        reverse("manage_employees"),
        trail=[person_crumb(employee), crumb("Editar")],
    )


@admin_required
def employee_password(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = ResetPasswordForm(employee, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        lockout.clear(employee.username)
        if employee.pk == request.user.pk:
            update_session_auth_hash(request, employee)  # mantiene al administrador con la sesión abierta
        messages.success(request, f"Contraseña actualizada para {employee.display_name}.")
        return redirect("manage_employees")
    return form_page(
        request,
        form,
        f"Asignar nueva contraseña para {employee.display_name}",
        reverse("manage_employees"),
        "Asignar contraseña",
        trail=[person_crumb(employee), crumb("Asignar contraseña")],
    )


@admin_required
@require_POST
def sign_in_as(request, pk):
    """Muestra al administrador el sitio tal como lo ve este empleado. El banner en cada página permite volver."""
    employee = get_object_or_404(Employee, pk=pk, is_staff=False)
    if not employee.is_active:
        messages.error(request, f"{employee.display_name} está inactivo y no puede iniciar sesión.")
        return redirect("manage_employee_detail", pk=employee.pk)
    request.session[SIGNED_IN_AS] = employee.pk
    return redirect("home")


@admin_required
def employee_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    current = services.today()
    month = parse_month(request.GET.get("month"), current)
    context = month_context(employee, month)
    context.update(
        calendar_url=reverse("manage_employee_detail", args=[employee.pk]),
        leave_balances=services.leave_balances(employee, month.year),
        assignments=employee.assignment_list()[::-1],
        breadcrumbs=[crumb(employee.display_name)],
    )
    return render(request, "attendance/manage/employee_detail.html", context)


# --- Correcciones al registro -----------------------------------------------


def _parse_day(value, fallback):
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback
    return day if 2000 <= day.year <= 2100 else fallback


def _month_of(employee, moment):
    """Página del administrador para un empleado, abierta en el mes en que cae un momento."""
    month = services.work_date(moment).strftime("%Y-%m")
    return f"{reverse('manage_employee_detail', args=[employee.pk])}?month={month}"


def _save_entry(request, form, employee, entry=None):
    """Aplica un formulario válido de registro. Devuelve la corrección o None poniendo el error en el formulario."""
    try:
        correction = services.correct_entry(
            request.user,
            employee,
            form.cleaned_data["clock_in"],
            form.cleaned_data["clock_out"],
            form.cleaned_data["reason"],
            entry=entry,
        )
    except ValidationError as error:
        form.add_error(None, error)
        return None
    notifications.record_corrected(request, correction)
    return correction


@admin_required
def entry_new(request, employee_pk):
    employee = get_object_or_404(Employee, pk=employee_pk, is_staff=False)
    initial = {"date": _parse_day(request.GET.get("date"), services.today())}
    form = EntryForm(request.POST or None, employee=employee, initial=initial)
    if request.method == "POST" and form.is_valid():
        correction = _save_entry(request, form, employee)
        if correction:
            messages.success(request, "Tiempo agregado al registro.")
            return redirect(_month_of(employee, correction.new_clock_in))
    return form_page(
        request,
        form,
        f"Agregar tiempo para {employee.display_name}",
        reverse("manage_employee_detail", args=[employee.pk]),
        "Agregar tiempo",
        intro="Para tiempo trabajado que no quedó registrado. Se marca como corrección de administrador con tu motivo.",
        trail=[person_crumb(employee), crumb("Agregar tiempo")],
    )


@admin_required
def entry_edit(request, pk):
    entry = get_object_or_404(TimeEntry.objects.select_related("employee"), pk=pk)
    employee = entry.employee
    form = EntryForm(request.POST or None, employee=employee, entry=entry)
    if request.method == "POST" and form.is_valid():
        correction = _save_entry(request, form, employee, entry=entry)
        if correction:
            messages.success(request, "Registro corregido.")
            return redirect(_month_of(employee, correction.new_clock_in))
    return form_page(
        request,
        form,
        f"Corregir registro de {employee.display_name}",
        _month_of(employee, entry.clock_in),
        "Guardar corrección",
        intro="El registro queda marcado como corrección de administrador y las horas anteriores se conservan en el historial.",
        trail=[person_crumb(employee), crumb("Corregir registro")],
    )


@admin_required
def entry_remove(request, pk):
    entry = get_object_or_404(TimeEntry.objects.select_related("employee"), pk=pk)
    employee = entry.employee
    back = _month_of(employee, entry.clock_in)
    form = RemoveEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        correction = services.remove_entry(request.user, entry, form.cleaned_data["reason"])
        notifications.record_corrected(request, correction)
        messages.success(request, "Registro eliminado.")
        return redirect(back)
    clock_in = timezone.localtime(entry.clock_in)
    clock_out = clock_text(entry.clock_out) if entry.clock_out else "sin salida"
    return form_page(
        request,
        form,
        f"¿Eliminar este registro de {employee.display_name}?",
        back,
        "Eliminar registro",
        intro=f"{date_text(clock_in, 'l j \\d\\e F \\d\\e Y')}, {clock_text(clock_in)} a {clock_out}. "
        "Las horas se conservan en el historial de correcciones.",
        danger=True,
        trail=[person_crumb(employee), crumb("Eliminar registro")],
    )


# --- Horarios ---------------------------------------------------------------


@admin_required
def schedules(request):
    """Quién debe trabajar cuándo en una semana, además de los horarios definidos."""
    current = services.today()
    monday = services.week_start(_parse_day(request.GET.get("week"), current))
    holidays = services.holiday_map(monday, monday + timedelta(days=6))
    employees = list(tracked_employees())
    rows = [
        {
            "employee": employee,
            "week": services.build_weeks(employee, monday, monday, holidays=holidays)[0],
        }
        for employee in employees
    ]
    days = [
        {"date": day, "holiday": holidays.get(day), "is_today": day == current}
        for day in (monday + timedelta(days=offset) for offset in range(7))
    ]
    on_schedule_now = Counter(employee.terms.schedule_id for employee in employees if employee.terms)
    definitions = list(Schedule.objects.prefetch_related("versions").annotate(use_count=Count("assignments")))
    for schedule in definitions:
        schedule.employee_count = on_schedule_now[schedule.pk]
    return render(
        request,
        "attendance/manage/schedules.html",
        {
            "schedules": definitions,
            "rows": rows,
            "days": days,
            "monday": monday,
            "prev_week": monday - timedelta(days=7),
            "next_week": monday + timedelta(days=7),
            "is_current_week": monday == services.week_start(current),
        },
    )


@admin_required
def schedule_edit(request, pk=None):
    schedule = get_object_or_404(Schedule, pk=pk) if pk else None
    form = ScheduleForm(request.POST or None, schedule=schedule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Horario guardado.")
        return redirect("manage_schedules")
    title = f"Editar {schedule.name}" if schedule else "Agregar horario"
    intro = (
        "Las semanas anteriores a la fecha que elijas conservan las horas y días que tenían."
        if schedule
        else "Marca los días que normalmente trabajan las personas con este horario."
    )
    day_rows = [
        (form[day].label, form[day], form[hours])
        for day, hours in zip(
            ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
            ("monday_hours", "tuesday_hours", "wednesday_hours", "thursday_hours", "friday_hours", "saturday_hours", "sunday_hours"),
        )
    ]
    return render(
        request,
        "attendance/manage/schedule_form.html",
        {
            "form": form,
            "title": title,
            "intro": intro,
            "submit_label": "Guardar",
            "cancel_url": reverse("manage_schedules"),
            "day_rows": day_rows,
            "breadcrumbs": [crumb(schedule.name if schedule else "Agregar horario")],
        },
    )


@admin_required
@require_POST
def schedule_delete(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk)
    try:
        schedule.delete()
    except ProtectedError:
        messages.error(request, "Este horario hace parte del historial de alguien, así que no puede eliminarse.")
    else:
        messages.success(request, "Horario eliminado.")
    return redirect("manage_schedules")


# --- Permisos ---------------------------------------------------------------


@admin_required
def leave_overview(request):
    """Cupos anuales de permiso y saldo de cada empleado."""
    year = _parse_year(request.GET.get("year"), services.today().year)
    form = LeaveAllowanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Cupos de permiso guardados.")
        return redirect(f"{reverse('manage_leave')}?year={year}")

    allowances = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
    leave_types = LeaveRequest.LeaveType.choices
    rows = []
    for employee in tracked_employees():
        balances = {balance.leave_type: balance for balance in services.leave_balances(employee, year, allowances)}
        rows.append({"employee": employee, "cells": [balances.get(value) for value, _ in leave_types]})
    return render(
        request,
        "attendance/manage/leave.html",
        {"year": year, "form": form, "rows": rows, "leave_types": [label for _, label in leave_types]},
    )


# --- Configuración ----------------------------------------------------------


@admin_required
def settings_page(request):
    form = TimerCheckForm(request.POST or None, instance=TimerCheck.load())
    if request.method == "POST" and form.is_valid():
        check = form.save()
        if check.enabled:
            messages.success(
                request,
                f"Guardado. Se preguntará después de {services.duration_text(check.ask_after)} "
                f"y se detendrá si no hay respuesta en {check.answer_minutes} "
                f"minuto{'' if check.answer_minutes == 1 else 's'}.",
            )
        else:
            messages.success(request, "Guardado. Los temporizadores ya no preguntarán ni se detendrán automáticamente.")
        return redirect("manage_settings")
    return form_page(
        request,
        form,
        "Configuración",
        reverse("manage_dashboard"),
        intro=(
            'Cuando un temporizador lleva corriendo este tiempo sin descansos, se le pregunta al empleado "¿Sigues trabajando?". '
            "Si no responde a tiempo, se marca la salida en el momento en que se hizo la pregunta, el registro queda "
            "marcado como detenido automáticamente y el empleado recibe un correo. El cambio aplica a los temporizadores ya activos."
        ),
    )


# --- Correo -----------------------------------------------------------------


@admin_required
def email_settings(request):
    """Muestra el servidor de correo configurado (nunca su contraseña) y envía un mensaje de prueba."""
    configured = bool(settings.EMAIL_HOST)
    form = TestEmailForm(request.POST or None, initial={"to": request.user.email})
    if request.method == "POST" and form.is_valid():
        recipient = form.cleaned_data["to"]
        if not configured:
            messages.error(request, "No hay ningún servidor de correo configurado, así que aún no hay nada que probar.")
        else:
            try:
                notifications.send_test(request, recipient)
            except OSError as error:  # cubre fallos SMTP, red, TLS y tiempo de espera
                messages.error(request, f"No se envió el correo de prueba. {notifications.explain_failure(error)}")
            else:
                messages.success(
                    request,
                    f"Correo de prueba enviado a {recipient}. El servidor de correo lo aceptó; "
                    "revisa la bandeja de entrada y la carpeta de spam si no aparece.",
                )
        return redirect("manage_email")

    employees = Employee.objects.filter(is_active=True, is_staff=False)
    return render(
        request,
        "attendance/manage/email.html",
        {
            "form": form,
            "configured": configured,
            "server": {
                "host": settings.EMAIL_HOST,
                "port": settings.EMAIL_PORT,
                "encryption": "SSL" if settings.EMAIL_USE_SSL else "STARTTLS" if settings.EMAIL_USE_TLS else "Ninguno",
                "username": settings.EMAIL_HOST_USER,
                "has_password": bool(settings.EMAIL_HOST_PASSWORD),
                "sender": settings.DEFAULT_FROM_EMAIL,
            },
            "admin_addresses": notifications.admin_addresses(),
            "employee_count": employees.count(),
            "without_address": employees.filter(email="").count(),
        },
    )


# --- Festivos ---------------------------------------------------------------


def _parse_year(value, fallback):
    try:
        year = int(value)
    except (TypeError, ValueError):
        return fallback
    return year if 2000 <= year <= 2100 else fallback


@admin_required
def holidays(request):
    year = _parse_year(request.GET.get("year"), services.today().year)
    occurrences = services.holiday_occurrences(date(year, 1, 1), date(year, 12, 31))
    # En el año actual, los festivos que ya pasaron se agrupan aparte.
    current = services.today()
    past = [item for item in occurrences if item[0] < current] if year == current.year else []
    return render(
        request,
        "attendance/manage/holidays.html",
        {"year": year, "occurrences": occurrences[len(past) :], "past": past},
    )


@admin_required
def holiday_edit(request, pk=None):
    holiday = get_object_or_404(Holiday, pk=pk) if pk else None
    form = HolidayForm(request.POST or None, instance=holiday)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Festivo guardado.")
        return redirect(f"{reverse('manage_holidays')}?year={saved.date.year}")
    title = f"Editar {holiday.name}" if holiday else "Agregar festivo"
    return form_page(
        request, form, title, reverse("manage_holidays"), trail=[crumb(holiday.name if holiday else "Agregar festivo")]
    )


@admin_required
@require_POST
def holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    year = _parse_year(request.POST.get("year"), holiday.date.year)
    holiday.delete()
    messages.success(request, f"{holiday.name} eliminado.")
    return redirect(f"{reverse('manage_holidays')}?year={year}")


@admin_required
@require_POST
def holiday_preset(request):
    year = _parse_year(request.POST.get("year"), services.today().year)
    co_holidays.add_fixed_holidays(Holiday)
    added = co_holidays.add_movable_holidays(Holiday, year)
    if added:
        messages.success(
            request, f"Se agregaron {added} festivos de Colombia para {year}."
        )
    else:
        messages.info(request, f"Los festivos de Colombia para {year} ya están en la lista.")
    return redirect(f"{reverse('manage_holidays')}?year={year}")


# --- Recargos, extras, Art. 12 Ley 2466 y ERP -------------------------------


def _surcharge_filters(request):
    today = services.today()
    default_start = services.week_start(today)
    default_end = default_start + timedelta(days=6)
    form = ReviewFilterForm(request.GET or None, initial={"start": default_start, "end": default_end})
    if form.is_valid():
        start = form.cleaned_data["start"]
        end = form.cleaned_data["end"]
        if end < start:
            start, end = end, start
        employee = form.cleaned_data["employee"]
    else:
        start, end, employee = default_start, default_end, None
    return form, start, end, employee


@admin_required
def surcharges(request):
    form, start, end, employee = _surcharge_filters(request)
    preview = erp.build_preview_context(start, end, employee=employee)
    rule = LaborRuleConfig.for_date(end)
    return render(
        request,
        "attendance/manage/surcharges.html",
        {
            "form": form,
            "start": start,
            "end": end,
            "rule": rule,
            "query": request.GET.urlencode(),
            **preview,
        },
    )


@admin_required
def export_art12_csv(request):
    _, start, end, employee = _surcharge_filters(request)
    return erp.export_art12_csv(start, end, employee=employee)


@admin_required
def export_art12_xlsx(request):
    _, start, end, employee = _surcharge_filters(request)
    return erp.export_art12_xlsx(start, end, employee=employee)


@admin_required
def export_erp_csv(request):
    _, start, end, employee = _surcharge_filters(request)
    return erp.export_erp_csv(start, end, employee=employee)


@admin_required
def export_erp_xlsx(request):
    _, start, end, employee = _surcharge_filters(request)
    return erp.export_erp_xlsx(start, end, employee=employee)


@admin_required
def labor_rules(request):
    rules = LaborRuleConfig.objects.all()
    return render(request, "attendance/manage/labor_rules.html", {"rules": rules})


@admin_required
def labor_rule_edit(request, pk=None):
    rule = get_object_or_404(LaborRuleConfig, pk=pk) if pk else None
    form = LaborRuleConfigForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Regla laboral vigente desde {saved.effective_from} guardada.")
        return redirect("manage_labor_rules")
    title = f"Editar regla ({rule.effective_from})" if rule else "Nueva regla laboral"
    return form_page(
        request,
        form,
        title,
        reverse("manage_labor_rules"),
        trail=[crumb("Reglas laborales", "manage_labor_rules"), crumb(title)],
    )
