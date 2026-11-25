"""El menú y la sección a la que pertenece cada página.

Tanto la barra lateral como las migas de pan leen desde aquí para mantenerse
siempre sincronizadas.
"""

from dataclasses import dataclass

from django.urls import reverse


@dataclass(frozen=True)
class Section:
    label: str
    url_name: str
    icon: str
    prefixes: tuple  # una página pertenece aquí cuando el nombre de su URL empieza con uno de estos prefijos

    def owns(self, url_name):
        return any(url_name.startswith(prefix) for prefix in self.prefixes)


ADMIN_MENU = (
    Section("Hoy", "manage_dashboard", "pulse", ("manage_dashboard",)),
    Section("Asistencia", "manage_review", "bars", ("manage_review", "manage_monthly")),
    Section("Solicitudes", "manage_requests", "inbox", ("manage_request",)),
    Section("Empleados", "manage_employees", "people", ("manage_employee", "manage_entry")),
    Section("Horarios", "manage_schedules", "grid", ("manage_schedule",)),
    Section("Festivos", "manage_holidays", "star", ("manage_holiday",)),
    Section("Permisos", "manage_leave", "briefcase", ("manage_leave",)),
    Section("Correo", "manage_email", "mail", ("manage_email",)),
    Section("Configuración", "manage_settings", "sliders", ("manage_settings",)),
)

EMPLOYEE_MENU = (
    Section("Reloj", "home", "clock", ("home",)),
    Section("Calendario", "calendar", "calendar", ("calendar", "entry_memo")),
    Section("Solicitudes", "my_requests", "inbox", ("my_requests", "missing_time_new", "leave_new")),
)

# Se muestra al pie de la barra lateral para todos los usuarios.
ACCOUNT = Section("Cambiar contraseña", "password_change", "key", ("password_change",))


def menu_for(user):
    return ADMIN_MENU if user.is_staff else EMPLOYEE_MENU


def section_for(user, url_name):
    """La sección a la que pertenece una página, o None si está fuera del menú de este usuario."""
    for section in (*menu_for(user), ACCOUNT):
        if section.owns(url_name):
            return section
    return None


def crumb(label, url_name=None, *args, query=""):
    """Un paso en las migas de pan debajo de la sección.

    Las vistas los listan en `breadcrumbs`, terminando con la página actual. Omite
    `url_name` cuando el paso no deba ser un enlace.
    """
    return {"label": label, "url": reverse(url_name, args=args) + query if url_name else None}
