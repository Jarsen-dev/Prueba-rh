(function () {
    // Las cuentas viven en el servidor (tabla usuarios_acceso). Lo único que
    // queda en localStorage es la lista heredada, solo para importarla una vez.
    const legacyStorageKey = 'cwAccessUsers';
    const tokenKey = 'rhToken';
    const userKey = 'accesoUsuario';

    function normalizePermissions(permissions) {
        const allowed = new Set(['admin', 'reclutamiento', 'notificaciones']);
        if (!Array.isArray(permissions)) return [];
        return [...new Set(permissions
            .filter((permission) => typeof permission === 'string')
            .map((permission) => permission.trim().toLowerCase())
            .map((permission) => permission === 'recursos_humanos' ? 'admin' : permission)
            .filter((permission) => allowed.has(permission)))];
    }

    async function api(method, path, body) {
        const options = { method, headers: {}, credentials: 'same-origin' };
        if (body !== undefined) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }
        const response = await fetch(path, options);
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
        if (!response.ok) {
            const error = new Error(data.error || `Error ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function readLegacyUsers() {
        try {
            const users = JSON.parse(localStorage.getItem(legacyStorageKey) || '[]');
            return Array.isArray(users) ? users.filter((user) => user && user.usuario && user.password) : [];
        } catch (_) {
            return [];
        }
    }

    function validateUser(user) {
        if (!user.usuario || !user.password || !normalizePermissions(user.permisos).length) {
            throw new Error('Usuario, contraseña y al menos un permiso son obligatorios.');
        }
        if (user.password.length < 6) {
            throw new Error('La contraseña debe tener al menos 6 caracteres.');
        }
    }

    function payload(data) {
        const permisos = normalizePermissions(data.permisos);
        return {
            usuario: String(data.usuario || '').trim(),
            password: String(data.password || '').trim(),
            permisos,
            nombre_notificacion: permisos.includes('notificaciones') ? String(data.nombre_notificacion || '').trim() : '',
            telefono_notificacion: permisos.includes('notificaciones') ? String(data.telefono_notificacion || '').trim() : '',
        };
    }

    window.AccessControlUsers = {
        async list() {
            const data = await api('GET', '/api/access-users');
            return Array.isArray(data.usuarios) ? data.usuarios : [];
        },
        async create(data) {
            const user = payload(data);
            validateUser(user);
            return api('POST', '/api/access-users', user);
        },
        async update(userId, data) {
            const user = payload(data);
            validateUser(user);
            return api('PUT', `/api/access-users/${encodeURIComponent(userId)}`, user);
        },
        async remove(userId) {
            return api('DELETE', `/api/access-users/${encodeURIComponent(userId)}`);
        },
        async authenticate(usuario, password) {
            try {
                const user = await api('POST', '/api/auth/login', {
                    usuario: String(usuario || '').trim(),
                    password: String(password || '').trim(),
                });
                try { localStorage.setItem(tokenKey, user.token || ''); } catch (_) {}
                delete user.token;
                return user;
            } catch (error) {
                if (error.status === 401) return null;
                throw error;
            }
        },
        async logout() {
            try { localStorage.removeItem(tokenKey); } catch (_) {}
            sessionStorage.removeItem(userKey);
            try { await api('POST', '/api/auth/logout'); } catch (_) {}
        },
        legacyList: readLegacyUsers,
        clearLegacy() {
            try { localStorage.removeItem(legacyStorageKey); } catch (_) {}
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

    function goToLogin() {
        sessionStorage.removeItem(userKey);
        window.location.replace('portalrh.html');
    }

    function applyPermissions(permissions) {
        document.documentElement.classList.toggle(
            'recruitment-limited',
            permissions.includes('reclutamiento') && !permissions.includes('admin')
        );
        if (!permissions.some((permission) => allowedPermissions.includes(permission))) {
            goToLogin();
            return false;
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
        return true;
    }

    try {
        // Comprobación inmediata con lo guardado al iniciar sesión…
        const accessUser = JSON.parse(sessionStorage.getItem(userKey) || '{}');
        if (!accessUser.id || !applyPermissions(normalizePermissions(accessUser.permisos))) {
            goToLogin();
            return;
        }
        // …y confirmación con el servidor (sesión vigente y permisos actuales).
        api('GET', '/api/auth/me').then((me) => {
            const permissions = normalizePermissions(me.permisos);
            sessionStorage.setItem(userKey, JSON.stringify({ ...accessUser, permisos: permissions }));
            applyPermissions(permissions);
        }).catch((error) => {
            if (error.status === 401) goToLogin();
        });
    } catch (_) {
        goToLogin();
    }
})();
