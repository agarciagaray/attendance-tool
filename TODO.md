# TODO — Brechas y pasos pendientes frente a los requerimientos del cliente

Este documento compara los requerimientos levantados para el cliente (documento `Implementacion_control_asistencia.md` y definiciones funcionales acordadas) contra el estado actual del aplicativo en este repositorio.

---

## 1. Resumen de lo ya implementado

- [x] **Localización completa a Colombia:** interfaz, correos, exportaciones y documentación en español; formato de 12 horas; zona horaria `America/Bogota`; festivos de Colombia (fijos, Ley Emiliani y Semana Santa 2026+).
- [x] **Identificación y contrato del empleado:** campos `document_number` (cédula única) y `contract_type` (tipo de contrato) en `Employee`, formularios, listado y detalle.
- [x] **Jornada flexible (≤ 42 h) y distribución irregular:** `ScheduleVersion` y `ScheduleForm` soportan jornadas menores a 42 h y horas diarias personalizadas por día (4–9 h).
- [x] **Motor de cálculo laboral colombiano (`attendance/labor.py`):**
  - Regla combinada diaria + semanal sin doble conteo (Art. 159 CST).
  - Separación ortogonal entre jornada ordinaria/extra, diurna/nocturna (`19:00`–`06:00`) y día normal/dominical o festivo (Art. 179–181 CST).
  - Tablas persistentes `TimeEntryBreakdown` y `DailyAttendanceSummary` sincronizadas automáticamente.
  - Alertas sin bloqueo cuando se superan 2 h extras diarias o 12 h extras semanales.
- [x] **Parametrización de reglas laborales (`LaborRuleConfig`):** vista `/manage/labor-rules/` para gestionar vigencias, franja nocturna, porcentajes de recargos y umbrales de alerta.
- [x] **Vista previa de recargos y extras:** pestaña `/manage/attendance/surcharges/` con totales por empleado, detalle diario y alertas.
- [x] **Reporte Art. 12 Ley 2466 de 2025:** exportación en Excel (`.xlsx` con detalle + resumen) y `.csv`.
- [x] **Integración ERP (las 3 vías):**
  - Exportación plana ERP Nómina (`.xlsx` y `.csv`).
  - API REST JSON (`/api/v1/erp/summary/` y `/api/v1/erp/entries/`) con autenticación por sesión staff o `ERP_API_TOKEN`.
  - Vistas SQL en SQLite (`v_erp_resumen_diario` y `v_erp_desglose_marcaciones`).

---

## 2. Lo que falta por implementar o complementar

### Prioridad Alta (antes de pasar a producción con el cliente)

- [ ] **Compatibilidad de vistas SQL con PostgreSQL (si se sale de SQLite):**
  - La migración `attendance/migrations/0013_erp_sql_views.py` usa `CREATE VIEW IF NOT EXISTS`, sintaxis válida en **SQLite**, pero **inválida en PostgreSQL** (donde debe usarse `CREATE OR REPLACE VIEW`).
  - Antes de desplegar con `DATABASE_URL` apuntando a PostgreSQL, adaptar `0013_erp_sql_views.py` (o detectar `schema_editor.connection.vendor`) para usar `CREATE OR REPLACE VIEW` en PostgreSQL.

- [ ] **Mapeo e integración real con el ERP específico del cliente (Fase 4 del plan):**
  - Confirmar con el equipo de nómina/ERP del cliente el **formato y códigos de concepto** exactos (por ejemplo, código contable o de novedad para recargo nocturno `035`, extra diurna `025`, incapacidad, licencia no remunerada, etc.).
  - Si el ERP requiere `centro_de_costo`, `sucursal` o `codigo_cargo` por empleado, agregar esos campos al modelo `Employee` y exponerlos en la exportación, API y vistas SQL.
  - Definir cuál de los 3 canales (Excel/CSV, API REST o vista SQL) usará el ERP en producción y realizar pruebas de importación y conciliación con datos reales de una quincena/mes.

- [ ] **Seguridad y configuración de producción:**
  - Configurar `SECRET_KEY` seguro, `DEBUG=false`, `ERP_API_TOKEN` robusto, `HTTPS=true`, `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS`.
  - Configurar respaldo automático periódico de la base de datos (`db.sqlite3` o `pg_dump`).
  - Verificar envío de correos SMTP reales en `/manage/email/`.

- [ ] **Publicación de cambios pendientes en GitHub:**
  - Confirmar y subir (`git commit` + `git push`) los archivos nuevos y modificados de las Fases 1, 2 y 3 al repositorio en GitHub.

### Prioridad Media (mejoras funcionales y operativas)

- [ ] **Mejora visual en el formulario de horarios (`ScheduleForm`):**
  - Actualmente los campos opcionales `monday_hours`..`sunday_hours` se muestran como campos estándar de formulario. Mejorar la plantilla de creación/edición de horarios para alinear cada día (checkbox + campo de horas) en una tabla compacta con cálculo en vivo de la suma semanal.

- [ ] **Trazabilidad de días compensatorios (Art. 179–181 CST):**
  - El motor ya separa trabajo ordinario/extra y normal/dominical-festivo, pero si el cliente otorga días compensatorios remunerados por trabajo habitual en domingo, agregar un tipo de permiso o marcador específico ("Compensatorio") para que quede diferenciado en el reporte hacia el ERP.

- [ ] **Vista de recargos/extras en el portal del empleado:**
  - En el plan del cliente se menciona un portal de consulta de registros para el empleado. Actualmente el empleado ve sus marcaciones, horas totales, calendario y solicitudes; se puede agregar un resumen de sus horas nocturnas, dominicales y extras en su propio calendario.

- [ ] **Prueba piloto en departamento del cliente (Fase 5 del plan):**
  - Ejecutar un piloto controlado de 1–2 semanas con empleados reales, validar redondeos decimales (`ROUND(min / 60.0, 2)`) frente al cálculo del ERP y recoger retroalimentación de administración.

### Fase Futura (excluida por decisión del cliente en esta etapa)

- [ ] **Integración con hardware biométrico y cumplimiento Ley 1581 de 2012 (SIC):**
  - *Nota:* El cliente indicó expresamente que **aún no se usará biometría**. Cuando se decida incorporarla, se deberá implementar:
    1. Conector/API de recepción de eventos desde relojes biométricos (huella/rostro) hacia `TimeEntry` (mapeando por `document_number` o ID del dispositivo).
    2. Módulo de **consentimiento informado** para tratamiento de datos biométricos sensibles (aceptación digital o soporte escaneado por empleado).
    3. **Método alternativo de marcación** (PIN, tarjeta RFID o código QR) para garantizar el derecho legal del empleado a rechazar el registro biométrico sin sanciones.
