from . import notifications, services
from .models import Employee

SIGNED_IN_AS = "signed_in_as"  # session key: the employee an admin is looking in as


class CloseStaleEntriesMiddleware:
    """Stops timers nobody answered for, and flags yesterday's forgotten clock-outs, before any page is built.

    Doing it on each request means no scheduler has to be running for the
    record to be right; the check is a single indexed query.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            for entry in services.stop_unanswered_timers():
                notifications.timer_stopped(request, entry)
            services.close_stale_entries()
        return self.get_response(request)


class SignedInAsMiddleware:
    """Lets an admin use the site as one of the employees, without knowing their password.

    The session stays the admin's own. For as long as it names an employee,
    `request.user` is that employee and `request.impersonator` is the admin,
    so every page behaves exactly as it would for the employee.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.impersonator = None
        employee_pk = request.session.get(SIGNED_IN_AS)
        if employee_pk:
            admin = request.user
            employee = None
            if admin.is_authenticated and admin.is_staff:
                employee = Employee.objects.filter(pk=employee_pk, is_active=True, is_staff=False).first()
            if employee:
                request.impersonator = admin
                request.user = employee
            else:
                del request.session[SIGNED_IN_AS]
        return self.get_response(request)
