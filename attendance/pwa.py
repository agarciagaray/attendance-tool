"""Lo que necesita un teléfono para ofrecer "Instalar aplicación" / "Agregar a pantalla de inicio".

Al instalarla solo se agrega un icono que abre el sitio en pantalla completa. Nada
funciona sin conexión a propósito: el botón del reloj debe llegar al servidor,
porque el servidor es quien registra la hora.
"""

from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control

THEME_COLOR = "#0f172a"

SERVICE_WORKER = """\
// Present only so that browsers offer to install the site as an app.
// It stores nothing: every page and every clock-in goes to the server.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
"""


def _icon(name, size, purpose="any"):
    return {
        "src": static(f"attendance/icons/{name}"),
        "sizes": f"{size}x{size}",
        "type": "image/png",
        "purpose": purpose,
    }


@cache_control(max_age=3600)
def manifest(request):
    return JsonResponse(
        {
            "name": "Control de Asistencia",
            "short_name": "Asistencia",
            "description": "Registra tu entrada y salida.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f8fafc",
            "theme_color": THEME_COLOR,
            "icons": [
                _icon("icon-192.png", 192),
                _icon("icon-512.png", 512),
                _icon("icon-maskable-512.png", 512, purpose="maskable"),
            ],
        },
        content_type="application/manifest+json",
    )


@cache_control(no_cache=True)
def service_worker(request):
    # Servido desde la raíz del sitio para cubrir todas las páginas.
    return HttpResponse(SERVICE_WORKER, content_type="text/javascript")
