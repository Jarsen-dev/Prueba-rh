let usuariosRegistrados = [];
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
        emptyCell.colSpan = 3;
        row.appendChild(emptyCell);
        tbody.appendChild(row);
        return;
    }

    usuariosRegistrados.forEach((usuario) => {
        const row = document.createElement('tr');
        row.dataset.usuarioId = usuario.id;
        row.append(
            crearCelda(usuario.usuario),
            crearCelda(obtenerNombreAsignacion(usuario.permisos))
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

async function cargarUsuariosRegistrados() {
    try {
        usuariosRegistrados = await window.AccessControlUsers.list();
    } catch (error) {
        usuariosRegistrados = [];
        mostrarMensajeAcceso('access-users-feedback', `No se pudo cargar la lista: ${error.message}`, 'error');
    }
    renderUsuariosRegistrados();
    renderImportacionLegada();
}

function renderImportacionLegada() {
    const contenedor = document.getElementById('access-legacy-import');
    if (!contenedor) return;
    const existentes = new Set(usuariosRegistrados.map((usuario) => usuario.usuario.toLowerCase()));
    const pendientes = window.AccessControlUsers.legacyList()
        .filter((usuario) => !existentes.has(String(usuario.usuario).trim().toLowerCase()));
    contenedor.replaceChildren();
    if (!pendientes.length) {
        if (window.AccessControlUsers.legacyList().length) window.AccessControlUsers.clearLegacy();
        contenedor.hidden = true;
        return;
    }
    contenedor.hidden = false;
    const texto = document.createElement('span');
    texto.textContent = `Este navegador tiene ${pendientes.length} usuario(s) del sistema anterior que aún no están en el servidor. `;
    const boton = crearBotonAcceso('Importar al servidor', 'import-legacy');
    boton.addEventListener('click', () => importarUsuariosLegados(pendientes, boton));
    contenedor.append(texto, boton);
}

async function importarUsuariosLegados(pendientes, boton) {
    boton.disabled = true;
    const errores = [];
    for (const usuario of pendientes) {
        try {
            await window.AccessControlUsers.create(usuario);
        } catch (error) {
            if (error.status !== 409) errores.push(`${usuario.usuario}: ${error.message}`);
        }
    }
    if (errores.length) {
        mostrarMensajeAcceso('access-users-feedback', `No se importaron: ${errores.join('; ')}`, 'error');
    } else {
        window.AccessControlUsers.clearLegacy();
        mostrarMensajeAcceso('access-users-feedback', 'Usuarios importados al servidor.', 'success');
    }
    await cargarUsuariosRegistrados();
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
        passwordInput.value = datosFormularioAntesDeEditar?.password || '';
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
            password: document.getElementById('acceso-password').value,
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
    const password = document.getElementById('acceso-password').value.trim();
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
            default:
                break;
        }
    });

    cargarUsuariosRegistrados();
    establecerModoEdicion(null);
});
