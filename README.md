<h1 align="center">Control de Asistencia</h1>

<p align="center">
  Una herramienta autohospedada de control de asistencia para equipos pequeños.<br>
  Un solo botón para marcar entrada y salida, y un registro claro para quien administra el equipo.
</p>

<p align="center">
  <a href="https://github.com/Blankscreen-exe/attendance-tool/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/Blankscreen-exe/attendance-tool/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Django 5.2" src="https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white">
  <img alt="Tailwind CSS 4" src="https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white">
  <img alt="SQLite o PostgreSQL" src="https://img.shields.io/badge/SQLite_%7C_PostgreSQL-compatible-336791?logo=postgresql&logoColor=white">
  <img alt="Listo para Docker" src="https://img.shields.io/badge/Docker-listo-2496ED?logo=docker&logoColor=white">
  <img alt="Licencia MIT" src="https://img.shields.io/badge/License-MIT-green">
</p>

<p align="center">
  <img src="docs/screenshots/clock.png" alt="Pantalla del reloj del empleado, con un botón grande para marcar salida" width="49%">
  <img src="docs/screenshots/today.png" alt="Pantalla Hoy de administración, con una barra de línea de tiempo por empleado" width="49%">
</p>

## Por qué existe

Las suites completas de recursos humanos hacen asistencia, nómina, selección y una docena de cosas más, y son pesadas de configurar y usar. Este proyecto hace una sola tarea: registra cuándo trabaja cada persona y muestra si se cumplieron las horas acordadas.

- **Los empleados** inician sesión y pulsan un único botón. No pueden escribir una hora manualmente.
- **El administrador** ve quién está en turno, revisa cualquier semana o mes, decide solicitudes y configura horarios, festivos y permisos.
- **Corre en un servidor pequeño**, como un solo contenedor con un archivo SQLite, o con PostgreSQL si lo prefieres.

## Funcionalidades

**Para empleados**

- Un único botón para marcar entrada / salida. El servidor registra la hora.
- Horas de hoy y progreso de la semana frente a la meta.
- Calendario mensual: presente, ausente, permiso, festivo, día libre o sin salida.
- Línea de tiempo de cada día, mostrando los descansos entre sesiones.
- Solicitudes por tiempo faltante (con motivo) y de permisos, con el saldo disponible del año.
- **Notas de trabajo:** una nota opcional sobre en qué se empleó cada tramo de trabajo, escrita desde el reloj, el registro o seleccionando el tramo en la línea de tiempo. Los administradores pueden leerlas y aparecen en la exportación diaria.
- Instalable en la pantalla de inicio del teléfono.

**Para administración**

- **Hoy:** quién está en turno en este momento, con una barra de línea de tiempo por empleado.
- **Asistencia por semana y por mes:** horas frente a la meta para cualquier rango, con exportación CSV y resumen mensual listo para imprimir.
- **Bandeja de solicitudes:** aprobar o rechazar con una nota. Los permisos que superan el cupo anual se señalan, no se bloquean.
- **"¿Sigues trabajando?":** un temporizador que lleva 4 horas continuas sin descanso le pregunta al empleado. Si no responde en 10 minutos, se marca la salida en el punto de las 4 horas y se le envía un correo. Ambos tiempos se configuran en la página de Configuración.
- **Correcciones:** agregar, corregir o eliminar un registro con motivo. Los valores anteriores se conservan en un historial visible para el empleado.
- **Entrar como empleado:** ver el sitio exactamente como lo ve el empleado, sin su contraseña. Un aviso en cada página permite volver al panel de administración.
- **Horarios:** horas semanales y días laborables, con una vista semana a semana de quién debe trabajar cuándo. Los cambios aplican a partir de la semana elegida.
- **Festivos:** una lista editable que inicia con los festivos públicos de Colombia (fijos, Ley Emiliani y Semana Santa para 2026 y los próximos años).
- **Permisos:** cupo anual por tipo de permiso y saldo de cada persona.
- **Correo:** avisos por SMTP, con un botón de prueba que explica los fallos.

**Incluido de serie**

- Bloqueo de inicio de sesión tras varios intentos con contraseña incorrecta.
- Los administradores gestionan el sistema: no registran asistencia propia ni marcan entrada.
- SQLite por defecto, PostgreSQL mediante una variable de entorno.

## Capturas de pantalla

Todos los nombres y cifras de estas imágenes son datos de demostración ficticios.

| | |
|---|---|
| **Calendario mensual y línea de tiempo**<br>Cada semana muestra las horas frente a su meta.<br><br><img src="docs/screenshots/calendar.png" alt="Calendario mensual de un empleado con línea de tiempo de cada día"> | **Asistencia por semana**<br>Verde cumplió la meta, rojo quedó por debajo, azul sigue en curso.<br><br><img src="docs/screenshots/attendance-weekly.png" alt="Tabla de empleados y semanas con horas frente a la meta"> |
| **Solicitudes**<br>Tiempo faltante y permisos, con el saldo disponible junto a cada solicitud.<br><br><img src="docs/screenshots/requests.png" alt="Bandeja de solicitudes de administración con botones para aprobar y rechazar"> | **Horarios**<br>Quién debe trabajar cuándo y cómo ha cambiado cada horario en el tiempo.<br><br><img src="docs/screenshots/schedules.png" alt="Tabla de empleados y horas esperadas en cada día de la semana"> |
| **Resumen mensual**<br>Días y horas por empleado, listo para imprimir o exportar.<br><br><img src="docs/screenshots/attendance-monthly.png" alt="Tabla de resumen mensual con días presentes, ausentes y horas trabajadas"> | **Permisos**<br>Cupos anuales y lo que ha usado cada persona.<br><br><img src="docs/screenshots/leave.png" alt="Cupos de permisos y saldo de cada empleado"> |

En un teléfono, el menú se oculta detrás de un botón:

<p align="center">
  <img src="docs/screenshots/clock-phone.png" alt="La página del reloj en un teléfono" width="280">
  &nbsp;&nbsp;
  <img src="docs/screenshots/menu-phone.png" alt="El menú abierto en un teléfono" width="280">
</p>

## Cómo funciona

Algunas decisiones definen todo el proyecto.

**El servidor controla el reloj.** El botón solo envía "in" o "out". La hora se toma en el servidor, se guarda en UTC y se muestra en la zona horaria de la empresa (`America/Bogota` por defecto), así que un reloj del dispositivo alterado o incorrecto no cambia nada. Además, el botón indica la intención exacta, por lo que un doble toque nunca puede marcar entrada y salida seguidas.

**Un único cálculo, muchas vistas.** Cada celda del calendario, total semanal, resumen mensual y fila CSV proviene de la misma función, que calcula cada día a partir de cuatro fuentes: registros de tiempo, permisos aprobados, festivos y el horario vigente esa semana. No se almacena nada derivado, por lo que las vistas no pueden contradecirse.

**El pasado no se reescribe.** Las horas y días de un horario se guardan como versiones fechadas, al igual que la asignación de cada persona a un horario. Subir una meta desde la próxima semana deja las semanas anteriores evaluadas con lo que aplicaba entonces. Las correcciones funcionan igual: el registro cambia y los valores anteriores permanecen en el historial.

**La base de datos impide lo que nunca debe pasar.** Un índice único parcial permite como máximo un registro abierto por empleado, y restricciones de comprobación mantienen la salida posterior a la entrada tanto en SQLite como en PostgreSQL.

**Sin procesos que vigilar.** Una salida olvidada se marca la próxima vez que alguien abre la aplicación, así que no hace falta ningún programador de tareas corriendo para que el registro sea correcto. Ese registro cuenta cero horas hasta que una solicitud o corrección aporte la hora.

**Solicitudes en lugar de ediciones.** Los empleados nunca ingresan horas directamente. Una marcación olvidada se convierte en una solicitud con motivo; al aprobarse queda marcada como manual con el motivo adjunto.

**Probado en ambas bases de datos.** La suite de pruebas cubre reglas de asistencia, historial de horarios, solicitudes, correcciones, permisos, correo, bloqueo de acceso, permisos de rol y cada página. GitHub Actions la ejecuta en SQLite y PostgreSQL en cada push, y verifica que la imagen Docker construya e inicie. Una prueba migra datos antiguos hacia adelante para comprobar que la migración de historial de horarios los conserva.

## Stack tecnológico

| Capa | Elección | Por qué |
|---|---|---|
| Backend | Django 5.2 (Python 3.10+) | Autenticación, sesiones, migraciones y un ORM que corre igual en ambas bases de datos |
| Base de datos | SQLite, o PostgreSQL | Un solo archivo para una oficina pequeña; un servidor real cuando se necesite |
| Frontend | Plantillas renderizadas en servidor, Tailwind CSS 4 compilado | Sin paso de compilación en el servidor, sin SPA para lo que es principalmente un botón |
| JavaScript | Unos pocos scripts pequeños, sin framework | El reloj en vivo, tooltips de gráficos, pestañas y el editor de notas |
| Despliegue | Docker, gunicorn, WhiteNoise | Un solo contenedor que sirve la aplicación y sus archivos estáticos |

## Primeros pasos

### Ejecutar localmente

```
python -m venv .venv
.venv\Scripts\activate            # Windows; en Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
echo DEBUG=true > .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Abre http://localhost:8000, inicia sesión y luego agrega un horario en **Horarios** y personas en **Empleados**.

### Desplegar con Docker

```
cp .env.example .env              # luego completa SECRET_KEY, ALLOWED_HOSTS, ADMIN_PASSWORD
docker compose up -d --build
```

La aplicación escucha en el puerto 8000. En el primer arranque crea la base de datos y la cuenta de administrador definida en `.env`. El archivo SQLite vive en el volumen `attendance_data`.

Coloca delante un proxy inverso que termine HTTPS (Caddy, nginx o Traefik), y configura `HTTPS=true` y `CSRF_TRUSTED_ORIGINS` en `.env`.

Para actualizar: descarga el código nuevo y ejecuta `docker compose up -d --build` otra vez. Las migraciones se aplican al iniciar.

### Con PostgreSQL

Configura `POSTGRES_PASSWORD` en `.env` y luego ejecuta:

```
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

Para usar un servidor PostgreSQL que ya tengas, configura `DATABASE_URL` en `.env` y usa el `docker compose up` normal.

## Configuración

Todo se configura mediante variables de entorno o `.env`.

| Variable | Por defecto | Significado |
|---|---|---|
| `SECRET_KEY` | ninguno | Obligatorio salvo con `DEBUG=true`. |
| `DEBUG` | `false` | Modo desarrollo. Nunca activarlo en un servidor. |
| `ALLOWED_HOSTS` | `localhost` | Nombres de host a los que responde la app, separados por coma. |
| `CSRF_TRUSTED_ORIGINS` | vacío | P. ej. `https://asistencia.ejemplo.com`, cuando está detrás de un proxy HTTPS. |
| `HTTPS` | `false` | Marca las cookies como seguras y confía en el encabezado `X-Forwarded-Proto` del proxy. |
| `TIME_ZONE` | `America/Bogota` | Zona horaria de la empresa. |
| `DAY_ROLLOVER_HOUR` | `0` | Hora a la que cambia el día de asistencia. Súbela (p. ej. `5`) si hay turnos después de medianoche, para que el trabajo nocturno cuente en el día que empezó. |
| `DATABASE_URL` | vacío | Vacío usa SQLite. `postgres://usuario:clave@host:5432/nombre` para PostgreSQL. |
| `EMAIL_HOST` | vacío | Servidor SMTP. Vacío significa que no se envían correos. |
| `EMAIL_PORT` | `587` | 587 usa STARTTLS. Para 465 también configura `EMAIL_USE_SSL=true`. |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | vacío | Credenciales SMTP. Si usas clave API SMTP, va en la contraseña. |
| `EMAIL_FROM` | el usuario SMTP | Dirección desde la que se envían los avisos. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | vacío | Solo Docker: crea el primer administrador si aún no existe ninguno. |

### Correo electrónico

El correo está desactivado hasta configurar `EMAIL_HOST`. Completa las líneas `EMAIL_*` en `.env` con los datos SMTP de tu proveedor de correo y reinicia. Si tu proveedor da una clave API para SMTP, esa clave va en la contraseña.

Para comprobar que funciona, inicia sesión como administrador y abre la página **Correo**. Muestra el servidor de correo en uso (nunca la contraseña) y tiene un botón "Enviar correo de prueba". Si el envío falla, la página explica por qué: clave incorrecta, servidor inalcanzable, remitente rechazado o discrepancia entre puerto y cifrado.

Los avisos se envían al correo de cada cuenta, así que agrega las direcciones en la página **Empleados**. Quienes no tengan dirección simplemente no reciben correos. Si el servidor de correo falla, el error se registra en el log y la aplicación sigue funcionando.

### Instalar en teléfonos

Los teléfonos solo ofrecen instalar un sitio servido por HTTPS. Una vez lo esté:

- Android (Chrome): abre el sitio y usa "Agregar a la pantalla de inicio" en la página del reloj, o "Instalar aplicación" en el menú del navegador.
- iPhone (Safari): Compartir y luego "Agregar a inicio".

Sigue requiriendo conexión. Nada funciona sin internet, porque el servidor es quien registra la hora.

### Copias de seguridad

- SQLite: detén la aplicación, copia el archivo hacia afuera y vuelve a iniciarla:
  `docker compose stop && docker compose cp web:/app/data/db.sqlite3 ./backup.sqlite3 && docker compose start`
- PostgreSQL: `pg_dump`.

## Reglas importantes

- Una salida olvidada se marca cuando termina el día. Ese registro cuenta cero horas hasta que se corrija, y el botón ofrece "Marcar entrada" de nuevo a la mañana siguiente.
- Se pueden hacer varias entradas al día. Marcar salida y volver a entrar aparece como un descanso, y los descansos no cuentan como tiempo trabajado.
- Las semanas van de lunes a domingo. Una semana aún en curso nunca se marca como incompleta.
- Un festivo o permiso aprobado en un día laborable reduce la meta de esa semana en la parte de un día. Con 35 horas en 5 días, una semana con un festivo tiene una meta de 28 horas.
- Los días anteriores a la fecha de inicio de un empleado no cuentan en su contra.
- A un empleado sin horario se le registran sus horas pero nunca se le marca como ausente ni con horas faltantes.
- Los cambios de horario inician el lunes de la semana que elijas, así que cada semana siempre se evalúa con un único horario.
- Solo los días laborables que no son festivos consumen días de permiso. Los permisos no tienen acumulación, arrastre ni prorrateo para quienes ingresan a mitad de año.
- Cinco contraseñas incorrectas en 15 minutos bloquean ese usuario durante 15 minutos. Un administrador que asigne una nueva contraseña lo desbloquea.
- Los empleados se desactivan, no se eliminan, para conservar su historial.

## Estructura del proyecto

```
attendance/
  services.py        reglas de asistencia: el mejor punto para empezar a leer
  models.py          modelo de datos, incluyendo historial de horarios
  views.py           páginas de empleados
  manage_views.py    páginas de administración
  forms.py           formularios y su validación
  lockout.py         bloqueo de inicio de sesión
  notifications.py   avisos por correo
  co_holidays.py     festivos de Colombia (fijos, Ley Emiliani y Semana Santa)
  pwa.py             lo que necesita un teléfono para instalar el sitio
  templates/         páginas renderizadas en servidor
  static/            hoja de estilos compilada, iconos y scripts
  tests.py           suite de pruebas
config/              configuración y URLs
assets/              fuentes de Tailwind y generador de iconos
docs/                página de presentación para GitHub Pages y capturas
.github/workflows/   pruebas que corren en cada push
```

## Desarrollo

Ejecutar las pruebas:

```
python manage.py test
```

Para ejecutarlas contra PostgreSQL, define primero `DATABASE_URL`.

La hoja de estilos en `attendance/static/attendance/app.css` se compila desde `assets/tailwind.css` y las clases usadas en las plantillas. Está confirmada en el repositorio, así que el servidor no necesita Node. Después de editar plantillas o estilos:

```
npm install        # una vez
npm run css:build  # o css:watch mientras trabajas
```

La página de presentación en `docs/` es un único archivo estático. Su hoja de estilos, `docs/site.css`, se compila desde `assets/site.css` con `npm run site:build`.

Los iconos de la aplicación los genera `assets/make_icons.py` (requiere Pillow, que la app en sí no usa). Ejecútalo solo si quieres cambiar el icono.

`/admin/` es el editor de datos en bruto de Django, accesible para la cuenta principal de superusuario. Los cambios hechos allí omiten el flujo de solicitudes y aprobaciones, así que úsalo solo para corregir errores.

## Licencia

Publicado bajo la [Licencia MIT](LICENSE).
