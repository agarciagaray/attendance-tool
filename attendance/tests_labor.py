from datetime import date, datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from . import labor, services
from .forms import EmployeeForm, ScheduleForm
from .models import (
    DailyAttendanceSummary,
    Employee,
    Holiday,
    LaborRuleConfig,
    Schedule,
    TimeEntry,
    TimeEntryBreakdown,
)


def aware(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class LaborEngineTests(TestCase):
    def setUp(self):
        self.monday = date(2026, 9, 7)  # semana sin festivos en Colombia
        self.employee = Employee.objects.create_user(
            username="carlos",
            password="pass-test-123",
            first_name="Carlos",
            last_name="Pérez",
            document_number="1020304050",
            contract_type=Employee.ContractType.INDEFINITE,
            start_date=date(2026, 1, 1),
        )
        self.admin = Employee.objects.create_user(
            username="admin_labor",
            password="pass-test-123",
            is_staff=True,
            start_date=date(2026, 1, 1),
        )
        self.schedule = Schedule.objects.create(name="42h L-V")
        services.set_schedule_terms(
            self.schedule,
            Decimal("42.00"),
            {"monday": True, "tuesday": True, "wednesday": True, "thursday": True, "friday": True},
            date(2026, 1, 1),
        )
        services.assign_schedule(self.employee, self.schedule, date(2026, 1, 1))

    def test_standard_42h_week_has_no_overtime(self):
        # 5 días * 8h 24m = 42h exactas
        for offset in range(5):
            day = self.monday + timezone.timedelta(days=offset)
            TimeEntry.objects.create(
                employee=self.employee,
                clock_in=aware(day, 8, 0),
                clock_out=aware(day, 16, 24),
            )
        calc = labor.calculate_week(self.employee, self.monday)
        self.assertEqual(calc.weekly_overtime_minutes, 0)
        self.assertFalse(calc.weekly_overtime_exceeded)
        for offset in range(5):
            day = self.monday + timezone.timedelta(days=offset)
            self.assertEqual(calc.days[day].buckets["ordinary_day_minutes"], 8 * 60 + 24)
            self.assertEqual(calc.days[day].total_overtime_minutes, 0)

    def test_irregular_daily_distribution_4_to_9_hours(self):
        schedule_irr = Schedule.objects.create(name="Irregular 42h")
        services.set_schedule_terms(
            schedule_irr,
            Decimal("42.00"),
            {"monday": True, "tuesday": True, "wednesday": True, "thursday": True, "friday": True, "saturday": True},
            date(2026, 1, 1),
            daily_hours={
                "monday_hours": Decimal("9.00"),
                "tuesday_hours": Decimal("9.00"),
                "wednesday_hours": Decimal("8.00"),
                "thursday_hours": Decimal("8.00"),
                "friday_hours": Decimal("4.00"),
                "saturday_hours": Decimal("4.00"),
            },
        )
        services.assign_schedule(self.employee, schedule_irr, date(2026, 1, 1))

        hours_worked = [9, 9, 8, 8, 4, 4]
        for offset, hrs in enumerate(hours_worked):
            day = self.monday + timezone.timedelta(days=offset)
            TimeEntry.objects.create(
                employee=self.employee,
                clock_in=aware(day, 8, 0),
                clock_out=aware(day, 8 + hrs, 0),
            )
        calc = labor.calculate_week(self.employee, self.monday)
        self.assertEqual(calc.weekly_overtime_minutes, 0)
        self.assertEqual(calc.days[self.monday].buckets["ordinary_day_minutes"], 9 * 60)
        self.assertEqual(calc.days[self.monday + timezone.timedelta(days=4)].buckets["ordinary_day_minutes"], 4 * 60)

    def test_night_shift_crossing_19_00_and_overtime(self):
        # Meta diaria: 8h 24m (504 min). Trabaja 14:00 a 00:00 (10 horas = 600 min).
        # 14:00-19:00 = 5h (300 min) ordinarias diurnas
        # 19:00-22:24 = 3h 24m (204 min) ordinarias nocturnas (recargo nocturno)
        # 22:24-00:00 = 1h 36m (96 min) extras nocturnas
        TimeEntry.objects.create(
            employee=self.employee,
            clock_in=aware(self.monday, 14, 0),
            clock_out=aware(self.monday + timezone.timedelta(days=1), 0, 0),
        )
        calc = labor.calculate_week(self.employee, self.monday)
        day_data = calc.days[self.monday]
        self.assertEqual(day_data.buckets["ordinary_day_minutes"], 300)
        self.assertEqual(day_data.buckets["ordinary_night_minutes"], 204)
        self.assertEqual(day_data.buckets["overtime_night_minutes"], 96)
        self.assertFalse(day_data.daily_overtime_exceeded)

    def test_combined_rule_no_double_counting(self):
        # Lunes trabaja 11h (8h24m ord + 2h36m extra -> alerta diaria > 2h).
        # Martes a Viernes trabaja 8h24m cada día (33h36m ord).
        # Total trabajado: 44h36m. Extra debe ser exactamente 2h36m (156 min), sin duplicar.
        TimeEntry.objects.create(
            employee=self.employee,
            clock_in=aware(self.monday, 7, 0),
            clock_out=aware(self.monday, 18, 0),
        )
        for offset in range(1, 5):
            day = self.monday + timezone.timedelta(days=offset)
            TimeEntry.objects.create(
                employee=self.employee,
                clock_in=aware(day, 8, 0),
                clock_out=aware(day, 16, 24),
            )
        calc = labor.calculate_week(self.employee, self.monday)
        mon = calc.days[self.monday]
        self.assertEqual(mon.total_overtime_minutes, 156)
        self.assertTrue(mon.daily_overtime_exceeded)
        self.assertEqual(mon.daily_overtime_excess_minutes, 36)
        self.assertEqual(calc.weekly_overtime_minutes, 156)

    def test_sunday_and_holiday_surcharges(self):
        # Viernes 7 de agosto es festivo (Batalla de Boyacá). Trabaja 10h (8h24m ord festiva + 1h36m extra festiva).
        aug3 = date(2026, 8, 3)
        aug7 = date(2026, 8, 7)
        Holiday.objects.get_or_create(date=aug7, defaults={"name": "Batalla de Boyacá"})
        TimeEntry.objects.create(
            employee=self.employee,
            clock_in=aware(aug7, 8, 0),
            clock_out=aware(aug7, 18, 0),
        )
        calc = labor.calculate_week(self.employee, aug3)
        fri = calc.days[aug7]
        self.assertTrue(fri.is_holiday)
        self.assertEqual(fri.buckets["ordinary_holiday_day_minutes"], 504)
        self.assertEqual(fri.buckets["overtime_holiday_day_minutes"], 96)

    def test_weekly_overtime_alert_over_12h(self):
        # 5 días de 11h24m (3h extra por día = 15h extras en la semana > 12h)
        for offset in range(5):
            day = self.monday + timezone.timedelta(days=offset)
            TimeEntry.objects.create(
                employee=self.employee,
                clock_in=aware(day, 7, 0),
                clock_out=aware(day, 18, 24),
            )
        calc = labor.sync_week(self.employee, self.monday)
        self.assertTrue(calc.weekly_overtime_exceeded)
        self.assertEqual(calc.weekly_overtime_excess_minutes, 180)
        summary = DailyAttendanceSummary.objects.get(employee=self.employee, date=self.monday)
        self.assertTrue(summary.daily_overtime_exceeded)
        self.assertTrue(summary.weekly_overtime_exceeded)
        self.assertEqual(TimeEntryBreakdown.objects.filter(employee=self.employee).count(), 5)

    def test_services_correct_and_remove_entry_sync_breakdown(self):
        corr = services.correct_entry(
            self.admin,
            self.employee,
            aware(self.monday, 8, 0),
            aware(self.monday, 18, 0),
            "Ajuste de prueba",
        )
        self.assertTrue(TimeEntryBreakdown.objects.filter(entry=corr.entry).exists())
        summary = DailyAttendanceSummary.objects.get(employee=self.employee, date=self.monday)
        self.assertEqual(summary.total_worked_minutes, 600)

        services.remove_entry(self.admin, corr.entry, "Eliminar prueba")
        self.assertFalse(DailyAttendanceSummary.objects.filter(employee=self.employee, date=self.monday).exists())

    def test_schedule_form_validates_custom_daily_hours(self):
        LaborRuleConfig.objects.get_or_create(effective_from=date(2024, 7, 15))
        bad_form = ScheduleForm(
            data={
                "name": "Inválido",
                "weekly_hours": "42",
                "monday": "on",
                "monday_hours": "10",  # excede max_daily_hours (9)
                "tuesday": "on",
                "tuesday_hours": "8",
                "wednesday": "on",
                "wednesday_hours": "8",
                "thursday": "on",
                "thursday_hours": "8",
                "friday": "on",
                "friday_hours": "8",
            }
        )
        self.assertFalse(bad_form.is_valid())
        self.assertIn("monday_hours", bad_form.errors)

        good_form = ScheduleForm(
            data={
                "name": "Válido 42h",
                "weekly_hours": "42",
                "monday": "on",
                "monday_hours": "9",
                "tuesday": "on",
                "tuesday_hours": "9",
                "wednesday": "on",
                "wednesday_hours": "8",
                "thursday": "on",
                "thursday_hours": "8",
                "friday": "on",
                "friday_hours": "8",
            }
        )
        self.assertTrue(good_form.is_valid(), good_form.errors)
        sched = good_form.save()
        self.assertTrue(sched.latest.has_custom_daily_hours)

    def test_employee_form_saves_document_and_contract_type(self):
        form = EmployeeForm(
            instance=self.employee,
            data={
                "username": "carlos",
                "document_number": "99887766",
                "first_name": "Carlos",
                "last_name": "Pérez",
                "contract_type": Employee.ContractType.FIXED,
                "start_date": "2026-01-01",
                "is_active": "on",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        emp = form.save()
        self.assertEqual(emp.document_number, "99887766")
        self.assertEqual(emp.contract_type, Employee.ContractType.FIXED)
