from . import navigation as nav
from . import services
from .models import LeaveRequest, MissingTimeRequest, RequestStatus


def pending_requests(request):
    """Number of requests waiting for a decision, for the admin navigation."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_staff:
        return {}
    count = sum(
        model.objects.filter(status=RequestStatus.PENDING).count()
        for model in (MissingTimeRequest, LeaveRequest)
    )
    return {"pending_request_count": count}


def navigation(request):
    """The sidebar menu for this user, and the section the current page belongs to."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    current = nav.section_for(user, (match.url_name if match else "") or "")
    return {
        "menu": [{"section": section, "active": section is current} for section in nav.menu_for(user)],
        "account": {"section": nav.ACCOUNT, "active": current is nav.ACCOUNT},
        "section": current,
    }


def timer_check(request):
    """For an employee with a running timer: when "Are you still working?" is due, and when the timer stops."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.is_staff:
        return {}
    question = services.timer_question(services.get_open_entry(user))
    if question is None:
        return {}
    asked_at, stops_at = question
    return {
        "timer_check": {
            "asked_at": asked_at,
            "ask_ms": int(asked_at.timestamp() * 1000),
            "stop_ms": int(stops_at.timestamp() * 1000),
            "now_ms": int(services.timezone.now().timestamp() * 1000),
        }
    }
