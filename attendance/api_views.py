"""Endpoints REST JSON para integración con ERP/Nómina."""

from datetime import date, timedelta

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import erp, services
from .models import Employee


def _authorized(request):
    if request.user.is_authenticated and request.user.is_staff:
        return True
    configured = getattr(settings, "ERP_API_TOKEN", "")
    if not configured:
        return False
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else request.headers.get("X-API-Key", "").strip()
    return bool(token and token == configured)


def _parse_filters(request):
    today = services.today()
    default_start = services.week_start(today)
    default_end = default_start + timedelta(days=6)
    try:
        start = date.fromisoformat(request.GET["start"]) if request.GET.get("start") else default_start
        end = date.fromisoformat(request.GET["end"]) if request.GET.get("end") else default_end
    except ValueError:
        return None, None, None, None, JsonResponse(
            {"error": "Formato de fecha inválido. Usa YYYY-MM-DD en 'start' y 'end'."}, status=400
        )
    if end < start:
        return None, None, None, None, JsonResponse(
            {"error": "'end' no puede ser anterior a 'start'."}, status=400
        )

    employee = None
    if request.GET.get("employee_id"):
        try:
            employee = Employee.objects.get(pk=int(request.GET["employee_id"]), is_staff=False)
        except (ValueError, Employee.DoesNotExist):
            return None, None, None, None, JsonResponse({"error": "Empleado no encontrado."}, status=404)

    doc = request.GET.get("document_number", "").strip() or None
    return start, end, employee, doc, None


@require_GET
def erp_summary(request):
    if not _authorized(request):
        return JsonResponse({"error": "No autorizado. Inicia sesión como administrador o envía un token válido."}, status=401)
    start, end, employee, doc, err = _parse_filters(request)
    if err is not None:
        return err
    if doc and employee is None:
        emp_by_doc = Employee.objects.filter(document_number=doc, is_staff=False).first()
        if emp_by_doc:
            erp.sync_period(start, end, employee=emp_by_doc)
    else:
        erp.sync_period(start, end, employee=employee)

    rows = [erp.serialize_summary(item) for item in erp.summary_queryset(start, end, employee=employee, document_number=doc)]
    return JsonResponse(
        {
            "meta": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "count": len(rows),
                "generated_at": timezone.now().isoformat(),
            },
            "results": rows,
        }
    )


@require_GET
def erp_entries(request):
    if not _authorized(request):
        return JsonResponse({"error": "No autorizado. Inicia sesión como administrador o envía un token válido."}, status=401)
    start, end, employee, doc, err = _parse_filters(request)
    if err is not None:
        return err
    if doc and employee is None:
        emp_by_doc = Employee.objects.filter(document_number=doc, is_staff=False).first()
        if emp_by_doc:
            erp.sync_period(start, end, employee=emp_by_doc)
    else:
        erp.sync_period(start, end, employee=employee)

    rows = [erp.serialize_entry(item) for item in erp.entry_queryset(start, end, employee=employee, document_number=doc)]
    return JsonResponse(
        {
            "meta": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "count": len(rows),
                "generated_at": timezone.now().isoformat(),
            },
            "results": rows,
        }
    )
