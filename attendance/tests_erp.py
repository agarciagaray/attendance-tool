import csv
import io
from datetime import date, datetime, time
from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from . import services
from .models import Employee, LaborRuleConfig, LeaveRequest, Schedule, TimeEntry


def aware(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class ErpAndReportingTests(TestCase):
    def setUp(self):
        self.monday = date(2026, 9, 7)
        self.admin = Employee.objects.create_user(
            username="admin_erp",
            password="pass-test-123",
            is_staff=True,
            start_date=date(2026, 1, 1),
        )
        self.employee = Employee.objects.create_user(
            username="laura",
            password="pass-test-123",
            first_name="Laura",
            last_name="Gómez",
            document_number="1098765432",
            contract_type=Employee.ContractType.INDEFINITE,
            start_date=date(2026, 1, 1),
        )
        self.schedule = Schedule.objects.create(name="42h Estándar")
        services.set_schedule_terms(
            self.schedule,
            Decimal("42.00"),
            {"monday": True, "tuesday": True, "wednesday": True, "thursday": True, "friday": True},
            date(2026, 1, 1),
        )
        services.assign_schedule(self.employee, self.schedule, date(2026, 1, 1))
        # Lunes: 14:00 a 00:00 (10h = 600 min: 300 ord diurna, 204 ord nocturna, 96 extra nocturna)
        TimeEntry.objects.create(
            employee=self.employee,
            clock_in=aware(self.monday, 14, 0),
            clock_out=aware(self.monday + timezone.timedelta(days=1), 0, 0),
            memo="Soporte nocturno",
        )

    def test_surcharges_preview_and_labor_rules_views(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse("manage_surcharges"),
            {"start": self.monday.isoformat(), "end": self.monday.isoformat()},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Laura Gómez")
        self.assertContains(resp, "1098765432")

        rules_resp = self.client.get(reverse("manage_labor_rules"))
        self.assertEqual(rules_resp.status_code, 200)
        self.assertContains(rules_resp, "42")

        create_resp = self.client.post(
            reverse("manage_labor_rule_new"),
            {
                "effective_from": "2027-01-01",
                "max_weekly_hours": "42.00",
                "min_daily_hours": "4.00",
                "max_daily_hours": "9.00",
                "night_start": "19:00",
                "night_end": "06:00",
                "max_daily_overtime_hours": "2.00",
                "max_weekly_overtime_hours": "12.00",
                "night_surcharge_pct": "35.00",
                "sunday_holiday_surcharge_pct": "100.00",
                "overtime_day_pct": "25.00",
                "overtime_night_pct": "75.00",
                "overtime_holiday_day_pct": "125.00",
                "overtime_holiday_night_pct": "175.00",
            },
        )
        self.assertRedirects(create_resp, reverse("manage_labor_rules"))
        self.assertTrue(LaborRuleConfig.objects.filter(effective_from=date(2027, 1, 1)).exists())

    def test_art12_and_erp_exports_csv_and_xlsx(self):
        self.client.force_login(self.admin)
        params = {"start": self.monday.isoformat(), "end": self.monday.isoformat()}

        # Art. 12 CSV
        art12_csv = self.client.get(reverse("manage_export_art12_csv"), params)
        self.assertEqual(art12_csv.status_code, 200)
        text = art12_csv.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "1098765432")
        self.assertEqual(rows[1][6], "10.0")

        # Art. 12 XLSX
        art12_xlsx = self.client.get(reverse("manage_export_art12_xlsx"), params)
        self.assertEqual(art12_xlsx.status_code, 200)
        wb = load_workbook(io.BytesIO(art12_xlsx.content))
        self.assertIn("Detalle Art. 12 Ley 2466", wb.sheetnames)
        self.assertIn("Resumen por empleado", wb.sheetnames)
        ws = wb["Detalle Art. 12 Ley 2466"]
        self.assertEqual(ws.cell(row=2, column=2).value, "1098765432")
        self.assertEqual(ws.cell(row=2, column=7).value, 10.0)

        # ERP CSV y XLSX
        erp_csv = self.client.get(reverse("manage_export_erp_csv"), params)
        self.assertEqual(erp_csv.status_code, 200)
        erp_rows = list(csv.reader(io.StringIO(erp_csv.content.decode("utf-8-sig"))))
        self.assertEqual(erp_rows[1][0], "1098765432")
        self.assertEqual(erp_rows[1][8], "10.0")  # horas_trabajadas
        self.assertEqual(erp_rows[1][10], "3.4")  # horas_recargo_nocturno (204m = 3.4h)
        self.assertEqual(erp_rows[1][14], "1.6")  # horas_extra_nocturna (96m = 1.6h)

        erp_xlsx = self.client.get(reverse("manage_export_erp_xlsx"), params)
        self.assertEqual(erp_xlsx.status_code, 200)
        wb_erp = load_workbook(io.BytesIO(erp_xlsx.content))
        self.assertEqual(wb_erp["ERP Nomina"].cell(row=2, column=9).value, 10.0)

    @override_settings(ERP_API_TOKEN="secret-erp-token")
    def test_erp_rest_api_and_sql_views_conciliation(self):
        # Sin autenticación -> 401
        unauth = self.client.get(reverse("api_erp_summary"))
        self.assertEqual(unauth.status_code, 401)

        # Con Bearer token -> 200 y datos exactos
        params = {"start": self.monday.isoformat(), "end": self.monday.isoformat(), "document_number": "1098765432"}
        summary_resp = self.client.get(
            reverse("api_erp_summary"),
            params,
            HTTP_AUTHORIZATION="Bearer secret-erp-token",
        )
        self.assertEqual(summary_resp.status_code, 200)
        payload = summary_resp.json()
        self.assertEqual(payload["meta"]["count"], 1)
        row = payload["results"][0]
        self.assertEqual(row["cedula"], "1098765432")
        self.assertEqual(row["horas_trabajadas"], 10.0)
        self.assertEqual(row["horas_ord_diurna"], 5.0)
        self.assertEqual(row["horas_recargo_nocturno"], 3.4)
        self.assertEqual(row["horas_extra_nocturna"], 1.6)

        entries_resp = self.client.get(
            reverse("api_erp_entries"),
            params,
            HTTP_X_API_KEY="secret-erp-token",
        )
        self.assertEqual(entries_resp.status_code, 200)
        self.assertEqual(entries_resp.json()["meta"]["count"], 1)
        self.assertEqual(entries_resp.json()["results"][0]["memo"], "Soporte nocturno")

        # Conciliación directa con vistas SQL en SQLite
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT cedula, horas_trabajadas, horas_ord_diurna, horas_recargo_nocturno, horas_extra_nocturna "
                "FROM v_erp_resumen_diario WHERE cedula = %s AND fecha = %s",
                ["1098765432", self.monday.isoformat()],
            )
            sql_summary = cursor.fetchone()
            self.assertEqual(sql_summary, ("1098765432", 10.0, 5.0, 3.4, 1.6))

            cursor.execute(
                "SELECT cedula, horas_trabajadas, memo FROM v_erp_desglose_marcaciones WHERE cedula = %s",
                ["1098765432"],
            )
            sql_entry = cursor.fetchone()
            self.assertEqual(sql_entry, ("1098765432", 10.0, "Soporte nocturno"))

    def test_schedule_form_template_compensatory_leave_and_employee_calendar_surcharges(self):
        # 1. Nueva plantilla compacta de horarios
        self.client.force_login(self.admin)
        sched_resp = self.client.get(reverse("manage_schedule_edit", args=[self.schedule.pk]))
        self.assertEqual(sched_resp.status_code, 200)
        self.assertContains(sched_resp, "schedule-sum-status")
        self.assertContains(sched_resp, "Horas personalizadas (opcional)")

        # 2. Permiso de tipo Compensatorio -> reflejado en API y vista SQL
        tue = self.monday + timezone.timedelta(days=1)
        leave = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=LeaveRequest.LeaveType.COMPENSATORY,
            start_date=tue,
            end_date=tue,
            reason="Compensatorio por domingo trabajado",
        )
        services.decide(leave, self.admin, approve=True)

        summary_resp = self.client.get(
            reverse("api_erp_summary"),
            {"start": tue.isoformat(), "end": tue.isoformat(), "document_number": "1098765432"},
        )
        self.assertEqual(summary_resp.status_code, 200)
        comp_row = summary_resp.json()["results"][0]
        self.assertEqual(comp_row["tipo_permiso"], "compensatory")
        self.assertEqual(comp_row["tipo_permiso_label"], "Compensatorio")
        self.assertTrue(comp_row["es_compensatorio"])

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tipo_permiso, es_compensatorio FROM v_erp_resumen_diario WHERE cedula = %s AND fecha = %s",
                ["1098765432", tue.isoformat()],
            )
            self.assertEqual(cursor.fetchone(), ("compensatory", 1))

        # 3. Portal del empleado (/calendar/) muestra resumen de recargos y extras
        self.client.force_login(self.employee)
        cal_resp = self.client.get(reverse("calendar"), {"month": "2026-09"})
        self.assertEqual(cal_resp.status_code, 200)
        self.assertContains(cal_resp, "Resumen de recargos y horas extras")
        self.assertContains(cal_resp, "Recargo nocturno")
        self.assertContains(cal_resp, "3h 24m")
