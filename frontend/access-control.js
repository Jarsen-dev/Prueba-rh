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
        const allowed = ['recursos_humanos', 'reclutamiento', 'notificaciones'];
        return Array.isArray(permissions) ? permissions.filter((permission) => allowed.includes(permission)) : [];
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

    const requiredPermissions = {
        'iniciorh.html': 'recursos_humanos',
        'Ingreso.html': 'recursos_humanos',
        'Cheong Woon.html': 'recursos_humanos',
        'Kronos.html': 'recursos_humanos',
        'InicioReclu.html': 'reclutamiento',
    };

    const pageName = window.location.pathname.split('/').pop() || '';
    const requiredPermission = requiredPermissions[pageName];
    if (!requiredPermission) return;

    try {
        const accessUser = JSON.parse(sessionStorage.getItem('accesoUsuario') || '{}');
        const permissions = Array.isArray(accessUser.permisos) ? accessUser.permisos : [];
        if (!permissions.includes(requiredPermission)) {
            window.location.replace('portalrh.html');
        }
    } catch (_) {
        window.location.replace('portalrh.html');
    }
})();