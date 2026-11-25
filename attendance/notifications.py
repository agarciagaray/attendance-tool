"""Avisos por correo electrónico.

El envío nunca debe interrumpir el trabajo que reporta: si el servidor de correo
está caído o mal configurado, el fallo se registra en el log y la página continúa.
"""

import logging
import smtplib
import socket
import ssl

from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from .dates import clock_text, date_text, duration_text
from .models import Correction, Employee, RequestStatus

logger = logging.getLogger(__name__)


def _send(subject, lines, recipients):
    recipients = [address for address in recipients if address]
    if not recipients:
        return 0
    try:
        return send_mail(f"[Control de Asistencia] {subject}", "\n".join(lines), None, recipients)
    except Exception:
        logger.exception("No se pudo enviar el correo %r a %s", subject, recipients)
        return 0


def admin_addresses():
    return list(
        Employee.objects.filter(is_staff=True, is_active=True).exclude(email="").values_list("email", flat=True)
    )


def send_test(http_request, recipient):
    """Envía un mensaje y, a diferencia de los avisos, deja pasar errores para detectar fallos de configuración."""
    sender = http_request.user.display_name
    send_mail(
        "[Control de Asistencia] Correo de prueba",
        "\n".join(
            [
                f"Esta es una prueba de Control de Asistencia, enviada por {sender} a las {clock_text(timezone.now())}.",
                "",
                "Si estás leyendo esto, la configuración del servidor de correo funciona.",
                "",
                http_request.build_absolute_uri(reverse("home")),
            ]
        ),
        None,
        [recipient],
        fail_silently=False,
    )


def explain_failure(error):
    """Explicación clara de un envío fallido, seguida de lo que respondió el servidor o la red."""
    if isinstance(error, smtplib.SMTPAuthenticationError):
        reason = (
            "El servidor de correo rechazó el usuario o la contraseña. "
            "Revisa EMAIL_HOST_USER y EMAIL_HOST_PASSWORD (la clave API)."
        )
    elif isinstance(error, (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError)):
        reason = (
            "El servidor de correo rechazó el mensaje. "
            "Verifica que EMAIL_FROM sea una dirección autorizada para enviar."
        )
    elif isinstance(error, smtplib.SMTPServerDisconnected):
        reason = (
            "El servidor de correo cerró la conexión. "
            "Esto suele indicar que el puerto y el cifrado no coinciden."
        )
    elif isinstance(error, ssl.SSLError):
        reason = (
            "No se pudo establecer la conexión segura. "
            "El puerto 587 requiere EMAIL_USE_SSL=false; el puerto 465 requiere EMAIL_USE_SSL=true."
        )
    elif isinstance(error, (TimeoutError, ConnectionError, socket.gaierror)):
        reason = (
            "No se pudo conectar con el servidor de correo. Revisa EMAIL_HOST y EMAIL_PORT, "
            "y asegúrate de que este equipo tenga salida por ese puerto."
        )
    else:
        reason = "El servidor de correo reportó un problema."
    return f"{reason} Detalles: {type(error).__name__}: {error}"


def _day(moment):
    return date_text(moment, "D j M Y")


def _span(clock_in, clock_out):
    return f"{_day(clock_in)}, {clock_text(clock_in)} a {clock_text(clock_out) if clock_out else 'sin salida'}"


def describe(item):
    """Describe una solicitud en una línea de texto plano."""
    if item.kind == "leave":
        days = "1 día" if item.calendar_days == 1 else f"{item.calendar_days} días"
        dates = date_text(item.start_date, "D j M Y")
        if item.end_date != item.start_date:
            dates += f" al {date_text(item.end_date, 'D j M Y')}"
        return f"Permiso {item.get_leave_type_display().lower()}, {dates} ({days})"
    what = "Salida faltante" if item.entry_id else "Tiempo faltante"
    return f"{what}: {_span(item.clock_in, item.clock_out)} ({duration_text(item.duration)})"


def request_submitted(http_request, item):
    """Avisa a los administradores que hay una solicitud esperando decisión."""
    return _send(
        f"Nueva solicitud de {item.employee.display_name}",
        [
            f"{item.employee.display_name} envió una solicitud:",
            "",
            describe(item),
            f"Motivo: {item.reason}",
            "",
            f"Apruébala o recházala aquí: {http_request.build_absolute_uri(reverse('manage_requests'))}",
        ],
        admin_addresses(),
    )


def request_decided(http_request, item):
    """Avisa al empleado qué ocurrió con su solicitud."""
    outcome = {
        RequestStatus.APPROVED: "aprobada",
        RequestStatus.REJECTED: "rechazada",
        RequestStatus.CANCELLED: "cancelada",
    }[item.status]
    lines = [f"Tu solicitud fue {outcome}:", "", describe(item)]
    if item.decision_note:
        lines += [f"Nota de {item.decided_by.display_name if item.decided_by else 'administración'}: {item.decision_note}"]
    lines += ["", f"Tus solicitudes: {http_request.build_absolute_uri(reverse('my_requests'))}"]
    return _send(f"Tu solicitud fue {outcome}", lines, [item.employee.email])


def timer_stopped(http_request, entry):
    """Avisa al empleado que su temporizador se detuvo porque no respondió "¿Sigues trabajando?"."""
    return _send(
        "Tu temporizador se detuvo",
        [
            f"Tu temporizador se detuvo a las {clock_text(entry.clock_out)} el "
            f"{date_text(entry.clock_out, 'D j M Y')}.",
            f"Llevaba corriendo {duration_text(entry.clock_out - (entry.confirmed_at or entry.clock_in))} "
            'y no se respondió a "¿Sigues trabajando?".',
            "",
            "Si seguías trabajando, marca tu entrada de nuevo y registra el tiempo intermedio como tiempo faltante:",
            http_request.build_absolute_uri(reverse("missing_time_new")),
        ],
        [entry.employee.email],
    )


def record_corrected(http_request, correction):
    """Avisa al empleado que un administrador modificó su registro de asistencia."""
    if correction.action == Correction.Action.ADDED:
        change = [f"Agregado: {_span(correction.new_clock_in, correction.new_clock_out)}"]
    elif correction.action == Correction.Action.REMOVED:
        change = [f"Eliminado: {_span(correction.old_clock_in, correction.old_clock_out)}"]
    else:
        change = [
            f"Antes: {_span(correction.old_clock_in, correction.old_clock_out)}",
            f"Ahora: {_span(correction.new_clock_in, correction.new_clock_out)}",
        ]
    admin = correction.admin.display_name if correction.admin else "Un administrador"
    return _send(
        "Se corrigió tu registro de asistencia",
        [
            f"{admin} corrigió tu registro de asistencia.",
            "",
            *change,
            f"Motivo: {correction.reason}",
            "",
            f"Tu calendario: {http_request.build_absolute_uri(reverse('calendar'))}",
        ],
        [correction.employee.email],
    )
