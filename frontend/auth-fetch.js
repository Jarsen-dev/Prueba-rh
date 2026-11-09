// Añade la sesión (Bearer) a las peticiones hacia el backend de este mismo
// servidor, en cualquier puerto, y manda al login cuando la sesión no es válida.
// Debe cargarse antes que cualquier otro script que haga fetch.
(function () {
    if (window.__rhAuthFetch) return;
    window.__rhAuthFetch = true;

    const originalFetch = window.fetch.bind(window);
    const publicPages = new Set(['portalrh.html', 'registro.html', '']);

    function isBackendUrl(input) {
        try {
            const raw = typeof input === 'string' ? input : (input && input.url) || '';
            const url = new URL(raw, window.location.href);
            return url.hostname === window.location.hostname;
        } catch (_) {
            return false;
        }
    }

    window.fetch = async function (input, init) {
        let options = init;
        if (isBackendUrl(input)) {
            let token = '';
            try { token = localStorage.getItem('rhToken') || ''; } catch (_) {}
            if (token) {
                const headers = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
                if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
                options = { ...(init || {}), headers };
            }
        }
        const response = await originalFetch(input, options);
        if (response.status === 401 && isBackendUrl(input)) {
            const page = decodeURIComponent(window.location.pathname.split('/').pop() || '');
            if (!publicPages.has(page)) {
                const prefix = window.location.pathname.includes('/documentos/') ? '../' : '';
                sessionStorage.removeItem('accesoUsuario');
                window.location.replace(`${prefix}portalrh.html`);
            }
        }
        return response;
    };
})();
