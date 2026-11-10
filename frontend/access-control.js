(function () {
    const profileStorageKey = 'accesoUsuario';
    const tokenStorageKey = 'rhToken';
    const legacyUsersStorageKey = 'cwAccessUsers';

    function normalizePermissions(permissions) {
        const allowed = new Set(['admin', 'reclutamiento', 'notificaciones']);
        if (!Array.isArray(permissions)) return [];
        return [...new Set(permissions
            .filter((permission) => typeof permission === 'string')
            .map((permission) => permission.trim().toLowerCase())
            .map((permission) => permission === 'recursos_humanos' ? 'admin' : permission)
            .filter((permission) => allowed.has(permission)))];
    }

    function saveSessionProfile(user) {
        if (!user) {
            sessionStorage.removeItem(profileStorageKey);
            return;
        }

        sessionStorage.setItem(profileStorageKey, JSON.stringify({
            id: user.id,
            usuario: user.usuario,
            permisos: normalizePermissions(user.permisos),
            nombre_notificacion: user.nombre_notificacion || '',
            telefono_notificacion: user.telefono_notificacion || '',
        }));
    }

    async function requestJson(path, options = {}) {
        const response = await fetch(`/api/${path}`, {
            credentials: 'same-origin',
            ...options,
            headers: {
                ...(options.body ? { 'Content-Type': 'application/json' } : {}),
                ...(options.headers || {}),
            },
        });
        const payload = response.status === 204 ? {} : await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.error || `La solicitud falló (${response.status}).`);
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    function setToken(token) {
        if (token) localStorage.setItem(tokenStorageKey, token);
        else localStorage.removeItem(tokenStorageKey);
    }

    window.AccessControlUsers = {
        normalizePermissions,
        saveSessionProfile,
        async list() {
            const result = await requestJson('access-users');
            return Array.isArray(result.usuarios) ? result.usuarios : [];
        },
        create(data) {
            return requestJson('access-users', {
                method: 'POST',
                body: JSON.stringify({ ...data, permisos: normalizePermissions(data.permisos) }),
            });
        },
        update(userId, data) {
            return requestJson(`access-users/${encodeURIComponent(userId)}`, {
                method: 'PUT',
                body: JSON.stringify({ ...data, permisos: normalizePermissions(data.permisos) }),
            });
        },
        remove(userId) {
            return requestJson(`access-users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
        },
        hasLegacyUsers() {
            return localStorage.getItem(legacyUsersStorageKey) !== null;
        },
        migrateLegacyUsers() {
            let users;
            try {
                users = JSON.parse(localStorage.getItem(legacyUsersStorageKey) || '[]');
            } catch (_) {
                throw new Error('Las cuentas antiguas de este navegador no tienen un formato válido.');
            }
            if (!Array.isArray(users) || !users.length) {
                throw new Error('No hay cuentas antiguas para migrar en este navegador.');
            }
            return requestJson('access-users/import', {
                method: 'POST',
                body: JSON.stringify({
                    usuarios: users.map((user) => ({
                        usuario: user.usuario,
                        password: user.password,
                        permisos: normalizePermissions(user.permisos),
                        nombre_notificacion: user.nombre_notificacion || '',
                        telefono_notificacion: user.telefono_notificacion || '',
                    })),
                }),
            });
        },
        clearLegacyUsers() {
            localStorage.removeItem(legacyUsersStorageKey);
        },
        async authenticate(usuario, password) {
            try {
                const user = await requestJson('auth/login', {
                    method: 'POST',
                    body: JSON.stringify({ usuario, password }),
                });
                setToken(user.token);
                delete user.token;
                saveSessionProfile(user);
                return user;
            } catch (error) {
                if (error.status === 401) return null;
                throw error;
            }
        },
        async getSession() {
            try {
                return await requestJson('auth/me');
            } catch (error) {
                if (error.status === 401) return null;
                throw error;
            }
        },
        async logout() {
            setToken(null);
            saveSessionProfile(null);
            await requestJson('auth/logout', { method: 'POST' });
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

    function cargarBotonTelegram() {
        if (!document.querySelector('.header-right') || document.querySelector('script[data-telegram-vinculo]')) return;
        const script = document.createElement('script');
        script.src = 'telegram-vinculo.js?v=20261110-restore-button';
        script.dataset.telegramVinculo = 'true';
        document.head.append(script);
    }

    const pageName = decodeURIComponent(window.location.pathname.split('/').pop() || '');
    const allowedPermissions = pagePermissions[pageName];

    if (allowedPermissions) {
        document.documentElement.style.visibility = 'hidden';
        window.AccessControlReady = window.AccessControlUsers.getSession().then((user) => {
            if (!user) {
                saveSessionProfile(null);
                window.location.replace('portalrh.html');
                return false;
            }

            saveSessionProfile(user);
            const permissions = normalizePermissions(user.permisos);
            const hasPermission = permissions.some((permission) => allowedPermissions.includes(permission));
            if (!hasPermission) {
                window.location.replace('portalrh.html');
                return false;
            }

            cargarBotonTelegram();
            document.documentElement.classList.toggle(
                'recruitment-limited',
                permissions.includes('reclutamiento') && !permissions.includes('admin')
            );
            document.querySelectorAll('.nav-links a[href]').forEach((link) => {
                const target = new URL(link.href, window.location.href);
                const targetPage = decodeURIComponent(target.pathname.split('/').pop() || '');
                const targetPermissions = pagePermissions[targetPage];
                if (targetPermissions && !permissions.some((permission) => targetPermissions.includes(permission))) {
                    link.style.display = 'none';
                }
            });
            document.documentElement.style.visibility = '';
            return true;
        }).catch((error) => {
            console.error('No se pudo validar la sesión de acceso:', error);
            saveSessionProfile(null);
            window.location.replace('portalrh.html');
            return false;
        });
    }

    document.addEventListener('click', (event) => {
        const targetElement = event.target instanceof Element ? event.target : null;
        const link = targetElement?.closest('a[href]');
        if (!link) return;
        const target = new URL(link.href, window.location.href);
        if (decodeURIComponent(target.pathname.split('/').pop() || '') !== 'portalrh.html') return;

        event.preventDefault();
        window.AccessControlUsers.logout()
            .then(() => window.location.assign(target.href))
            .catch((error) => {
                console.error('No se pudo cerrar la sesión en el servidor:', error);
                window.alert('No se pudo cerrar la sesión en el servidor. Intenta de nuevo.');
            });
    });
})();
