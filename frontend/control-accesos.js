let usuariosRegistrados = [];
let vinculosTelegram = new Map();
let usuarioEnEdicion = null;
let datosFormularioAntesDeEditar = null;

function mostrarMensajeAcceso(elementId, mensaje, tipo = '') {
    const elemento = document.getElementById(elementId);
    elemento.textContent = mensaje;
    elemento.className = `access-feedback${tipo ? ` ${tipo}` : ''}`;
}

function obtenerNombreAsignacion(permisos) {
    const nombres = {
        admin: 'Admin',
        reclutamiento: 'Reclutamiento',
        notificaciones: 'Notificaciones',
    };
    return permisos.map((permiso) => nombres[permiso] || permiso).join(', ');
}

function crearCelda(texto) {
    const cell = document.createElement('td');
    cell.textContent = texto;
    return cell;
}

function crearBotonAcceso(texto, accion, clase = '') {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = texto;
    button.dataset.accion = accion;
    if (clase) button.className = clase;
    return button;
}

function renderUsuariosRegistrados() {
    const tbody = document.getElementById('tabla-usuarios-registrados-body');
    tbody.replaceChildren();

    if (!usuariosRegistrados.length) {
        const row = document.createElement('tr');
        const emptyCell = crearCelda('Aún no hay usuarios registrados.');
        emptyCell.colSpan = 4;
        row.appendChild(emptyCell);
        tbody.appendChild(row);
        return;
    }

    usuariosRegistrados.forEach((usuario) => {
        const row = document.createElement('tr');
        row.dataset.usuarioId = usuario.id;
        row.append(
            crearCelda(usuario.usuario),
            crearCelda(obtenerNombreAsignacion(usuario.permisos)),
            crearCeldaTelegram(usuario)
        );

        const actionCell = document.createElement('td');
        if (String(usuarioEnEdicion) === String(usuario.id)) {
            actionCell.append(
                crearBotonAcceso('Eliminar', 'delete-access', 'delete-access-button'),
                crearBotonAcceso('Cancelar', 'cancel-edit', 'cancel-edit')
            );
        } else {
            actionCell.appendChild(crearBotonAcceso('Editar', 'edit-password'));
        }
        row.appendChild(actionCell);
        tbody.appendChild(row);
    });
}

function crearCeldaTelegram(usuario) {
    const vinculo = vinculosTelegram.get(String(usuario.id));
    const cell = crearCelda(vinculo ? (vinculo.telegram_username ? `@${vinculo.telegram_username}` : 'Vinculado') : '—');
    if (vinculo) {
        cell.append(' ');
        cell.appendChild(crearBotonAcceso('Revocar', 'revoke-telegram', 'delete-access-button'));
    }
    return cell;
}

async function cargarVinculosTelegram() {
    try {
        const respuesta = await fetch('/api/telegram/vinculos', { credentials: 'same-origin' });
        if (!respuesta.ok) throw new Error(`No se pudieron cargar los vínculos (${respuesta.status}).`);
        const datos = await respuesta.json();
        vinculosTelegram = new Map((datos.vinculos || []).map((v) => [String(v.usuario_id), v]));
    } catch (error) {
        vinculosTelegram = new Map();
        console.error('No se pudieron cargar los vínculos de Telegram:', error);
    }
}

async function revocarTelegram(usuarioId) {
    const usuario = usuariosRegistrados.find((item) => String(item.id) === String(usuarioId));
    if (!usuario || !window.confirm(`¿Revocar el acceso por Telegram de ${usuario.usuario}?`)) return;
    try {
        const respuesta = await fetch(`/api/telegram/vinculos/${encodeURIComponent(usuarioId)}`, {
            method: 'DELETE', credentials: 'same-origin',
        });
        if (!respuesta.ok) {
            const datos = await respuesta.json().catch(() => ({}));
            throw new Error(datos.error || `Error ${respuesta.status}`);
        }
        await cargarUsuariosRegistrados();
        mostrarMensajeAcceso('access-users-feedback', 'Se revocó el acceso por Telegram.', 'success');
    } catch (error) {
        mostrarMensajeAcceso('access-users-feedback', error.message, 'error');
    }
}

async function cargarUsuariosRegistrados() {
    try {
        [usuariosRegistrados] = await Promise.all([
            window.AccessControlUsers.list(),
            cargarVinculosTelegram(),
        ]);
    } catch (error) {
        usuariosRegistrados = [];
        mostrarMensajeAcceso('access-users-feedback', `No se pudo cargar la lista: ${error.message}`, 'error');
    }
    renderUsuariosRegistrados();
}

function establecerModoEdicion(usuario) {
    const form = document.getElementById('form-nuevo-acceso');
    const userInput = document.getElementById('acceso-usuario');
    const passwordInput = document.getElementById('acceso-password');
    const permissionSelect = document.getElementById('acceso-asignacion');
    const isEditing = Boolean(usuario);

    document.getElementById('access-form-title').textContent = isEditing ? 'Cambiar contraseña' : 'Asignar acceso';
    document.getElementById('acceso-password-label').textContent = isEditing ? 'Nueva contraseña' : 'Contraseña';
    document.getElementById('access-submit-button').textContent = isEditing ? 'Guardar cambios' : 'Guardar Acceso';
    userInput.readOnly = isEditing;
    permissionSelect.disabled = isEditing;
    passwordInput.placeholder = isEditing ? 'Escribe la nueva contraseña' : '';
    passwordInput.value = '';
    passwordInput.required = true;

    if (usuario) {
        userInput.value = usuario.usuario;
        permissionSelect.value = usuario.permisos.find((permiso) => ['admin', 'reclutamiento'].includes(permiso)) || '';
    } else {
        userInput.value = datosFormularioAntesDeEditar?.usuario || '';
        permissionSelect.value = datosFormularioAntesDeEditar?.permiso || '';
        passwordInput.value = '';
        passwordInput.required = true;
        datosFormularioAntesDeEditar = null;
    }
}

function iniciarEdicionUsuario(usuarioId) {
    const usuario = usuariosRegistrados.find((item) => String(item.id) === String(usuarioId));
    if (!usuario) {
        mostrarMensajeAcceso('access-users-feedback', 'No se encontró el usuario seleccionado.', 'error');
        return;
    }

    if (!usuarioEnEdicion) {
        datosFormularioAntesDeEditar = {
            usuario: document.getElementById('acceso-usuario').value,
            permiso: document.getElementById('acceso-asignacion').value,
        };
    }

    usuarioEnEdicion = usuario.id;
    establecerModoEdicion(usuario);
    mostrarMensajeAcceso('access-create-feedback', '');
    mostrarMensajeAcceso('access-users-feedback', '');
    renderUsuariosRegistrados();
    document.getElementById('acceso-password').focus();
    document.getElementById('form-nuevo-acceso').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function cancelarEdicionUsuario() {
    usuarioEnEdicion = null;
    establecerModoEdicion(null);
    renderUsuariosRegistrados();
    mostrarMensajeAcceso('access-create-feedback', '');
}

async function guardarNuevoUsuario(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const feedbackId = 'access-create-feedback';
    const password = document.getElementById('acceso-password').value;
    const permission = document.getElementById('acceso-asignacion').value;

    try {
        if (usuarioEnEdicion) {
            const usuario = usuariosRegistrados.find((item) => String(item.id) === String(usuarioEnEdicion));
            if (!usuario) throw new Error('No se encontró el usuario seleccionado.');
            if (!password) throw new Error('Escribe una nueva contraseña.');
            await window.AccessControlUsers.update(usuario.id, {
                usuario: usuario.usuario,
                password,
                permisos: usuario.permisos,
                nombre_notificacion: usuario.nombre_notificacion,
                telefono_notificacion: usuario.telefono_notificacion,
            });
            usuarioEnEdicion = null;
            datosFormularioAntesDeEditar = null;
            form.reset();
            establecerModoEdicion(null);
            await cargarUsuariosRegistrados();
            mostrarMensajeAcceso(feedbackId, 'La contraseña se actualizó correctamente.', 'success');
            mostrarMensajeAcceso('access-users-feedback', '');
            return;
        }

        await window.AccessControlUsers.create({
            usuario: document.getElementById('acceso-usuario').value.trim(),
            password,
            permisos: [permission],
        });
        form.reset();
        await cargarUsuariosRegistrados();
        mostrarMensajeAcceso(feedbackId, 'El acceso se guardó correctamente.', 'success');
        mostrarMensajeAcceso('access-users-feedback', '');
    } catch (error) {
        mostrarMensajeAcceso(feedbackId, error.message, 'error');
    }
}

async function eliminarUsuarioAcceso(usuarioId) {
    const usuario = usuariosRegistrados.find((item) => String(item.id) === String(usuarioId));
    if (!usuario || !window.confirm(`¿Eliminar el acceso de ${usuario.usuario}?`)) return;

    try {
        await window.AccessControlUsers.remove(usuario.id);
        if (String(usuarioEnEdicion) === String(usuario.id)) {
            usuarioEnEdicion = null;
            datosFormularioAntesDeEditar = null;
            document.getElementById('form-nuevo-acceso').reset();
            establecerModoEdicion(null);
        }
        await cargarUsuariosRegistrados();
        mostrarMensajeAcceso('access-users-feedback', 'El acceso se eliminó correctamente.', 'success');
        mostrarMensajeAcceso('access-create-feedback', '');
    } catch (error) {
        mostrarMensajeAcceso('access-users-feedback', error.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('form-nuevo-acceso');
    const tbody = document.getElementById('tabla-usuarios-registrados-body');
    const migrateButton = document.getElementById('migrate-access-users-button');

    form.addEventListener('submit', guardarNuevoUsuario);
    tbody.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-accion]');
        if (!button) return;

        const row = button.closest('tr[data-usuario-id]');
        if (!row) return;

        switch (button.dataset.accion) {
            case 'edit-password':
                iniciarEdicionUsuario(row.dataset.usuarioId);
                break;
            case 'delete-access':
                eliminarUsuarioAcceso(row.dataset.usuarioId);
                break;
            case 'cancel-edit':
                cancelarEdicionUsuario();
                break;
            case 'revoke-telegram':
                revocarTelegram(row.dataset.usuarioId);
                break;
            default:
                break;
        }
    });

    if (migrateButton) {
        migrateButton.hidden = !window.AccessControlUsers.hasLegacyUsers();
        migrateButton.addEventListener('click', async () => {
            if (migrateButton.dataset.migrationComplete === 'true') {
                if (!window.confirm('Las cuentas ya están guardadas en PostgreSQL. ¿Eliminar la copia antigua de este navegador?')) return;
                window.AccessControlUsers.clearLegacyUsers();
                migrateButton.hidden = true;
                mostrarMensajeAcceso('access-users-feedback', 'Se eliminó la copia local antigua; las cuentas permanecen en PostgreSQL.', 'success');
                return;
            }

            if (!window.confirm('Se migrarán las cuentas de este navegador a PostgreSQL y sus contraseñas quedarán guardadas como hashes. La copia local no se borrará hasta que confirmes después de verificar la migración. ¿Continuar?')) return;

            migrateButton.disabled = true;
            try {
                const result = await window.AccessControlUsers.migrateLegacyUsers();
                await cargarUsuariosRegistrados();
                if (window.confirm(`Se migraron ${result.imported} cuentas a PostgreSQL. ¿Eliminar ahora la copia de contraseñas de este navegador?`)) {
                    window.AccessControlUsers.clearLegacyUsers();
                    migrateButton.hidden = true;
                    mostrarMensajeAcceso('access-users-feedback', `Se migraron ${result.imported} cuentas y se eliminó la copia local.`, 'success');
                } else {
                    migrateButton.dataset.migrationComplete = 'true';
                    migrateButton.textContent = 'Eliminar copia local de cuentas migradas';
                    mostrarMensajeAcceso('access-users-feedback', `Se migraron ${result.imported} cuentas. La copia local sigue presente hasta que confirmes eliminarla.`, 'success');
                }
            } catch (error) {
                mostrarMensajeAcceso('access-users-feedback', error.message, 'error');
            } finally {
                migrateButton.disabled = false;
            }
        });
    }

    cargarUsuariosRegistrados();
    establecerModoEdicion(null);
});
