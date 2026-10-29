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
        if (usuarioEnEdicion === usuario.id) {
            actionCell.appendChild(crearBotonAcceso('Eliminar', 'delete-access', 'delete-access-button'));
        } else {
            actionCell.appendChild(crearBotonAcceso('Editar', 'edit-password'));
        }
        row.appendChild(actionCell);
        tbody.appendChild(row);
    });
}

function cargarUsuariosRegistrados() {
    usuariosRegistrados = window.AccessControlUsers.list();
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
    document.getElementById('access-cancel-button').hidden = !isEditing;
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
    const usuario = usuariosRegistrados.find((item) => item.id === usuarioId);
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

function guardarNuevoUsuario(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const feedbackId = 'access-create-feedback';
    const password = document.getElementById('acceso-password').value.trim();
    const permission = document.getElementById('acceso-asignacion').value;

    try {
        if (usuarioEnEdicion) {
            const usuario = usuariosRegistrados.find((item) => item.id === usuarioEnEdicion);
            if (!usuario) throw new Error('No se encontró el usuario seleccionado.');
            if (!password) throw new Error('Escribe una nueva contraseña.');
            window.AccessControlUsers.update(usuario.id, {
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
            cargarUsuariosRegistrados();
            mostrarMensajeAcceso(feedbackId, 'La contraseña se actualizó correctamente.', 'success');
            mostrarMensajeAcceso('access-users-feedback', '');
            return;
        }

        window.AccessControlUsers.create({
            usuario: document.getElementById('acceso-usuario').value.trim(),
            password,
            permisos: [permission],
        });
        form.reset();
        cargarUsuariosRegistrados();
        mostrarMensajeAcceso(feedbackId, 'El acceso se guardó correctamente.', 'success');
        mostrarMensajeAcceso('access-users-feedback', '');
    } catch (error) {
        mostrarMensajeAcceso(feedbackId, error.message, 'error');
    }
}

function eliminarUsuarioAcceso(usuarioId) {
    const usuario = usuariosRegistrados.find((item) => item.id === usuarioId);
    if (!usuario || !window.confirm(`¿Eliminar el acceso de ${usuario.usuario}?`)) return;

    try {
        window.AccessControlUsers.remove(usuario.id);
        if (usuarioEnEdicion === usuario.id) {
            usuarioEnEdicion = null;
            datosFormularioAntesDeEditar = null;
            document.getElementById('form-nuevo-acceso').reset();
            establecerModoEdicion(null);
        }
        cargarUsuariosRegistrados();
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
    document.getElementById('access-cancel-button').addEventListener('click', cancelarEdicionUsuario);
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
            default:
                break;
        }
    });

    cargarUsuariosRegistrados();
    establecerModoEdicion(null);
});
