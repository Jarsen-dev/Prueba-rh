(function () {
    const storageKey = 'cwAccessUsers';

    function readUsers() {
        try {
            const users = JSON.parse(localStorage.getItem(storageKey) || '[]');
            return Array.isArray(users) ? users.map((user) => ({
                id: user.id,
                usuario: user.usuario,
                password: user.password,
                permisos: normalizePermissions(user.permisos),
                nombre_notificacion: user.nombre_notificacion || '',
                telefono_notificacion: user.telefono_notificacion || '',
            })) : [];
        } catch (_) {
            return [];
        }
    }

    function writeUsers(users) {
        localStorage.setItem(storageKey, JSON.stringify(users));
    }

    function normalizePermissions(permissions) {
        const allowed = new Set(['admin', 'reclutamiento', 'notificaciones']);
        if (!Array.isArray(permissions)) return [];
        return [...new Set(permissions
            .filter((permission) => typeof permission === 'string')
            .map((permission) => permission.trim().toLowerCase())
            .map((permission) => permission === 'recursos_humanos' ? 'admin' : permission)
            .filter((permission) => allowed.has(permission)))];
    }

    function normalizeUser(data, existing = {}) {
        const permissions = normalizePermissions(data.permisos);
        return {
            id: existing.id || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
            usuario: String(data.usuario || '').trim(),
            password: String(data.password || '').trim(),
            permisos: permissions,
            nombre_notificacion: permissions.includes('notificaciones') ? String(data.nombre_notificacion || '').trim() : '',
            telefono_notificacion: permissions.includes('notificaciones') ? String(data.telefono_notificacion || '').trim() : '',
        };
    }

    function validateUser(user, users, userId = null) {
        if (!user.usuario || !user.password || !user.permisos.length) {
            throw new Error('Usuario, contraseña y al menos un permiso son obligatorios.');
        }
        if (users.some((item) => item.usuario.toLowerCase() === user.usuario.toLowerCase() && item.id !== userId)) {
            throw new Error('Ese usuario ya está registrado.');
        }
        if (user.permisos.includes('notificaciones') && (!user.nombre_notificacion || !user.telefono_notificacion)) {
            throw new Error('Nombre y teléfono son obligatorios para Notificaciones.');
        }
    }

    window.AccessControlUsers = {
        list() {
            return readUsers();
        },
        create(data) {
            const users = readUsers();
            const user = normalizeUser(data);
            validateUser(user, users);
            users.push(user);
            writeUsers(users);
            return user;
        },
        update(userId, data) {
            const users = readUsers();
            const index = users.findIndex((user) => user.id === userId);
            if (index < 0) throw new Error('Usuario no encontrado.');
            const user = normalizeUser(data, users[index]);
            validateUser(user, users, userId);
            users[index] = user;
            writeUsers(users);
            return user;
        },
        remove(userId) {
            const users = readUsers();
            const filteredUsers = users.filter((user) => user.id !== userId);
            if (filteredUsers.length === users.length) throw new Error('Usuario no encontrado.');
            writeUsers(filteredUsers);
        },
        authenticate(usuario, password) {
            const usuarioSistema = String(usuario || '').trim();
            const passwordSistema = String(password || '').trim();
            return readUsers().find((user) => user.usuario === usuarioSistema && user.password === passwordSistema) || null;
        },
    };

    const pagePermissions = {
        'iniciorh.html': ['admin'],
        'Ingreso.html': ['admin'],
        'Cheong Woon.html': ['admin', 'reclutamiento'],
        'Kronos.html': ['admin', 'reclutamiento'],
        'InicioReclu.html': ['admin', 'reclutamiento'],
        'empleado.html': ['admin', 'reclutamiento'],
    };

    const pageName = decodeURIComponent(window.location.pathname.split('/').pop() || '');
    const allowedPermissions = pagePermissions[pageName];
    if (!allowedPermissions) return;

    try {
        const accessUser = JSON.parse(sessionStorage.getItem('accesoUsuario') || '{}');
        const currentUser = AccessControlUsers.list().find((user) => (
            accessUser.id && user.id === accessUser.id
        ) || (
            accessUser.usuario
            && user.usuario.toLowerCase() === String(accessUser.usuario).trim().toLowerCase()
        ));
        if (!currentUser) {
            window.location.replace('portalrh.html');
            return;
        }

        sessionStorage.setItem('accesoUsuario', JSON.stringify(currentUser));
        const permissions = normalizePermissions(currentUser.permisos);
        document.documentElement.classList.toggle(
            'recruitment-limited',
            permissions.includes('reclutamiento') && !permissions.includes('admin')
        );
        const hasPermission = permissions.some((permission) => allowedPermissions.includes(permission));
        if (!hasPermission) {
            window.location.replace('portalrh.html');
            return;
        }

        const filterNavigation = () => {
            document.querySelectorAll('.nav-links a[href]').forEach((link) => {
                const target = new URL(link.href, window.location.href);
                const targetPage = decodeURIComponent(target.pathname.split('/').pop() || '');
                const targetPermissions = pagePermissions[targetPage];
                if (targetPermissions && !permissions.some((permission) => targetPermissions.includes(permission))) {
                    link.style.display = 'none';
                }
            });
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', filterNavigation, { once: true });
        } else {
            filterNavigation();
        }
    } catch (_) {
        window.location.replace('portalrh.html');
    }
})();