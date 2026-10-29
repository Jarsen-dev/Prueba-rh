let usuariosRegistrados = [];
let usuarioEnEdicion = null;

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
            const passwordInput = document.createElement('input');
            passwordInput.type = 'password';
            passwordInput.className = 'form-control';
            passwordInput.autocomplete = 'new-password';
            passwordInput.placeholder = 'Nueva contraseña';
            passwordInput.required = true;
            passwordInput.dataset.field = 'new-password';
            passwordInput.setAttribute('aria-label', `Nueva contraseña para ${usuario.usuario}`);

            const saveButton = crearBotonAcceso('Guardar', 'save-password');
            const cancelButton = crearBotonAcceso('Cancelar', 'cancel-edit', 'cancel-edit');
            actionCell.append(passwordInput, saveButton, cancelButton);
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

function guardarNuevoUsuario(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const feedbackId = 'access-create-feedback';
    const payload = {
        usuario: document.getElementById('acceso-usuario').value.trim(),
        password: document.getElementById('acceso-password').value.trim(),
        permisos: [document.getElementById('acceso-asignacion').value],
    };

    try {
        window.AccessControlUsers.create(payload);
        form.reset();
        cargarUsuariosRegistrados();
        mostrarMensajeAcceso(feedbackId, 'El acceso se guardó correctamente.', 'success');
        mostrarMensajeAcceso('access-users-feedback', '');
    } catch (error) {
        mostrarMensajeAcceso(feedbackId, error.message, 'error');
    }
}

function guardarNuevaContrasena(row) {
    const usuarioId = row.dataset.usuarioId;
    const usuario = usuariosRegistrados.find((item) => item.id === usuarioId);
    const password = row.querySelector('[data-field="new-password"]').value.trim();
    if (!usuario || !password) {
        mostrarMensajeAcceso('access-users-feedback', 'Escribe una nueva contraseña.', 'error');
        return;
    }

    try {
        window.AccessControlUsers.update(usuario.id, {
            usuario: usuario.usuario,
            password,
            permisos: usuario.permisos,
            nombre_notificacion: usuario.nombre_notificacion,
            telefono_notificacion: usuario.telefono_notificacion,
        });
        usuarioEnEdicion = null;
        cargarUsuariosRegistrados();
        mostrarMensajeAcceso('access-users-feedback', 'La contraseña se actualizó correctamente.', 'success');
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
                usuarioEnEdicion = row.dataset.usuarioId;
                mostrarMensajeAcceso('access-users-feedback', '');
                renderUsuariosRegistrados();
                tbody.querySelector('[data-field="new-password"]').focus();
                break;
            case 'cancel-edit':
                usuarioEnEdicion = null;
                renderUsuariosRegistrados();
                break;
            case 'save-password':
                guardarNuevaContrasena(row);
                break;
            default:
                break;
        }
    });

    cargarUsuariosRegistrados();
});
