from datetime import time as dt_time
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from . import dates

WEEKDAY_FIELDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_HOURS_FIELDS = (
    "monday_hours",
    "tuesday_hours",
    "wednesday_hours",
    "thursday_hours",
    "friday_hours",
    "saturday_hours",
    "sunday_hours",
)
WEEKDAY_SHORT = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


def in_force(items, day, fall_back_to_first=False):
    """De una lista ordenada por `effective_from`, devuelve el elemento vigente en `day`."""
    chosen = None
    for item in items:
        if item.effective_from > day:
            break
        chosen = item
    if chosen is None and fall_back_to_first and items:
        return items[0]
    return chosen


class Schedule(models.Model):
    """Un patrón de trabajo con nombre, p. ej. "Oficina".

    Sus horas y días se guardan como versiones fechadas, de modo que cambiarlos a
    partir de una semana deja las semanas anteriores evaluadas con lo que aplicaba entonces.
    """

    name = models.CharField("nombre", max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "horario"
        verbose_name_plural = "horarios"

    def __str__(self):
        return self.name

    def version_list(self):
        if getattr(self, "_version_list", None) is None:
            self._version_list = sorted(self.versions.all(), key=lambda version: version.effective_from)
        return self._version_list

    def forget_history(self):
        self._version_list = None
        getattr(self, "_prefetched_objects_cache", {}).pop("versions", None)

    def version_on(self, day):
        return in_force(self.version_list(), day, fall_back_to_first=True)

    @property
    def current(self):
        return self.version_on(dates.today())

    @property
    def latest(self):
        versions = self.version_list()
        return versions[-1] if versions else None

    @property
    def upcoming(self):
        """Versiones que inician después de esta semana."""
        current = self.current
        return [v for v in self.version_list() if current and v.effective_from > current.effective_from]

    @property
    def past(self):
        """Versiones que ya fueron reemplazadas, de la más reciente a la más antigua."""
        current = self.current
        versions = self.version_list()
        earlier = [v for v in versions if current and v.effective_from < current.effective_from]
        # Cada una aplicó hasta el día anterior al inicio de la siguiente versión.
        for version in earlier:
            version.until = versions[versions.index(version) + 1].effective_from - timedelta(days=1)
        return earlier[::-1]


class ScheduleVersion(models.Model):
    """Horas semanales y días laborables de un horario a partir de un lunes.

    Si los campos `*_hours` están definidos, cada día tiene su propia duración
    (distribución irregular dentro del tope semanal); si están en None, las horas
    semanales se reparten en partes iguales entre los días laborables.
    """

    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="versions")
    effective_from = models.DateField("vigente desde")  # siempre un lunes
    weekly_hours = models.DecimalField(
        "horas semanales",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("112"))],
        help_text="Horas a completar cada semana (máximo legal 42 h, o jornada menor pactada). "
        "Los festivos y permisos aprobados las reducen proporcionalmente.",
    )
    monday = models.BooleanField("lunes", default=True)
    tuesday = models.BooleanField("martes", default=True)
    wednesday = models.BooleanField("miércoles", default=True)
    thursday = models.BooleanField("jueves", default=True)
    friday = models.BooleanField("viernes", default=True)
    saturday = models.BooleanField("sábado", default=False)
    sunday = models.BooleanField("domingo", default=False)
    monday_hours = models.DecimalField("horas lunes", max_digits=4, decimal_places=2, null=True, blank=True)
    tuesday_hours = models.DecimalField("horas martes", max_digits=4, decimal_places=2, null=True, blank=True)
    wednesday_hours = models.DecimalField("horas miércoles", max_digits=4, decimal_places=2, null=True, blank=True)
    thursday_hours = models.DecimalField("horas jueves", max_digits=4, decimal_places=2, null=True, blank=True)
    friday_hours = models.DecimalField("horas viernes", max_digits=4, decimal_places=2, null=True, blank=True)
    saturday_hours = models.DecimalField("horas sábado", max_digits=4, decimal_places=2, null=True, blank=True)
    sunday_hours = models.DecimalField("horas domingo", max_digits=4, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["effective_from"]
        verbose_name = "versión de horario"
        verbose_name_plural = "versiones de horario"
        constraints = [
            models.UniqueConstraint(fields=["schedule", "effective_from"], name="one_version_per_schedule_week")
        ]

    def __str__(self):
        return f"{self.schedule.name}: {self.hours_label}, {self.days_label} desde {self.effective_from}"

    @property
    def name(self):
        return self.schedule.name

    @property
    def has_custom_daily_hours(self):
        return any(getattr(self, field) is not None for field in WEEKDAY_HOURS_FIELDS)

    @property
    def terms(self):
        """Lo que define la versión, para detectar si dos versiones difieren."""
        daily = tuple(getattr(self, field) for field in WEEKDAY_HOURS_FIELDS)
        return (self.weekly_hours, self.working_weekdays, daily)

    @property
    def working_weekdays(self):
        return frozenset(i for i, name in enumerate(WEEKDAY_FIELDS) if getattr(self, name))

    @property
    def hours_label(self):
        return f"{self.weekly_hours.normalize():f}h"

    def hours_for_weekday(self, weekday):
        """Horas pactadas como Decimal para un día de la semana (0=lun .. 6=dom)."""
        if weekday not in self.working_weekdays:
            return Decimal("0")
        if self.has_custom_daily_hours:
            return getattr(self, WEEKDAY_HOURS_FIELDS[weekday]) or Decimal("0")
        days = len(self.working_weekdays)
        return (self.weekly_hours / Decimal(days)) if days else Decimal("0")

    def daily_target_for(self, weekday):
        """Duración esperada para un día de la semana (0=lun .. 6=dom)."""
        hours = self.hours_for_weekday(weekday)
        return timedelta(seconds=round(float(hours) * 3600)) if hours else timedelta(0)

    @property
    def daily_target(self):
        """La parte promedio de las horas semanales que corresponde a un día laborable."""
        days = len(self.working_weekdays)
        return timedelta(hours=float(self.weekly_hours) / days) if days else timedelta(0)

    @property
    def days_label(self):
        days = sorted(self.working_weekdays)
        if not days:
            return "Sin días"
        if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
            return f"{WEEKDAY_SHORT[days[0]]}–{WEEKDAY_SHORT[days[-1]]}"
        return ", ".join(WEEKDAY_SHORT[day] for day in days)

    @property
    def distribution_label(self):
        """Resumen corto de las horas diarias cuando el horario tiene distribución personalizada."""
        if not self.has_custom_daily_hours:
            return ""
        parts = [
            f"{WEEKDAY_SHORT[i]} {getattr(self, WEEKDAY_HOURS_FIELDS[i]).normalize():f}h"
            for i in sorted(self.working_weekdays)
            if getattr(self, WEEKDAY_HOURS_FIELDS[i]) is not None
        ]
        return ", ".join(parts)


class Employee(AbstractUser):
    """Un usuario. Los administradores tienen `is_staff` activo y no registran asistencia propia."""

    class ContractType(models.TextChoices):
        INDEFINITE = "indefinite", "Término indefinido"
        FIXED = "fixed", "Término fijo"
        PROJECT = "project", "Obra o labor"
        APPRENTICE = "apprentice", "Aprendizaje"
        EXEMPT = "exempt", "Dirección, confianza o manejo"
        PART_TIME = "part_time", "Tiempo parcial"

    document_number = models.CharField(
        "número de documento",
        max_length=30,
        unique=True,
        null=True,
        blank=True,
        help_text="Cédula o documento de identidad para reportes legales y exportación al ERP.",
    )
    contract_type = models.CharField(
        "tipo de contrato",
        max_length=20,
        choices=ContractType.choices,
        default=ContractType.INDEFINITE,
    )
    start_date = models.DateField(
        "fecha de inicio",
        default=timezone.localdate,
        help_text="La asistencia se registra desde esta fecha.",
    )

    class Meta(AbstractUser.Meta):
        verbose_name = "empleado"
        verbose_name_plural = "empleados"
        ordering = ["first_name", "last_name", "username"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def assignment_list(self):
        if getattr(self, "_assignment_list", None) is None:
            prefetched = "assignments" in getattr(self, "_prefetched_objects_cache", {})
            found = self.assignments.all() if prefetched else self.assignments.select_related("schedule")
            self._assignment_list = sorted(found, key=lambda assignment: assignment.effective_from)
        return self._assignment_list

    def forget_history(self):
        self._assignment_list = None
        getattr(self, "_prefetched_objects_cache", {}).pop("assignments", None)

    def terms_on(self, day):
        """La versión de horario que aplica a este empleado en un día, o None."""
        assignment = in_force(self.assignment_list(), day)
        if assignment is None or assignment.schedule is None:
            return None
        return assignment.schedule.version_on(day)

    @property
    def terms(self):
        """La versión de horario vigente esta semana, o None."""
        return self.terms_on(dates.today())

    @property
    def assigned_schedule(self):
        """El horario de la asignación más reciente, que puede iniciar en el futuro."""
        assignments = self.assignment_list()
        return assignments[-1].schedule if assignments else None


class ScheduleAssignment(models.Model):
    """El horario asignado a un empleado a partir de un lunes. Vacío significa sin horario desde entonces."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    schedule = models.ForeignKey(
        Schedule, null=True, blank=True, on_delete=models.PROTECT, related_name="assignments"
    )
    effective_from = models.DateField("vigente desde")  # siempre un lunes

    class Meta:
        ordering = ["effective_from"]
        verbose_name = "asignación de horario"
        verbose_name_plural = "asignaciones de horario"
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="one_assignment_per_employee_week")
        ]

    def __str__(self):
        return f"{self.employee}: {self.schedule or 'sin horario'} desde {self.effective_from}"


class TimeEntry(models.Model):
    """Un tramo de trabajo. Un empleado tiene como máximo un registro abierto a la vez."""

    class Source(models.TextChoices):
        PUNCH = "punch", "Marcación"
        MANUAL = "manual", "Manual"  # ambas horas vienen de una solicitud aprobada
        ADJUSTED = "adjusted", "Salida agregada"  # la salida viene de una solicitud aprobada
        ADMIN = "admin", "Corrección de admin"  # agregado o cambiado directamente por un admin

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="entries")
    clock_in = models.DateTimeField("entrada")
    clock_out = models.DateTimeField("salida", null=True, blank=True)
    # Se marca cuando el día terminó sin salida. El registro cuenta cero horas
    # hasta que una solicitud aprobada o corrección aporte la hora faltante.
    missing_out = models.BooleanField("sin salida", default=False)
    source = models.CharField("origen", max_length=10, choices=Source.choices, default=Source.PUNCH)
    note = models.TextField("nota", blank=True, max_length=1000)  # motivo de la última corrección de admin
    memo = models.CharField("nota de trabajo", blank=True, max_length=300)  # lo que anota el empleado sobre este tramo
    # Verificación "¿Sigues trabajando?" (ver TimerCheck): cuándo se respondió por última vez
    # y si el sistema detuvo el registro por falta de respuesta.
    confirmed_at = models.DateTimeField(null=True, blank=True)
    auto_stopped = models.BooleanField(default=False)
    request = models.ForeignKey(
        "MissingTimeRequest", null=True, blank=True, on_delete=models.SET_NULL, related_name="entries"
    )

    class Meta:
        ordering = ["clock_in"]
        verbose_name = "registro de tiempo"
        verbose_name_plural = "registros de tiempo"
        indexes = [models.Index(fields=["employee", "clock_in"])]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=Q(clock_out__isnull=True, missing_out=False),
                name="one_open_entry_per_employee",
            ),
            models.CheckConstraint(
                condition=Q(clock_out__isnull=True) | Q(clock_out__gte=F("clock_in")),
                name="clock_out_not_before_clock_in",
            ),
        ]

    is_break = False  # permite a las plantillas distinguir registros de descansos

    def __str__(self):
        return f"{self.employee} {self.clock_in:%Y-%m-%d %H:%M}"

    @property
    def is_open(self):
        return self.clock_out is None and not self.missing_out

    @property
    def has_pending_fix(self):
        return self.fix_requests.filter(status=RequestStatus.PENDING).exists()

    def worked(self, now=None):
        if self.clock_out:
            return self.clock_out - self.clock_in
        if self.missing_out:
            return timedelta(0)
        return max((now or timezone.now()) - self.clock_in, timedelta(0))


class Correction(models.Model):
    """Historial cuando un administrador agrega, modifica o elimina un registro de tiempo, con su motivo."""

    class Action(models.TextChoices):
        ADDED = "added", "Agregado"
        CHANGED = "changed", "Modificado"
        REMOVED = "removed", "Eliminado"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="corrections")
    entry = models.ForeignKey(
        TimeEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="corrections"
    )
    admin = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField("acción", max_length=10, choices=Action.choices)
    old_clock_in = models.DateTimeField(null=True, blank=True)
    old_clock_out = models.DateTimeField(null=True, blank=True)
    new_clock_in = models.DateTimeField(null=True, blank=True)
    new_clock_out = models.DateTimeField(null=True, blank=True)
    reason = models.TextField("motivo", max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "corrección"
        verbose_name_plural = "correcciones"

    def __str__(self):
        return f"{self.get_action_display()} para {self.employee}"


class RequestStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    CANCELLED = "cancelled", "Cancelada"


class BaseRequest(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="%(class)ss")
    reason = models.TextField("motivo", max_length=1000)
    status = models.CharField(
        "estado", max_length=10, choices=RequestStatus.choices, default=RequestStatus.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decision_note = models.TextField("nota de decisión", blank=True, max_length=1000)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    @property
    def is_pending(self):
        return self.status == RequestStatus.PENDING


class MissingTimeRequest(BaseRequest):
    """Solicitud de un empleado por tiempo trabajado que no marcó con el botón."""

    kind = "missing-time"

    clock_in = models.DateTimeField("entrada")
    clock_out = models.DateTimeField("salida")
    # Se asigna cuando la solicitud aporta la salida de un registro existente
    # en lugar de crear uno completamente nuevo.
    entry = models.ForeignKey(
        TimeEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="fix_requests"
    )

    class Meta(BaseRequest.Meta):
        verbose_name = "solicitud de tiempo faltante"
        verbose_name_plural = "solicitudes de tiempo faltante"

    def __str__(self):
        return f"{self.employee}: tiempo faltante {self.clock_in:%Y-%m-%d}"

    @property
    def duration(self):
        return self.clock_out - self.clock_in


class LeaveRequest(BaseRequest):
    class LeaveType(models.TextChoices):
        ANNUAL = "annual", "Anual"
        CASUAL = "casual", "Personal"
        SICK = "sick", "Incapacidad"
        UNPAID = "unpaid", "No remunerado"
        OTHER = "other", "Otro"

    kind = "leave"

    leave_type = models.CharField(
        "tipo de permiso", max_length=10, choices=LeaveType.choices, default=LeaveType.CASUAL
    )
    start_date = models.DateField("primer día")
    end_date = models.DateField("último día")

    class Meta(BaseRequest.Meta):
        verbose_name = "solicitud de permiso"
        verbose_name_plural = "solicitudes de permiso"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="leave_end_not_before_start",
                violation_error_message="El último día no puede ser anterior al primer día.",
            ),
        ]

    def __str__(self):
        return f"{self.employee}: permiso {self.get_leave_type_display().lower()} {self.start_date}"

    @property
    def calendar_days(self):
        return (self.end_date - self.start_date).days + 1


class LeaveAllowance(models.Model):
    """Días de un tipo de permiso que cada empleado puede tomar al año.

    Un tipo de permiso sin fila aquí no tiene límite.
    """

    leave_type = models.CharField("tipo de permiso", max_length=10, choices=LeaveRequest.LeaveType.choices, unique=True)
    days = models.PositiveSmallIntegerField("días")

    class Meta:
        verbose_name = "cupo de permiso"
        verbose_name_plural = "cupos de permiso"

    def __str__(self):
        return f"{self.get_leave_type_display()}: {self.days} días al año"


class TimerCheck(models.Model):
    """Verificación "¿Sigues trabajando?" en un temporizador activo. Solo existe una fila.

    Si un temporizador corre este tiempo sin pausas, se muestra la pregunta. Sin respuesta
    a tiempo, se marca la salida en el momento en que se hizo la pregunta.
    """

    ask_after_hours = models.DecimalField(
        "preguntar después de (horas)",
        max_digits=4,
        decimal_places=2,
        default=Decimal("4"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("24"))],
        help_text="Cuánto tiempo puede correr el temporizador antes de preguntar al empleado. 0 desactiva la verificación.",
    )
    answer_minutes = models.PositiveSmallIntegerField(
        "esperar respuesta (minutos)",
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
        help_text="Si no hay respuesta en este tiempo, el temporizador se detiene.",
    )

    class Meta:
        verbose_name = "verificación de temporizador"
        verbose_name_plural = "verificaciones de temporizador"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def enabled(self):
        return self.ask_after_hours > 0

    @property
    def ask_after(self):
        return timedelta(hours=float(self.ask_after_hours))

    @property
    def answer_within(self):
        return timedelta(minutes=self.answer_minutes)

    def __str__(self):
        return "Verificación de temporizador"


class LaborRuleConfig(models.Model):
    """Parámetros legales de jornada, franja nocturna, recargos y topes de alerta, versionados por fecha."""

    effective_from = models.DateField("vigente desde", unique=True)
    max_weekly_hours = models.DecimalField(
        "jornada máxima semanal (horas)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("42.00"),
        validators=[MinValueValidator(Decimal("1")), MaxValueValidator(Decimal("48"))],
        help_text="Tope máximo legal de horas semanales (p. ej. 42 h).",
    )
    min_daily_hours = models.DecimalField(
        "jornada mínima diaria (horas)",
        max_digits=4,
        decimal_places=2,
        default=Decimal("4.00"),
        validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("12"))],
    )
    max_daily_hours = models.DecimalField(
        "jornada máxima diaria (horas)",
        max_digits=4,
        decimal_places=2,
        default=Decimal("9.00"),
        validators=[MinValueValidator(Decimal("1")), MaxValueValidator(Decimal("12"))],
    )
    night_start = models.TimeField("inicio jornada nocturna", default=dt_time(19, 0))
    night_end = models.TimeField("fin jornada nocturna", default=dt_time(6, 0))
    max_daily_overtime_hours = models.DecimalField(
        "límite alerta extra diaria (horas)",
        max_digits=4,
        decimal_places=2,
        default=Decimal("2.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("12"))],
        help_text="Activa alerta sin bloquear el cálculo ni la exportación.",
    )
    max_weekly_overtime_hours = models.DecimalField(
        "límite alerta extra semanal (horas)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("12.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("60"))],
        help_text="Activa alerta semanal sin bloquear el cálculo ni la exportación.",
    )
    night_surcharge_pct = models.DecimalField(
        "recargo nocturno ordinario (%)", max_digits=5, decimal_places=2, default=Decimal("35.00")
    )
    sunday_holiday_surcharge_pct = models.DecimalField(
        "recargo dominical/festivo ordinario (%)", max_digits=5, decimal_places=2, default=Decimal("90.00")
    )
    overtime_day_pct = models.DecimalField(
        "hora extra diurna (%)", max_digits=5, decimal_places=2, default=Decimal("25.00")
    )
    overtime_night_pct = models.DecimalField(
        "hora extra nocturna (%)", max_digits=5, decimal_places=2, default=Decimal("75.00")
    )
    overtime_holiday_day_pct = models.DecimalField(
        "hora extra dominical/festiva diurna (%)", max_digits=5, decimal_places=2, default=Decimal("115.00")
    )
    overtime_holiday_night_pct = models.DecimalField(
        "hora extra dominical/festiva nocturna (%)", max_digits=5, decimal_places=2, default=Decimal("165.00")
    )

    class Meta:
        ordering = ["-effective_from"]
        verbose_name = "regla laboral"
        verbose_name_plural = "reglas laborales"

    def __str__(self):
        return f"Regla laboral desde {self.effective_from} ({self.max_weekly_hours.normalize():f}h)"

    @classmethod
    def for_date(cls, day):
        """La regla vigente en `day`, o la más antigua, o una instancia con los valores predeterminados."""
        rule = cls.objects.filter(effective_from__lte=day).order_by("-effective_from").first()
        if rule is not None:
            return rule
        first = cls.objects.order_by("effective_from").first()
        return first if first is not None else cls(effective_from=dates.BEGINNING)


class TimeEntryBreakdown(models.Model):
    """Desglose calculado en minutos de un registro de tiempo cerrado, para reportes Art. 12 y vistas SQL."""

    entry = models.OneToOneField(TimeEntry, on_delete=models.CASCADE, related_name="breakdown")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="entry_breakdowns")
    work_date = models.DateField("fecha laboral", db_index=True)
    ordinary_day_minutes = models.PositiveIntegerField(default=0)
    ordinary_night_minutes = models.PositiveIntegerField(default=0)
    ordinary_holiday_day_minutes = models.PositiveIntegerField(default=0)
    ordinary_holiday_night_minutes = models.PositiveIntegerField(default=0)
    overtime_day_minutes = models.PositiveIntegerField(default=0)
    overtime_night_minutes = models.PositiveIntegerField(default=0)
    overtime_holiday_day_minutes = models.PositiveIntegerField(default=0)
    overtime_holiday_night_minutes = models.PositiveIntegerField(default=0)
    total_worked_minutes = models.PositiveIntegerField(default=0)
    total_ordinary_minutes = models.PositiveIntegerField(default=0)
    total_overtime_minutes = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["work_date", "entry_id"]
        verbose_name = "desglose de marcación"
        verbose_name_plural = "desgloses de marcaciones"

    def __str__(self):
        return f"{self.employee} {self.work_date}: {self.total_worked_minutes} min"


class DailyAttendanceSummary(models.Model):
    """Resumen diario calculado por empleado para vista previa, ERP y vistas SQL."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="daily_summaries")
    date = models.DateField("fecha", db_index=True)
    week_start = models.DateField("inicio de semana", db_index=True)
    schedule_name = models.CharField("horario", max_length=100, blank=True)
    daily_target_minutes = models.PositiveIntegerField(default=0)
    is_rest_or_holiday = models.BooleanField(default=False)
    is_holiday = models.BooleanField(default=False)
    is_sunday = models.BooleanField(default=False)
    ordinary_day_minutes = models.PositiveIntegerField(default=0)
    ordinary_night_minutes = models.PositiveIntegerField(default=0)
    ordinary_holiday_day_minutes = models.PositiveIntegerField(default=0)
    ordinary_holiday_night_minutes = models.PositiveIntegerField(default=0)
    overtime_day_minutes = models.PositiveIntegerField(default=0)
    overtime_night_minutes = models.PositiveIntegerField(default=0)
    overtime_holiday_day_minutes = models.PositiveIntegerField(default=0)
    overtime_holiday_night_minutes = models.PositiveIntegerField(default=0)
    total_worked_minutes = models.PositiveIntegerField(default=0)
    total_ordinary_minutes = models.PositiveIntegerField(default=0)
    total_overtime_minutes = models.PositiveIntegerField(default=0)
    leave_minutes = models.PositiveIntegerField(default=0)
    leave_type = models.CharField(max_length=10, blank=True)
    daily_overtime_exceeded = models.BooleanField(default=False)
    daily_overtime_excess_minutes = models.PositiveIntegerField(default=0)
    weekly_overtime_exceeded = models.BooleanField(default=False)
    weekly_overtime_excess_minutes = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "employee_id"]
        verbose_name = "resumen diario de asistencia"
        verbose_name_plural = "resúmenes diarios de asistencia"
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="one_summary_per_employee_day")
        ]

    def __str__(self):
        return f"{self.employee} {self.date}: {self.total_worked_minutes} min"


class Holiday(models.Model):
    date = models.DateField("fecha")
    name = models.CharField("nombre", max_length=120)
    recurs_yearly = models.BooleanField(
        "se repite cada año",
        default=False,
        help_text="Para festivos que caen en la misma fecha cada año.",
    )
    tentative = models.BooleanField(
        "tentativo",
        default=False,
        help_text="Para fechas pendientes de confirmación. Desmárcalo cuando la fecha esté confirmada.",
    )

    class Meta:
        ordering = ["date", "name"]
        verbose_name = "festivo"
        verbose_name_plural = "festivos"
        constraints = [models.UniqueConstraint(fields=["date", "name"], name="unique_holiday_date_name")]

    def __str__(self):
        return f"{self.name} ({self.date})"


class LoginThrottle(models.Model):
    """Cuenta contraseñas incorrectas por usuario para bloquear el inicio de sesión temporalmente."""

    username = models.CharField("usuario", max_length=150, unique=True)  # en minúsculas; la cuenta puede no existir
    failures = models.PositiveSmallIntegerField("intentos fallidos", default=0)
    window_start = models.DateTimeField("inicio de ventana")
    locked_until = models.DateTimeField("bloqueado hasta", null=True, blank=True)

    class Meta:
        verbose_name = "bloqueo de acceso"
        verbose_name_plural = "bloqueos de acceso"

    def __str__(self):
        return self.username
