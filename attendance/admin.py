"""Django's built-in admin, kept as a raw-data escape hatch for superusers.

Day-to-day administration happens under /manage/. Changes made here skip
the request-and-approval trail, so use it only to repair mistakes.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Correction,
    DailyAttendanceSummary,
    Employee,
    Holiday,
    LaborRuleConfig,
    LeaveAllowance,
    LeaveRequest,
    LoginThrottle,
    MissingTimeRequest,
    Schedule,
    ScheduleAssignment,
    ScheduleVersion,
    TimeEntry,
    TimeEntryBreakdown,
)


class ScheduleAssignmentInline(admin.TabularInline):
    model = ScheduleAssignment
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(UserAdmin):
    fieldsets = (
        *UserAdmin.fieldsets,
        ("Asistencia y contrato", {"fields": ("document_number", "contract_type", "start_date")}),
    )
    list_display = ("username", "document_number", "first_name", "last_name", "contract_type", "is_staff", "is_active")
    inlines = [ScheduleAssignmentInline]


class ScheduleVersionInline(admin.TabularInline):
    model = ScheduleVersion
    extra = 0


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    inlines = [ScheduleVersionInline]


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("employee", "clock_in", "clock_out", "missing_out", "source")
    list_filter = ("source", "missing_out", "employee")
    date_hierarchy = "clock_in"
    raw_id_fields = ("request",)


@admin.register(MissingTimeRequest)
class MissingTimeRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "clock_in", "clock_out", "status", "decided_by")
    list_filter = ("status", "employee")
    raw_id_fields = ("entry",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "status", "decided_by")
    list_filter = ("status", "leave_type", "employee")


@admin.register(Correction)
class CorrectionAdmin(admin.ModelAdmin):
    list_display = ("employee", "action", "admin", "created_at")
    list_filter = ("action", "employee")
    raw_id_fields = ("entry",)


@admin.register(LaborRuleConfig)
class LaborRuleConfigAdmin(admin.ModelAdmin):
    list_display = (
        "effective_from",
        "max_weekly_hours",
        "min_daily_hours",
        "max_daily_hours",
        "night_start",
        "night_end",
    )


@admin.register(TimeEntryBreakdown)
class TimeEntryBreakdownAdmin(admin.ModelAdmin):
    list_display = (
        "entry",
        "total_worked_minutes",
        "ordinary_day_minutes",
        "ordinary_night_minutes",
        "overtime_day_minutes",
        "overtime_night_minutes",
    )
    raw_id_fields = ("entry",)


@admin.register(DailyAttendanceSummary)
class DailyAttendanceSummaryAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "date",
        "total_worked_minutes",
        "total_overtime_minutes",
        "daily_overtime_exceeded",
        "weekly_overtime_exceeded",
    )
    list_filter = ("daily_overtime_exceeded", "weekly_overtime_exceeded", "is_holiday", "is_sunday")
    date_hierarchy = "date"


admin.site.register(Holiday)
admin.site.register(LeaveAllowance)
admin.site.register(LoginThrottle)
