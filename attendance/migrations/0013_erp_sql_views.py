from django.db import migrations

SQL_CREATE_SUMMARY_VIEW = """
CREATE VIEW IF NOT EXISTS v_erp_resumen_diario AS
SELECT
    e.id AS employee_id,
    COALESCE(e.document_number, '') AS cedula,
    e.username AS usuario,
    TRIM(COALESCE(e.first_name, '') || ' ' || COALESCE(e.last_name, '')) AS nombre_completo,
    e.contract_type AS tipo_contrato,
    s.date AS fecha,
    s.week_start AS inicio_semana,
    s.schedule_name AS horario,
    s.is_holiday AS es_festivo,
    s.is_sunday AS es_domingo,
    s.is_rest_or_holiday AS es_descanso_obligatorio,
    s.daily_target_minutes AS meta_diaria_minutos,
    ROUND(s.daily_target_minutes / 60.0, 2) AS meta_diaria_horas,
    s.total_worked_minutes AS total_trabajado_minutos,
    ROUND(s.total_worked_minutes / 60.0, 2) AS horas_trabajadas,
    s.ordinary_day_minutes AS ord_diurna_minutos,
    ROUND(s.ordinary_day_minutes / 60.0, 2) AS horas_ord_diurna,
    s.ordinary_night_minutes AS recargo_nocturno_minutos,
    ROUND(s.ordinary_night_minutes / 60.0, 2) AS horas_recargo_nocturno,
    s.ordinary_holiday_day_minutes AS ord_festivo_diurna_minutos,
    ROUND(s.ordinary_holiday_day_minutes / 60.0, 2) AS horas_ord_festivo_diurna,
    s.ordinary_holiday_night_minutes AS ord_festivo_nocturna_minutos,
    ROUND(s.ordinary_holiday_night_minutes / 60.0, 2) AS horas_ord_festivo_nocturna,
    s.overtime_day_minutes AS extra_diurna_minutos,
    ROUND(s.overtime_day_minutes / 60.0, 2) AS horas_extra_diurna,
    s.overtime_night_minutes AS extra_nocturna_minutos,
    ROUND(s.overtime_night_minutes / 60.0, 2) AS horas_extra_nocturna,
    s.overtime_holiday_day_minutes AS extra_festivo_diurna_minutos,
    ROUND(s.overtime_holiday_day_minutes / 60.0, 2) AS horas_extra_festivo_diurna,
    s.overtime_holiday_night_minutes AS extra_festivo_nocturna_minutos,
    ROUND(s.overtime_holiday_night_minutes / 60.0, 2) AS horas_extra_festivo_nocturna,
    s.total_ordinary_minutes AS total_ordinarias_minutos,
    ROUND(s.total_ordinary_minutes / 60.0, 2) AS horas_ordinarias,
    s.total_overtime_minutes AS total_extras_minutos,
    ROUND(s.total_overtime_minutes / 60.0, 2) AS horas_extras,
    s.leave_minutes AS permiso_minutos,
    ROUND(s.leave_minutes / 60.0, 2) AS horas_permiso,
    s.leave_type AS tipo_permiso,
    s.daily_overtime_exceeded AS alerta_extra_diaria,
    s.daily_overtime_excess_minutes AS exceso_extra_diaria_minutos,
    s.weekly_overtime_exceeded AS alerta_extra_semanal,
    s.weekly_overtime_excess_minutes AS exceso_extra_semanal_minutos
FROM attendance_dailyattendancesummary s
INNER JOIN attendance_employee e ON e.id = s.employee_id;
"""

SQL_CREATE_ENTRY_VIEW = """
CREATE VIEW IF NOT EXISTS v_erp_desglose_marcaciones AS
SELECT
    b.entry_id AS entry_id,
    e.id AS employee_id,
    COALESCE(e.document_number, '') AS cedula,
    e.username AS usuario,
    TRIM(COALESCE(e.first_name, '') || ' ' || COALESCE(e.last_name, '')) AS nombre_completo,
    b.work_date AS fecha_laboral,
    t.clock_in AS entrada_utc,
    t.clock_out AS salida_utc,
    t.source AS origen,
    b.total_worked_minutes AS total_trabajado_minutos,
    ROUND(b.total_worked_minutes / 60.0, 2) AS horas_trabajadas,
    ROUND(b.ordinary_day_minutes / 60.0, 2) AS horas_ord_diurna,
    ROUND(b.ordinary_night_minutes / 60.0, 2) AS horas_recargo_nocturno,
    ROUND(b.ordinary_holiday_day_minutes / 60.0, 2) AS horas_ord_festivo_diurna,
    ROUND(b.ordinary_holiday_night_minutes / 60.0, 2) AS horas_ord_festivo_nocturna,
    ROUND(b.overtime_day_minutes / 60.0, 2) AS horas_extra_diurna,
    ROUND(b.overtime_night_minutes / 60.0, 2) AS horas_extra_nocturna,
    ROUND(b.overtime_holiday_day_minutes / 60.0, 2) AS horas_extra_festivo_diurna,
    ROUND(b.overtime_holiday_night_minutes / 60.0, 2) AS horas_extra_festivo_nocturna,
    t.note AS nota,
    t.memo AS memo
FROM attendance_timeentrybreakdown b
INNER JOIN attendance_timeentry t ON t.id = b.entry_id
INNER JOIN attendance_employee e ON e.id = b.employee_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0012_initial_labor_rule_config"),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_CREATE_SUMMARY_VIEW,
            reverse_sql="DROP VIEW IF EXISTS v_erp_resumen_diario;",
        ),
        migrations.RunSQL(
            sql=SQL_CREATE_ENTRY_VIEW,
            reverse_sql="DROP VIEW IF EXISTS v_erp_desglose_marcaciones;",
        ),
    ]
