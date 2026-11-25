"""Festivos públicos de Colombia (Ley 51 de 1983 - Ley Emiliani y fechas fijas).

Los festivos de fecha fija se repiten cada año. Los festivos bajo la Ley
Emiliani se trasladan al lunes siguiente cuando no caen en lunes, y los de
Semana Santa / Pascua se calculan a partir del Domingo de Pascua.
"""

from datetime import date, timedelta

# (mes, día, nombre) — nunca cambian de día
FIXED_HOLIDAYS = [
    (1, 1, "Año Nuevo"),
    (5, 1, "Día del Trabajo"),
    (7, 20, "Día de la Independencia"),
    (8, 7, "Batalla de Boyacá"),
    (12, 8, "Inmaculada Concepción"),
    (12, 25, "Navidad"),
]

# (mes, día, nombre) — se trasladan al siguiente lunes si no caen en lunes
EMILIANI_HOLIDAYS = [
    (1, 6, "Día de los Reyes Magos"),
    (3, 19, "Día de San José"),
    (6, 29, "San Pedro y San Pablo"),
    (8, 15, "Asunción de la Virgen"),
    (10, 12, "Día de la Raza"),
    (11, 1, "Todos los Santos"),
    (11, 11, "Independencia de Cartagena"),
]

# (desfase en días desde el Domingo de Pascua, nombre)
EASTER_HOLIDAYS = [
    (-3, "Jueves Santo"),
    (-2, "Viernes Santo"),
    (43, "Ascensión del Señor"),
    (64, "Corpus Christi"),
    (71, "Sagrado Corazón de Jesús"),
]

# Nombres de los festivos de Pakistán sembrados originalmente por la migración 0002.
LEGACY_PK_HOLIDAY_NAMES = [
    "Kashmir Day",
    "Pakistan Day",
    "Labour Day",
    "Youm-e-Takbeer",
    "Independence Day",
    "Iqbal Day",
    "Quaid-e-Azam Day",
    "Ashura",
    "Eid Milad un-Nabi",
    "Eid ul-Fitr",
    "Eid ul-Adha",
]


def easter_sunday(year):
    """Calcula el Domingo de Pascua en el calendario gregoriano (algoritmo de Butcher)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def next_monday(day):
    """Traslada una fecha al lunes siguiente si no cae en lunes."""
    if day.weekday() == 0:
        return day
    return day + timedelta(days=7 - day.weekday())


def movable_holidays(year):
    """Pares (fecha, nombre) de los festivos trasladables y religiosos de Colombia en un año."""
    easter = easter_sunday(year)
    found = [(next_monday(date(year, month, day)), name) for month, day, name in EMILIANI_HOLIDAYS]
    found.extend((easter + timedelta(days=offset), name) for offset, name in EASTER_HOLIDAYS)
    return sorted(found)


def all_holidays(year):
    """Todos los festivos de Colombia (fijos y trasladables) en un año dado."""
    fixed = [(date(year, month, day), name) for month, day, name in FIXED_HOLIDAYS]
    return sorted([*fixed, *movable_holidays(year)])


def add_fixed_holidays(holiday_model, base_year=2020):
    """Agrega los festivos de fecha fija como registros anuales recurrentes."""
    for month, day, name in FIXED_HOLIDAYS:
        holiday_model.objects.get_or_create(
            date=date(base_year, month, day), name=name, defaults={"recurs_yearly": True}
        )


def add_movable_holidays(holiday_model, year):
    """Agrega los festivos trasladables y de Pascua de Colombia para un año, omitiendo los existentes."""
    existing = set(holiday_model.objects.filter(date__year=year).values_list("date", "name"))
    new = [
        holiday_model(date=day, name=name, tentative=False)
        for day, name in movable_holidays(year)
        if (day, name) not in existing
    ]
    holiday_model.objects.bulk_create(new)
    return len(new)


def seed_default_holidays(holiday_model, start_year=2026, end_year=2031):
    """Siembra los festivos fijos recurrentes y los festivos variables desde 2026 hasta los próximos años."""
    add_fixed_holidays(holiday_model)
    for year in range(start_year, end_year + 1):
        add_movable_holidays(holiday_model, year)
