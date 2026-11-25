"""Páginas que ve cada empleado al iniciar sesión."""

import calendar
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import notifications, services
from .dates import date_text
from .forms import LeaveForm, LoginForm, MemoForm, MissingTimeForm, OwnPasswordForm
from .middleware import SIGNED_IN_AS
from .models import WEEKDAY_SHORT, LeaveRequest, MissingTimeRequest, RequestStatus, TimeEntry
from .navigation import crumb

REQUEST_MODELS = {model.kind: model for model in (MissingTimeRequest, LeaveRequest)}


def employee_only(view):
    """Para las páginas personales. Los administradores gestionan el sistema y no registran asistencia propia."""

    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.is_staff:
            return redirect("manage_dashboard")
        return view(request, *args, **kwargs)

    return wrapped


def parse_month(value, fallback):
    """Primer día del mes de una cadena "YYYY-MM", o del mes de `fallback`."""
    try:
        year, month = (int(part) for part in value.split("-"))
        if not 2000 <= year <= 2100:
            raise ValueError
        return date(year, month, 1)
    except (AttributeError, ValueError):
        return fallback.replace(day=1)


def month_context(employee, month, now=None):
    """Contexto de plantilla para el mes de un empleado: semanas del calendario y registros."""
    now = now or timezone.now()
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    weeks = services.build_weeks(employee, month, last, now=now)
    month_days = []
    for week in weeks:
        for day in week.days:
            day.muted = day.date.month != month.month
            if not day.muted:
                month_days.append(day)
    recorded = [day for day in month_days if day.entries]
    return {
        "subject": employee,
        "month": month,
        "prev_month": (month - services.ONE_DAY).replace(day=1),
        "next_month": last + services.ONE_DAY,
        "weeks": weeks,
        "weekday_names": WEEKDAY_SHORT,
        "month_worked": sum((day.worked for day in month_days), timedelta(0)),
        "days_with_entries": recorded,
        "timeline": services.build_timeline(
            [(date_text(day.date, "D j M"), None, day) for day in recorded], now=now
        ),
        "corrections": employee.corrections.select_related("admin")[:20],
    }


def get_request_or_404(kind, **filters):
    model = REQUEST_MODELS.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(model, **filters)


class SignInView(LoginView):
    authentication_form = LoginForm
    template_name = "attendance/login.html"
    redirect_authenticated_user = True


class OwnPasswordView(PasswordChangeView):
    form_class = OwnPasswordForm
    template_name = "attendance/form_page.html"
    success_url = reverse_lazy("home")
    extra_context = {
        "title": "Cambiar contraseña",
        "submit_label": "Cambiar contraseña",
        "cancel_url": reverse_lazy("home"),
    }

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "impersonator", None):
            messages.error(request, "No se puede cambiar la contraseña mientras ves la sesión como otro usuario.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "Tu contraseña ha sido actualizada.")
        return super().form_valid(form)


@login_required
@require_POST
def sign_in_as_stop(request):
    """Termina la vista del administrador como empleado."""
    employee_pk = request.session.pop(SIGNED_IN_AS, None)
    if request.impersonator and employee_pk:
        return redirect("manage_employee_detail", pk=employee_pk)
    return redirect("home")


@employee_only
def home(request):
    employee = request.user
    now = timezone.now()
    current = services.today(now)
    week = services.build_weeks(employee, current, current, now=now)[0]
    day = next(day for day in week.days if day.date == current)
    open_entry = services.get_open_entry(employee)
    finished = sum((entry.worked(now) for entry in day.entries if not entry.is_open), timedelta(0))
    unresolved = (
        TimeEntry.objects.filter(employee=employee, missing_out=True)
        .exclude(fix_requests__status=RequestStatus.PENDING)
        .order_by("-clock_in")[:5]
    )
    return render(
        request,
        "attendance/home.html",
        {
            "now": now,
            "now_ms": int(now.timestamp() * 1000),
            "open_entry": open_entry,
            "open_ms": int(open_entry.clock_in.timestamp() * 1000) if open_entry else None,
            "finished_seconds": int(finished.total_seconds()),
            "time_zone": settings.TIME_ZONE,
            "day": day,
            "today_days": [day],
            # La última sesión de hoy, si el sistema la detuvo y no hay nada corriendo desde entonces.
            "auto_stopped": next(
                (entry for entry in day.entries[-1:] if entry.auto_stopped and not open_entry), None
            ),
            "timeline": services.build_timeline([("Hoy", None, day)], now),
            "week": week,
            "unresolved": unresolved,
        },
    )


@employee_only
@require_POST
def punch(request):
    action = request.POST.get("action")
    if action not in ("in", "out"):
        return HttpResponseBadRequest("Acción desconocida.")
    entry, changed = services.punch(request.user, action)
    if not changed:
        state = "tu entrada" if action == "in" else "tu salida"
        messages.info(request, f"Ya habías registrado {state}, así que no cambió nada.")
    elif action == "in":
        messages.success(request, f"Entrada registrada a las {services.clock_text(entry.clock_in)}.")
    else:
        worked = services.duration_text(entry.clock_out - entry.clock_in)
        messages.success(request, f"Salida registrada a las {services.clock_text(entry.clock_out)} tras {worked}.")
    return redirect("home")


@employee_only
@require_POST
def still_working(request):
    """La respuesta "sí" a "¿Sigues trabajando?"."""
    if services.confirm_still_working(request.user) is None:
        messages.warning(request, "Tu temporizador ya se había detenido.")
        return redirect("home")
    back = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = reverse("home")
    return redirect(back)


@employee_only
def entry_memo(request, pk):
    """Nota propia del empleado sobre un tramo de su tiempo. Opcional y editable en cualquier momento."""
    entry = get_object_or_404(TimeEntry, pk=pk, employee=request.user)
    back = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = f"{reverse('calendar')}?month={services.work_date(entry.clock_in):%Y-%m}"

    form = MemoForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Nota guardada." if entry.memo else "Nota eliminada.")
        return redirect(back)

    if entry.clock_out:
        span = f"{services.clock_text(entry.clock_in)} – {services.clock_text(entry.clock_out)}"
    else:
        span = f"entrada a las {services.clock_text(entry.clock_in)}"
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": "Nota de trabajo",
            "intro": f"{date_text(services.work_date(entry.clock_in), 'l j \\d\\e F')}, {span}.",
            "submit_label": "Guardar nota",
            "cancel_url": back,
            "breadcrumbs": [crumb("Nota")],
        },
    )


@employee_only
def my_calendar(request):
    month = parse_month(request.GET.get("month"), services.today())
    context = month_context(request.user, month)
    context["calendar_url"] = reverse("calendar")
    return render(request, "attendance/calendar.html", context)


@employee_only
def my_requests(request):
    employee = request.user
    year = services.today().year
    return render(
        request,
        "attendance/requests.html",
        {
            "missing_requests": employee.missingtimerequests.all()[:50],
            "leave_requests": employee.leaverequests.all()[:50],
            "leave_balances": services.leave_balances(employee, year),
            "year": year,
        },
    )


@employee_only
def missing_time_new(request):
    employee = request.user
    entry = None
    entry_id = request.GET.get("entry")
    if entry_id:
        if not entry_id.isdigit():
            raise Http404
        entry = get_object_or_404(TimeEntry, pk=entry_id, employee=employee, missing_out=True)

    form = MissingTimeForm(request.POST or None, employee=employee, entry=entry)
    if request.method == "POST" and form.is_valid():
        sent = MissingTimeRequest.objects.create(
            employee=employee,
            clock_in=form.cleaned_data["clock_in"],
            clock_out=form.cleaned_data["clock_out"],
            entry=entry,
            reason=form.cleaned_data["reason"],
        )
        notifications.request_submitted(request, sent)
        messages.success(request, "Solicitud enviada. El tiempo contará cuando un administrador la apruebe.")
        return redirect("my_requests")

    if entry:
        title = "Agregar salida faltante"
        intro = (
            f"Marcaste entrada a las {services.clock_text(entry.clock_in)} el "
            f"{date_text(entry.clock_in, 'l j \\d\\e F')} y no marcaste salida. "
            "Ingresa la hora en que terminaste de trabajar."
        )
    else:
        title = "Registrar tiempo faltante"
        intro = "Para tiempo que trabajaste pero no pudiste marcar con el botón del reloj. Un administrador revisa cada solicitud."
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": title,
            "intro": intro,
            "submit_label": "Enviar solicitud",
            "cancel_url": reverse("my_requests"),
            "breadcrumbs": [crumb(title)],
        },
    )


@employee_only
def leave_new(request):
    form = LeaveForm(request.POST or None, employee=request.user)
    if request.method == "POST" and form.is_valid():
        leave = form.save(commit=False)
        leave.employee = request.user
        leave.save()
        notifications.request_submitted(request, leave)
        balance = services.leave_balance_for(leave)
        if balance and balance.over_by:
            days = "1 día" if balance.over_by == 1 else f"{balance.over_by} días"
            messages.warning(
                request,
                f"Solicitud de permiso enviada. Excede en {days} tu cupo de permiso {balance.label.lower()} para "
                f"{leave.start_date.year}, así que queda a criterio del administrador.",
            )
        else:
            messages.success(request, "Solicitud de permiso enviada.")
        return redirect("my_requests")
    left = [
        f"{balance.label} {max(balance.remaining, 0)}"
        for balance in services.leave_balances(request.user, services.today().year)
        if balance.allowance is not None
    ]
    intro = "Los permisos aprobados aparecen en tu calendario y reducen la meta de horas de esa semana."
    if left:
        intro += f" Días disponibles este año: {', '.join(left)}."
    return render(
        request,
        "attendance/form_page.html",
        {
            "form": form,
            "title": "Solicitar permiso",
            "intro": intro,
            "submit_label": "Enviar solicitud",
            "cancel_url": reverse("my_requests"),
            "breadcrumbs": [crumb("Solicitar permiso")],
        },
    )


@employee_only
@require_POST
def request_cancel(request, kind, pk):
    leave_or_missing = get_request_or_404(kind, pk=pk, employee=request.user)
    try:
        services.cancel(leave_or_missing, by=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Solicitud cancelada.")
    return redirect("my_requests")
