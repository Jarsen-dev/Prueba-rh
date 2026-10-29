## Portal de Acceso
Pantalla de Bienvenida `main.html`:

✓ Muestra el panel central con los nuevos ingresos organizados por semana.

✓ Muestra la lista de cumpleañeros del mes para seguimiento interno.

## Acceso y Seguridad `portalrh.html / access-control.js`:

✓ Control de inicio de sesión seguro para el personal administrativo `utily.py`.

✓ Validación automática de permisos para restringir o permitir acceso según el rol de cada usuario.

## Registro de Personal
Formulario `registro.html`:

✓ Formulario dinámico para nuevos candidatos.

✓ Autocompletado de dirección (colonia, municipio y estado) mediante código postal (cp.csv).

✓ Captura de firma digital en pantalla.

✓ Encuesta sobre el medio de reclutamiento (Facebook, volanteo, recomendación, etc.).

✗ Pagina publica para el uso de pruebas de funcionamiento de la misma en un caso real

✗ Medidas para el funcionamiento de equipos moviles

## Asignación de Puestos `Ingreso.html`:

✓ Alta del candidato seleccionado en el sistema asignándole su empresa (Cheong Woon o Kronos), área, puesto, turno, salario y esquema de pago.

✓ Funcionamiento para Guardar a los ingresos comepletos, asegurandose de obtener los datos completos

✓ Eliminacion de Ingresos de manera permanente

## Control de Reclutamiento
Indicadores de ingresos `InicioReclu.html`:

✓ Gráficas comparativas mensuales de contrataciones (vía Chart.js).

✓ Distribución de ingresos por fuentes de reclutamiento y desempeño por equipos (Team 1, Team 2, etc.).

✓ Generador de enlaces temporales.

✓ Organizacion de Personal activo y numeros de comunicacion directa.

✓ Registro mensual de ingresos (Indicador de Personal y team asignado).

## Expediente Digital e Historial
Gestión de Personal `Cheong Woon.html / Kronos.html`:

✓ Listados generales de empleados agrupados por empresa.

✓ Filtros por Estatus (Activo / Inactivo) y rango de fechas.

✓ Exportación de personal a Excel (.xlsx).

# Expediente Individual `empleado.html`:

✓ Organización por pestañas: Información General, Papelería Digital y Asistencia/Incidencias.

✓ Carga y previsualización de documentos en formato PDF o Imagen (INE, CURP, RFC, NSS, comprobante de domicilio, etc.).

✓ Control individual de vacaciones, suspensiones y faltas.

✓ Fotografía del Personal.

✓ Documentos personalizados automaticos.

## Generación Automática de Documentos y Gafetes
Identificaciones `maingafette.html`:

✓ Módulo de diseño y generación de gafetes de empleados para impresión directa o exportación a PDF.

# Plantillas Automatizadas `contrato.html, cartaimss.html, cartacom.html`:

✓ Creación automática de Contratos de Trabajo, Cartas IMSS y Cartas Compromiso tomando los datos guardados del colaborador sin necesidad de llenado manual.

## Kiosco Interactivo y Bot de WhatsApp
Servidor de WhatsApp `bot.py`:

✓ Integración con Meta Cloud API para atención automática a colaboradores.

✓ Autenticación segura: el colaborador ingresa su número de empleado y crea/valida su contraseña de 8 caracteres.

✓ Consultas rápidas: información sobre área asignada, turno, días de vacaciones disponibles y aviso de privacidad.

✓ Atención a externos: provee los contactos directos del equipo de Reclutamiento.

# Puente de Conexión `whatsapp-puente.js`:

✓ Automatización mediante @wppconnect-team/wppconnect y Puppeteer para mantener la sesión de WhatsApp activa y redirigir mensajes hacia el backend en Flask.