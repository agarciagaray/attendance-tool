from datetime import date

from django.db import migrations


def create_initial_labor_rule(apps, schema_editor):
    LaborRuleConfig = apps.get_model("attendance", "LaborRuleConfig")
    LaborRuleConfig.objects.get_or_create(effective_from=date(2024, 7, 15))


def remove_initial_labor_rule(apps, schema_editor):
    LaborRuleConfig = apps.get_model("attendance", "LaborRuleConfig")
    LaborRuleConfig.objects.filter(effective_from=date(2024, 7, 15)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0011_laborruleconfig_employee_contract_type_and_more"),
    ]

    operations = [
        migrations.RunPython(create_initial_labor_rule, remove_initial_labor_rule),
    ]
