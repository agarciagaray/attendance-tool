from datetime import datetime, timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from . import lockout, services
from .models import (
    WEEKDAY_FIELDS,
    WEEKDAY_HOURS_FIELDS,
    Employee,
    Holiday,
    LaborRuleConfig,
    LeaveAllowance,
    LeaveRequest,
    Schedule,
    TimeEntry,
    TimerCheck,
)


def date_input():
    return forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


def time_input():
    return forms.TimeInput(format="%H:%M", attrs={"type": "time"})


class StyledFormMixin:
    """Asigna a cada widget la clase CSS que espera la hoja de estilos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for form_field in self.fields.values():
            widget = form_field.widget
            css = "checkbox" if isinstance(widget, forms.CheckboxInput) else "input"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class LoginForm(StyledFormMixin, AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username")
        self._refuse_if_locked(username)
        try:
            return super().clean()
        except forms.ValidationError:
            self._refuse_if_locked(username)  # este intento pudo haber activado el bloqueo
            raise

    def _refuse_if_locked(self, username):
        until = lockout.locked_until(username)
        if until:
            minutes = max(1, round((until - timezone.now()).total_seconds() / 60))
            plural = "" if minutes == 1 else "s"
            raise forms.ValidationError(
                f"Demasiados intentos con contraseña incorrecta. Intenta de nuevo en {minutes} minuto{plural}, "
                "o pídele a un administrador que te asigne una nueva contraseña."
            )


class OwnPasswordForm(StyledFormMixin, PasswordChangeForm):
    pass


class ResetPasswordForm(StyledFormMixin, SetPasswordForm):
    pass


# --- Solicitudes de empleados -----------------------------------------------


class MemoForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TimeEntry
        fields = ["memo"]
        labels = {"memo": "Nota de trabajo"}
        widgets = {"memo": forms.Textarea(attrs={"rows": 3, "maxlength": 300})}
        help_texts = {"memo": "Opcional: en qué se empleó este tiempo. Tu administrador puede leerla."}

    def clean_memo(self):
        return " ".join(self.cleaned_data["memo"].split())


class MissingTimeForm(StyledFormMixin, forms.Form):
    date = forms.DateField(label="Fecha", widget=date_input())
    time_in = forms.TimeField(label="Hora de entrada", widget=time_input())
    time_out = forms.TimeField(
        label="Hora de salida",
        widget=time_input(),
        help_text="Si es anterior a la hora de entrada, se toma como del día siguiente.",
    )
    reason = forms.CharField(
        label="Motivo",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Por qué no se registró el tiempo con el botón del reloj.",
    )

    def __init__(self, *args, employee, entry=None, **kwargs):
        """Pasa `entry` para pedir solo la hora de salida de un registro al que le falta."""
        self.employee = employee
        self.entry = entry
        super().__init__(*args, **kwargs)
        if entry is not None:
            del self.fields["date"]
            del self.fields["time_in"]

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        if self.entry is not None:
            clock_in = self.entry.clock_in
            day = timezone.localtime(clock_in).date()
        else:
            day = cleaned["date"]
            clock_in = timezone.make_aware(datetime.combine(day, cleaned["time_in"]))
        clock_out = timezone.make_aware(datetime.combine(day, cleaned["time_out"]))
        if clock_out <= clock_in:
            clock_out += timedelta(days=1)
        services.validate_missing_time(self.employee, clock_in, clock_out, entry=self.entry)
        cleaned["clock_in"] = clock_in
        cleaned["clock_out"] = clock_out
        return cleaned


class LeaveForm(StyledFormMixin, forms.ModelForm):
    one_day = forms.BooleanField(
        required=False,
        label="Solo 1 día",
        widget=forms.CheckboxInput(attrs={"data-hides": "id_end_date"}),
        help_text="Marca esto para un permiso de un solo día; no necesitarás indicar último día.",
    )
    field_order = ["leave_type", "start_date", "one_day", "end_date", "reason"]

    class Meta:
        model = LeaveRequest
        fields = ["leave_type", "start_date", "end_date", "reason"]
        labels = {
            "leave_type": "Tipo de permiso",
            "start_date": "Primer día",
            "end_date": "Último día",
            "reason": "Motivo",
        }
        widgets = {
            "start_date": date_input(),
            "end_date": date_input(),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, employee, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        self.fields["end_date"].required = False

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        if cleaned.get("one_day") and start:
            cleaned["end_date"] = start
        elif not cleaned.get("end_date") and "end_date" not in self.errors:
            self.add_error("end_date", "Ingresa el último día del permiso, o marca \"Solo 1 día\".")
        end = cleaned.get("end_date")
        # Si el último día es anterior al primero, lo reporta la restricción del modelo.
        if start and end and end >= start:
            services.validate_leave(self.employee, start, end)
        return cleaned


# --- Administración ---------------------------------------------------------


class EntryForm(StyledFormMixin, forms.Form):
    """Un administrador agrega un registro de tiempo o corrige las horas de uno existente."""

    date = forms.DateField(label="Fecha", widget=date_input())
    time_in = forms.TimeField(label="Hora de entrada", widget=time_input())
    time_out = forms.TimeField(
        label="Hora de salida",
        widget=time_input(),
        help_text="Si es anterior a la hora de entrada, se toma como del día siguiente.",
    )
    reason = forms.CharField(
        label="Motivo",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Se guarda con el registro y se muestra al empleado.",
    )

    def __init__(self, *args, employee, entry=None, **kwargs):
        self.employee = employee
        self.entry = entry
        if entry is not None:
            kwargs["initial"] = {
                "date": timezone.localtime(entry.clock_in).date(),
                "time_in": self._to_minute(entry.clock_in),
                "time_out": self._to_minute(entry.clock_out) if entry.clock_out else None,
            }
        super().__init__(*args, **kwargs)

    @staticmethod
    def _to_minute(moment):
        return timezone.localtime(moment).time().replace(second=0, microsecond=0)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        day = cleaned["date"]
        clock_in = timezone.make_aware(datetime.combine(day, cleaned["time_in"]))
        clock_out = timezone.make_aware(datetime.combine(day, cleaned["time_out"]))
        if clock_out <= clock_in:
            clock_out += timedelta(days=1)
        # Si la hora de entrada no se editó, conserva el segundo exacto original.
        untouched = self.entry is not None and (day, cleaned["time_in"]) == (
            self.initial["date"],
            self.initial["time_in"],
        )
        if untouched:
            clock_in = self.entry.clock_in
        services.validate_entry_times(self.employee, clock_in, clock_out, exclude_entry=self.entry)
        cleaned["clock_in"] = clock_in
        cleaned["clock_out"] = clock_out
        return cleaned


class RemoveEntryForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        label="Motivo",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Se guarda en el historial de correcciones y se muestra al empleado.",
    )


class EmployeeForm(StyledFormMixin, forms.ModelForm):
    schedule = forms.ModelChoiceField(
        label="Horario",
        queryset=Schedule.objects.all(),
        required=False,
        empty_label="Sin horario",
        help_text="Define la meta de horas semanales. Déjalo vacío para registrar horas sin meta.",
    )
    schedule_from = forms.DateField(
        label="El horario aplica desde",
        required=False,
        widget=date_input(),
        help_text="Solo se usa si cambias el horario. Inicia desde el lunes de esa semana; "
        "las semanas anteriores conservan el anterior.",
    )

    class Meta:
        model = Employee
        fields = [
            "username",
            "document_number",
            "first_name",
            "last_name",
            "email",
            "contract_type",
            "start_date",
            "is_staff",
            "is_active",
        ]
        labels = {
            "username": "Usuario",
            "document_number": "Cédula / Documento",
            "first_name": "Nombre",
            "last_name": "Apellidos",
            "email": "Correo electrónico",
            "contract_type": "Tipo de contrato",
            "start_date": "Fecha de inicio",
            "is_staff": "Administrador",
            "is_active": "Activo",
        }
        help_texts = {
            "username": "Se usa para iniciar sesión.",
            "document_number": "Opcional. Número de cédula o documento de identidad (único).",
            "email": "Opcional. Los avisos sobre solicitudes y correcciones se envían aquí.",
            "is_staff": "Los administradores ven la asistencia de todos y deciden solicitudes. No marcan entrada ni salida.",
            "is_active": "",
        }
        widgets = {"start_date": date_input()}

    field_order = [
        "username",
        "document_number",
        "first_name",
        "last_name",
        "email",
        "contract_type",
        "schedule",
        "schedule_from",
        "start_date",
    ]

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.fields["contract_type"].required = False
        self.is_new = self.instance.pk is None
        if self.is_new:
            # Un empleado nuevo queda en su horario desde el inicio.
            del self.fields["schedule_from"]
            self.assigned = None
        else:
            self.assigned = self.instance.assigned_schedule
            self.fields["schedule"].initial = self.assigned
            self.fields["schedule_from"].initial = services.week_start(services.today())

    def clean_contract_type(self):
        return self.cleaned_data.get("contract_type") or (
            self.instance.contract_type if self.instance.pk else Employee.ContractType.INDEFINITE
        )

    def clean(self):
        cleaned = super().clean()
        editing_self = self.instance.pk and self.acting_user and self.instance.pk == self.acting_user.pk
        if editing_self and not (cleaned.get("is_staff") and cleaned.get("is_active")):
            raise forms.ValidationError("No puedes quitarte tu propio acceso de administrador ni desactivarte.")
        if cleaned.get("is_staff"):
            cleaned["schedule"] = None  # los administradores no registran asistencia
        return cleaned

    def save(self):
        employee = super().save()
        schedule = self.cleaned_data.get("schedule")
        if self.is_new:
            if schedule:
                services.assign_schedule(employee, schedule, services.BEGINNING)
        elif schedule != self.assigned:
            services.assign_schedule(employee, schedule, self.cleaned_data.get("schedule_from") or services.today())
        return employee


class EmployeeCreateForm(EmployeeForm):
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Entrega esta contraseña al empleado. Podrá cambiarla después de iniciar sesión.",
    )

    field_order = ["username", "password", *EmployeeForm.field_order[1:]]

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def save(self):
        self.instance.set_password(self.cleaned_data["password"])
        return super().save()


class ScheduleForm(StyledFormMixin, forms.Form):
    """Crea un horario o modifica uno a partir de una semana elegida."""

    name = forms.CharField(label="Nombre", max_length=100)
    weekly_hours = forms.DecimalField(
        label="Horas por semana",
        min_value=Decimal("0.5"),
        max_value=Decimal("112"),
        max_digits=5,
        decimal_places=2,
        help_text="Los festivos y permisos aprobados reducen la meta de la semana proporcionalmente.",
    )
    monday = forms.BooleanField(label="Lunes", required=False, initial=True)
    monday_hours = forms.DecimalField(
        label="Horas lunes (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    tuesday = forms.BooleanField(label="Martes", required=False, initial=True)
    tuesday_hours = forms.DecimalField(
        label="Horas martes (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    wednesday = forms.BooleanField(label="Miércoles", required=False, initial=True)
    wednesday_hours = forms.DecimalField(
        label="Horas miércoles (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    thursday = forms.BooleanField(label="Jueves", required=False, initial=True)
    thursday_hours = forms.DecimalField(
        label="Horas jueves (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    friday = forms.BooleanField(label="Viernes", required=False, initial=True)
    friday_hours = forms.DecimalField(
        label="Horas viernes (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    saturday = forms.BooleanField(label="Sábado", required=False)
    saturday_hours = forms.DecimalField(
        label="Horas sábado (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    sunday = forms.BooleanField(label="Domingo", required=False)
    sunday_hours = forms.DecimalField(
        label="Horas domingo (opcional)", required=False, min_value=Decimal("0.5"), max_value=Decimal("12"), max_digits=4, decimal_places=2
    )
    effective_from = forms.DateField(
        label="Los cambios aplican desde",
        widget=date_input(),
        help_text="Las nuevas horas o días rigen desde el lunes de esa semana. Las semanas anteriores conservan "
        "lo que tenían. Para corregir un error, elige la fecha en que comenzó.",
    )

    def __init__(self, *args, schedule=None, **kwargs):
        self.schedule = schedule
        self.latest = schedule.latest if schedule else None
        if self.latest:
            this_week = services.week_start(services.today())
            kwargs["initial"] = {
                "name": schedule.name,
                "weekly_hours": f"{self.latest.weekly_hours.normalize():f}",  # "40", no "40.00"
                "effective_from": max(this_week, self.latest.effective_from),
                **{day: getattr(self.latest, day) for day in WEEKDAY_FIELDS},
                **{
                    hf: (f"{getattr(self.latest, hf).normalize():f}" if getattr(self.latest, hf) is not None else None)
                    for hf in WEEKDAY_HOURS_FIELDS
                },
            }
        super().__init__(*args, **kwargs)
        if not self.latest:
            del self.fields["effective_from"]  # un horario nuevo no tiene semanas previas que proteger

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        taken = Schedule.objects.filter(name__iexact=name)
        if self.schedule:
            taken = taken.exclude(pk=self.schedule.pk)
        if taken.exists():
            raise forms.ValidationError("Ya existe un horario con ese nombre.")
        return name

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(day) for day in WEEKDAY_FIELDS):
            raise forms.ValidationError("Selecciona al menos un día laborable.")
        custom_hours = [cleaned.get(hf) for hf in WEEKDAY_HOURS_FIELDS]
        if any(h is not None for h in custom_hours):
            eff = cleaned.get("effective_from") or services.today()
            rule = LaborRuleConfig.for_date(eff)
            total_custom = Decimal("0")
            for day, hf in zip(WEEKDAY_FIELDS, WEEKDAY_HOURS_FIELDS):
                is_working = bool(cleaned.get(day))
                hrs = cleaned.get(hf)
                if is_working and hrs is None:
                    self.add_error(hf, "Indica las horas de este día laborable o deja todos los días vacíos.")
                elif not is_working and hrs is not None:
                    self.add_error(hf, "No asignes horas a un día marcado como libre.")
                elif hrs is not None:
                    if hrs < rule.min_daily_hours or hrs > rule.max_daily_hours:
                        self.add_error(
                            hf,
                            f"La jornada diaria personalizada debe estar entre {rule.min_daily_hours.normalize():f} "
                            f"y {rule.max_daily_hours.normalize():f} horas.",
                        )
                    total_custom += hrs
            weekly_hours = cleaned.get("weekly_hours")
            if not self.errors and weekly_hours is not None and total_custom != weekly_hours:
                raise forms.ValidationError(
                    f"La suma de las horas diarias ({total_custom.normalize():f} h) debe coincidir con "
                    f"las horas por semana ({weekly_hours.normalize():f} h)."
                )
        return cleaned

    def save(self):
        cleaned = self.cleaned_data
        schedule = self.schedule or Schedule()
        schedule.name = cleaned["name"]
        schedule.save()
        weekdays = {day: cleaned[day] for day in WEEKDAY_FIELDS}
        daily_hours = {hf: cleaned.get(hf) for hf in WEEKDAY_HOURS_FIELDS}
        if not self.latest:
            services.set_schedule_terms(
                schedule, cleaned["weekly_hours"], weekdays, services.BEGINNING, daily_hours=daily_hours
            )
        else:
            unchanged = (
                self.latest.weekly_hours == cleaned["weekly_hours"]
                and all(getattr(self.latest, day) == weekdays[day] for day in WEEKDAY_FIELDS)
                and all(getattr(self.latest, hf) == daily_hours[hf] for hf in WEEKDAY_HOURS_FIELDS)
                and services.week_start(cleaned["effective_from"]) == self.initial["effective_from"]
            )
            if not unchanged:
                services.set_schedule_terms(
                    schedule, cleaned["weekly_hours"], weekdays, cleaned["effective_from"], daily_hours=daily_hours
                )
        return schedule


def _allowance_field(label):
    return forms.IntegerField(label=label, required=False, min_value=0, max_value=366)


class LeaveAllowanceForm(StyledFormMixin, forms.Form):
    """Días por tipo de permiso que cada empleado puede tomar al año. Vacío significa sin límite."""

    # Un campo por cada valor de LeaveRequest.LeaveType.
    annual = _allowance_field("Anual")
    casual = _allowance_field("Personal")
    sick = _allowance_field("Incapacidad")
    unpaid = _allowance_field("No remunerado")
    other = _allowance_field("Otro")

    def __init__(self, *args, **kwargs):
        kwargs["initial"] = dict(LeaveAllowance.objects.values_list("leave_type", "days"))
        super().__init__(*args, **kwargs)

    def save(self):
        for leave_type in LeaveRequest.LeaveType.values:
            days = self.cleaned_data.get(leave_type)
            if days is None:
                LeaveAllowance.objects.filter(leave_type=leave_type).delete()
            else:
                LeaveAllowance.objects.update_or_create(leave_type=leave_type, defaults={"days": days})


class TimerCheckForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TimerCheck
        fields = ["ask_after_hours", "answer_minutes"]
        labels = {
            "ask_after_hours": "Preguntar después de (horas)",
            "answer_minutes": "Esperar respuesta (minutos)",
        }


class TestEmailForm(StyledFormMixin, forms.Form):
    to = forms.EmailField(label="Enviar correo de prueba a")


class HolidayForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "recurs_yearly", "tentative"]
        labels = {
            "date": "Fecha",
            "name": "Nombre",
            "recurs_yearly": "Se repite cada año",
            "tentative": "Tentativo",
        }
        widgets = {"date": date_input()}


class ReviewFilterForm(StyledFormMixin, forms.Form):
    start = forms.DateField(label="Desde", widget=date_input())
    end = forms.DateField(label="Hasta", widget=date_input())
    employee = forms.ModelChoiceField(
        label="Empleado",
        queryset=Employee.objects.filter(is_staff=False),
        required=False,
        empty_label="Todos los empleados activos",
    )
