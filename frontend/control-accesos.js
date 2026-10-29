let usuariosRegistrados = [];
let usuarioSeleccionado = null;
let edicionUsuarioActiva = false;

function cambiarPestanaAcceso(pestana) {
    const buttons = document.querySelectorAll('#modal-control-accesos .access-tab-btn');
    const contents = document.querySelectorAll('#modal-control-accesos .access-tab-content');
    buttons.forEach((button) => button.classList.remove('active'));
    contents.forEach((content) => content.classList.remove('active'));

    const index = pestana === 'nuevo' ? 0 : 1;
    buttons[index].classList.add('active');
    contents[index].classList.add('active');
}

function obtenerPermisos(nombre) {
    return Array.from(document.querySelectorAll(`input[name="${nombre}"]:checked`)).map((input) => input.value);
}

function toggleCamposEdicion() {
    const permisos = obtenerPermisos('edit-permisos');
    document.getElementById('edit-grupo-notif').style.display = permisos.includes('notificaciones') ? 'block' : 'none';
}

function toggleCamposAdicionales() {
    const tieneNotificaciones = document.getElementById('chk-notificaciones').checked;
    document.getElementById('seccion-campos-notificacion').style.display = tieneNotificaciones ? 'block' : 'none';
}

function renderUsuariosRegistrados() {
    const tbody = document.getElementById('tabla-usuarios-registrados-body');
    tbody.textContent = '';
    usuariosRegistrados.forEach((usuario) => {
        const row = document.createElement('tr');
        const userCell = document.createElement('td');
        const userButton = document.createElement('button');
        userButton.type = 'button';
        userButton.className = 'btn-texto';
        userButton.textContent = usuario.usuario;
        userButton.addEventListener('click', () => abrirModalEditarUsuario(usuario.id));
        userCell.appendChild(userButton);

        const passwordCell = document.createElement('td');
        const passwordInput = document.createElement('input');
        passwordInput.type = 'password';
        passwordInput.className = 'form-control-table';
        passwordInput.value = '********';
        passwordInput.disabled = true;
        passwordCell.appendChild(passwordInput);
        row.append(userCell, passwordCell);
        tbody.appendChild(row);
    });
}

function cargarUsuariosRegistrados() {
    usuariosRegistrados = window.AccessControlUsers.list();
    renderUsuariosRegistrados();
}

function guardarNuevoUsuario(form) {
    const payload = {
        usuario: document.getElementById('acceso-usuario').value.trim(),
        password: document.getElementById('acceso-password').value.trim(),
        permisos: obtenerPermisos('permisos'),
        nombre_notificacion: document.getElementById('notif-nombre').value.trim(),
        telefono_notificacion: document.getElementById('notif-telefono').value.trim(),
    };

    try {
        window.AccessControlUsers.create(payload);
        form.reset();
        toggleCamposAdicionales();
        cargarUsuariosRegistrados();
        cambiarPestanaAcceso('registrados');
    } catch (error) {
        alert(error.message);
    }
}

function establecerModoEdicion(activo) {
    edicionUsuarioActiva = activo;
    document.querySelectorAll('#modal-editar-usuario input, #modal-editar-usuario select').forEach((input) => {
        input.disabled = !activo;
    });
    document.getElementById('edit-password').required = activo;
    document.getElementById('btn-editar-usuario').style.display = activo ? 'none' : 'inline-block';
    document.getElementById('btn-guardar-usuario').style.display = activo ? 'inline-block' : 'none';
}

function abrirModalEditarUsuario(usuarioId) {
    usuarioSeleccionado = usuariosRegistrados.find((usuario) => usuario.id === usuarioId) || null;
    if (!usuarioSeleccionado) return;

    document.getElementById('edit-usuario').value = usuarioSeleccionado.usuario;
    document.getElementById('edit-password').value = usuarioSeleccionado.password;
    document.getElementById('edit-nombre').value = usuarioSeleccionado.nombre_notificacion || '';
    document.getElementById('edit-telefono').value = usuarioSeleccionado.telefono_notificacion || '';
    document.querySelectorAll('input[name="edit-permisos"]').forEach((input) => {
        input.checked = usuarioSeleccionado.permisos.includes(input.value);
    });
    toggleCamposEdicion();
    establecerModoEdicion(false);
    document.getElementById('modal-editar-usuario').style.display = 'flex';
}

function habilitarEdicionUsuario() {
    establecerModoEdicion(true);
}

function cerrarModalEditarUsuario() {
    document.getElementById('modal-editar-usuario').style.display = 'none';
    usuarioSeleccionado = null;
    edicionUsuarioActiva = false;
}

function actualizarUsuarioRegistrado() {
    if (!usuarioSeleccionado || !edicionUsuarioActiva) return;
    const payload = {
        usuario: document.getElementById('edit-usuario').value.trim(),
        password: document.getElementById('edit-password').value.trim(),
        permisos: obtenerPermisos('edit-permisos'),
        nombre_notificacion: document.getElementById('edit-nombre').value.trim(),
        telefono_notificacion: document.getElementById('edit-telefono').value.trim(),
    };

    try {
        window.AccessControlUsers.update(usuarioSeleccionado.id, payload);
        cargarUsuariosRegistrados();
        cerrarModalEditarUsuario();
    } catch (error) {
        alert(error.message);
    }
}

function eliminarUsuarioRegistrado() {
    if (!usuarioSeleccionado || !confirm('¿Está seguro de eliminar este usuario?')) return;
    try {
        window.AccessControlUsers.remove(usuarioSeleccionado.id);
        cargarUsuariosRegistrados();
        cerrarModalEditarUsuario();
    } catch (error) {
        alert(error.message);
    }
}

function togglePasswordVisibility(iconElement) {
    const passwordInput = iconElement.previousElementSibling;
    if (passwordInput.type === 'password') {
        passwordInput.type = 'text';
        iconElement.classList.remove('fa-eye-slash');
        iconElement.classList.add('fa-eye');
    } else {
        passwordInput.type = 'password';
        iconElement.classList.remove('fa-eye');
        iconElement.classList.add('fa-eye-slash');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('btn-control-accesos').addEventListener('click', () => {
        document.getElementById('modal-control-accesos').style.display = 'flex';
    });

    document.querySelectorAll('.close-modal-access').forEach((button) => {
        button.addEventListener('click', () => {
            button.closest('.control-access-overlay').style.display = 'none';
        });
    });

    cargarUsuariosRegistrados();
});
