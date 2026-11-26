"""Exportaciones (Art. 12 Ley 2466 y ERP Nómina) en Excel/CSV y serialización para API REST."""

import csv
import io
from collections import defaultdict
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import dates, labor, services
from .models import DailyAttendanceSummary, Employee, TimeEntryBreakdown


def _hours_decimal(minutes):
    return round((minutes or 0) / 60.0, 2)


def _csv_safe(value):
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def sync_period(start, end, employee=None):
    """Asegura que el desglose diario esté calculado para el rango solicitado."""
    qs = Employee.objects.filter(is_staff=False, is_active=True)
    if employee is not None:
        qs = qs.filter(pk=employee.pk)
    for emp in qs:
        labor.sync_range(emp, start, end)


def summary_queryset(start, end, employee=None, document_number=None):
    qs = (
        DailyAttendanceSummary.objects.filter(date__range=(start, end))
        .select_related("employee")
        .order_by("employee__last_name", "employee__first_name", "date")
    )
    if employee is not None:
        qs = qs.filter(employee=employee)
    if document_number:
        qs = qs.filter(employee__document_number=document_number)
    return qs


def entry_queryset(start, end, employee=None, document_number=None):
    qs = (
        TimeEntryBreakdown.objects.filter(work_date__range=(start, end))
        .select_related("employee", "entry", "entry__request")
        .order_by("work_date", "entry__clock_in")
    )
    if employee is not None:
        qs = qs.filter(employee=employee)
    if document_number:
        qs = qs.filter(employee__document_number=document_number)
    return qs


def serialize_summary(row):
    emp = row.employee
    return {
        "employee_id": emp.pk,
        "cedula": emp.document_number or "",
        "usuario": emp.username,
        "nombre_completo": emp.display_name,
        "tipo_contrato": emp.contract_type,
        "tipo_contrato_label": emp.get_contract_type_display(),
        "fecha": row.date.isoformat(),
        "inicio_semana": row.week_start.isoformat(),
        "horario": row.schedule_name,
        "meta_diaria_horas": _hours_decimal(row.daily_target_minutes),
        "es_festivo": row.is_holiday,
        "es_domingo": row.is_sunday,
        "es_descanso_obligatorio": row.is_rest_or_holiday,
        "horas_trabajadas": _hours_decimal(row.total_worked_minutes),
        "horas_ordinarias": _hours_decimal(row.total_ordinary_minutes),
        "horas_extras": _hours_decimal(row.total_overtime_minutes),
        "horas_ord_diurna": _hours_decimal(row.ordinary_day_minutes),
        "horas_recargo_nocturno": _hours_decimal(row.ordinary_night_minutes),
        "horas_ord_festivo_diurna": _hours_decimal(row.ordinary_holiday_day_minutes),
        "horas_ord_festivo_nocturna": _hours_decimal(row.ordinary_holiday_night_minutes),
        "horas_extra_diurna": _hours_decimal(row.overtime_day_minutes),
        "horas_extra_nocturna": _hours_decimal(row.overtime_night_minutes),
        "horas_extra_festivo_diurna": _hours_decimal(row.overtime_holiday_day_minutes),
        "horas_extra_festivo_nocturna": _hours_decimal(row.overtime_holiday_night_minutes),
        "horas_permiso": _hours_decimal(row.leave_minutes),
        "tipo_permiso": row.leave_type,
        "alerta_extra_diaria": row.daily_overtime_exceeded,
        "exceso_extra_diaria_horas": _hours_decimal(row.daily_overtime_excess_minutes),
        "alerta_extra_semanal": row.weekly_overtime_exceeded,
        "exceso_extra_semanal_horas": _hours_decimal(row.weekly_overtime_excess_minutes),
    }


def serialize_entry(item):
    emp = item.employee
    entry = item.entry
    return {
        "entry_id": entry.pk,
        "employee_id": emp.pk,
        "cedula": emp.document_number or "",
        "usuario": emp.username,
        "nombre_completo": emp.display_name,
        "fecha_laboral": item.work_date.isoformat(),
        "entrada": timezone.localtime(entry.clock_in).isoformat(),
        "salida": timezone.localtime(entry.clock_out).isoformat() if entry.clock_out else None,
        "origen": entry.source,
        "horas_trabajadas": _hours_decimal(item.total_worked_minutes),
        "horas_ordinarias": _hours_decimal(item.total_ordinary_minutes),
        "horas_extras": _hours_decimal(item.total_overtime_minutes),
        "horas_ord_diurna": _hours_decimal(item.ordinary_day_minutes),
        "horas_recargo_nocturno": _hours_decimal(item.ordinary_night_minutes),
        "horas_ord_festivo_diurna": _hours_decimal(item.ordinary_holiday_day_minutes),
        "horas_ord_festivo_nocturna": _hours_decimal(item.ordinary_holiday_night_minutes),
        "horas_extra_diurna": _hours_decimal(item.overtime_day_minutes),
        "horas_extra_nocturna": _hours_decimal(item.overtime_night_minutes),
        "horas_extra_festivo_diurna": _hours_decimal(item.overtime_holiday_day_minutes),
        "horas_extra_festivo_nocturna": _hours_decimal(item.overtime_holiday_night_minutes),
        "nota": entry.note or "",
        "memo": entry.memo or "",
    }


def build_preview_context(start, end, employee=None):
    """Agrupa resúmenes diarios por empleado para la vista previa de recargos y extras."""
    sync_period(start, end, employee=employee)
    summaries = list(summary_queryset(start, end, employee=employee))
    by_emp = defaultdict(list)
    for item in summaries:
        by_emp[item.employee_id].append(item)

    employees_data = []
    for emp_id, days in by_emp.items():
        emp = days[0].employee
        totals = {
            "ordinary_day": sum(d.ordinary_day_minutes for d in days),
            "ordinary_night": sum(d.ordinary_night_minutes for d in days),
            "ordinary_holiday": sum(d.ordinary_holiday_day_minutes + d.ordinary_holiday_night_minutes for d in days),
            "overtime_day": sum(d.overtime_day_minutes for d in days),
            "overtime_night": sum(d.overtime_night_minutes for d in days),
            "overtime_holiday": sum(d.overtime_holiday_day_minutes + d.overtime_holiday_night_minutes for d in days),
            "total_overtime": sum(d.total_overtime_minutes for d in days),
            "total_worked": sum(d.total_worked_minutes for d in days),
            "leave": sum(d.leave_minutes for d in days),
            "daily_alerts": sum(1 for d in days if d.daily_overtime_exceeded),
            "weekly_alerts": len({d.week_start for d in days if d.weekly_overtime_exceeded}),
        }
        employees_data.append({"employee": emp, "days": days, "totals": totals})

    return {"employees_data": employees_data, "summaries": summaries}


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def _xlsx_response(filename, sheets):
    """Genera un libro Excel con estilo estándar a partir de una lista de (titulo, headers, rows, numeric_cols)."""
    wb = Workbook()
    default_ws = wb.active
    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    for idx, (title, headers, rows, numeric_cols) in enumerate(sheets):
        ws = default_ws if idx == 0 else wb.create_sheet(title=title)
        ws.title = title
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for row in rows:
            ws.append(row)

        for row_idx in range(2, len(rows) + 2):
            for col_idx in numeric_cols:
                ws.cell(row=row_idx, column=col_idx).number_format = "0.00"

        for col_idx in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            max_len = max([len(str(headers[col_idx - 1]))] + [len(str(r[col_idx - 1])) for r in rows])
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 42)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- Exportación Art. 12 Ley 2466 de 2025 -----------------------------------

ART12_HEADERS = [
    "Fecha",
    "Cédula",
    "Empleado",
    "Tipo de contrato",
    "Horario",
    "Día descanso/festivo",
    "Total trabajado (h)",
    "Ord. diurna (h)",
    "Ord. nocturna (h)",
    "Ord. dom/fest diurna (h)",
    "Ord. dom/fest nocturna (h)",
    "Extra diurna (h)",
    "Extra nocturna (h)",
    "Extra dom/fest diurna (h)",
    "Extra dom/fest nocturna (h)",
    "Total extras (h)",
    "Alerta >2h día",
    "Alerta >12h semana",
    "Observaciones / Permiso",
]


def _art12_rows(start, end, employee=None, csv_mode=False):
    sync_period(start, end, employee=employee)
    summaries = summary_queryset(start, end, employee=employee)
    entries = entry_queryset(start, end, employee=employee)
    notes_by_emp_day = defaultdict(list)
    for item in entries:
        text = item.entry.note or (item.entry.request.reason if item.entry.request else "") or item.entry.memo
        if text:
            notes_by_emp_day[(item.employee_id, item.work_date)].append(text)

    safe = _csv_safe if csv_mode else (lambda v: v)
    rows = []
    tot_by_emp = defaultdict(lambda: {"emp": None, "worked": 0, "ord": 0, "ot": 0, "ot_day": 0, "ot_night": 0, "ot_hol": 0})
    for d in summaries:
        emp = d.employee
        obs = list(notes_by_emp_day.get((emp.pk, d.date), []))
        if d.leave_type:
            obs.append(f"Permiso ({d.leave_type}): {_hours_decimal(d.leave_minutes):.2f} h")
        rest_label = "Festivo" if d.is_holiday else ("Domingo" if d.is_sunday else "No")
        rows.append(
            [
                d.date.isoformat(),
                safe(emp.document_number or ""),
                safe(emp.display_name),
                safe(emp.get_contract_type_display()),
                safe(d.schedule_name or "Sin horario"),
                rest_label,
                _hours_decimal(d.total_worked_minutes),
                _hours_decimal(d.ordinary_day_minutes),
                _hours_decimal(d.ordinary_night_minutes),
                _hours_decimal(d.ordinary_holiday_day_minutes),
                _hours_decimal(d.ordinary_holiday_night_minutes),
                _hours_decimal(d.overtime_day_minutes),
                _hours_decimal(d.overtime_night_minutes),
                _hours_decimal(d.overtime_holiday_day_minutes),
                _hours_decimal(d.overtime_holiday_night_minutes),
                _hours_decimal(d.total_overtime_minutes),
                "Sí" if d.daily_overtime_exceeded else "No",
                "Sí" if d.weekly_overtime_exceeded else "No",
                safe("; ".join(obs)),
            ]
        )
        t = tot_by_emp[emp.pk]
        t["emp"] = emp
        t["worked"] += d.total_worked_minutes
        t["ord"] += d.total_ordinary_minutes
        t["ot"] += d.total_overtime_minutes
        t["ot_day"] += d.overtime_day_minutes
        t["ot_night"] += d.overtime_night_minutes
        t["ot_hol"] += d.overtime_holiday_day_minutes + d.overtime_holiday_night_minutes

    summary_rows = [
        [
            t["emp"].document_number or "",
            t["emp"].display_name,
            t["emp"].get_contract_type_display(),
            _hours_decimal(t["worked"]),
            _hours_decimal(t["ord"]),
            _hours_decimal(t["ot_day"]),
            _hours_decimal(t["ot_night"]),
            _hours_decimal(t["ot_hol"]),
            _hours_decimal(t["ot"]),
        ]
        for t in tot_by_emp.values()
    ]
    return rows, summary_rows


def export_art12_csv(start, end, employee=None):
    rows, _ = _art12_rows(start, end, employee=employee, csv_mode=True)
    return _csv_response(f"art12-ley2466-{start}-a-{end}.csv", ART12_HEADERS, rows)


def export_art12_xlsx(start, end, employee=None):
    rows, summary_rows = _art12_rows(start, end, employee=employee, csv_mode=False)
    summary_headers = [
        "Cédula",
        "Empleado",
        "Tipo de contrato",
        "Total trabajado (h)",
        "Total ordinario (h)",
        "Extra diurna (h)",
        "Extra nocturna (h)",
        "Extra dom/fest (h)",
        "Total extras (h)",
    ]
    return _xlsx_response(
        f"art12-ley2466-{start}-a-{end}.xlsx",
        [
            ("Detalle Art. 12 Ley 2466", ART12_HEADERS, rows, set(range(7, 17))),
            ("Resumen por empleado", summary_headers, summary_rows, set(range(4, 10))),
        ],
    )


# --- Exportación ERP Nómina -------------------------------------------------

ERP_HEADERS = [
    "cedula",
    "usuario",
    "nombre_completo",
    "tipo_contrato",
    "fecha",
    "inicio_semana",
    "es_festivo",
    "es_domingo",
    "horas_trabajadas",
    "horas_ord_diurna",
    "horas_recargo_nocturno",
    "horas_ord_festivo_diurna",
    "horas_ord_festivo_nocturna",
    "horas_extra_diurna",
    "horas_extra_nocturna",
    "horas_extra_festivo_diurna",
    "horas_extra_festivo_nocturna",
    "horas_permiso",
    "tipo_permiso",
    "alerta_extra_diaria",
    "alerta_extra_semanal",
]


def _erp_rows(start, end, employee=None, csv_mode=False):
    sync_period(start, end, employee=employee)
    summaries = summary_queryset(start, end, employee=employee)
    safe = _csv_safe if csv_mode else (lambda v: v)
    rows = []
    for d in summaries:
        data = serialize_summary(d)
        rows.append(
            [
                safe(data["cedula"]),
                safe(data["usuario"]),
                safe(data["nombre_completo"]),
                safe(data["tipo_contrato"]),
                data["fecha"],
                data["inicio_semana"],
                1 if data["es_festivo"] else 0,
                1 if data["es_domingo"] else 0,
                data["horas_trabajadas"],
                data["horas_ord_diurna"],
                data["horas_recargo_nocturno"],
                data["horas_ord_festivo_diurna"],
                data["horas_ord_festivo_nocturna"],
                data["horas_extra_diurna"],
                data["horas_extra_nocturna"],
                data["horas_extra_festivo_diurna"],
                data["horas_extra_festivo_nocturna"],
                data["horas_permiso"],
                safe(data["tipo_permiso"]),
                1 if data["alerta_extra_diaria"] else 0,
                1 if data["alerta_extra_semanal"] else 0,
            ]
        )
    return rows


def export_erp_csv(start, end, employee=None):
    rows = _erp_rows(start, end, employee=employee, csv_mode=True)
    return _csv_response(f"erp-nomina-{start}-a-{end}.csv", ERP_HEADERS, rows)


def export_erp_xlsx(start, end, employee=None):
    rows = _erp_rows(start, end, employee=employee, csv_mode=False)
    return _xlsx_response(
        f"erp-nomina-{start}-a-{end}.xlsx",
        [("ERP Nomina", ERP_HEADERS, rows, set(range(9, 19)))],
    )
