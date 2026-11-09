// Botón «Telegram» en la cabecera del panel: genera el código de un solo uso
// para vincular la cuenta con el bot y permite desvincularla.
(function () {
    if (window.__rhTelegramVinculo) return;
    window.__rhTelegramVinculo = true;

    const estilos = `
        .tg-boton { background: none; color: var(--color-primary, #007bff); border: 1px solid var(--color-primary, #007bff);
            border-radius: 5px; padding: 5px 12px; font-weight: bold; font-size: 0.9rem; cursor: pointer; margin-right: 10px; }
        .tg-boton:hover { background: var(--color-primary, #007bff); color: #fff; }
        .tg-fondo { position: fixed; inset: 0; background: rgba(0,0,0,0.65); display: flex; align-items: center;
            justify-content: center; z-index: 10000; padding: 16px; }
        .tg-caja { background: var(--color-surface, #1e1e1e); color: var(--color-text, #fff); border: 1px solid var(--color-border, #444);
            border-radius: 10px; padding: 24px; width: min(420px, 100%); font-family: inherit; }
        .tg-caja h2 { margin: 0 0 12px; font-size: 1.2rem; }
        .tg-caja p { color: var(--color-muted, #ddd); font-size: 0.92rem; line-height: 1.45; margin: 8px 0; }
        .tg-codigo { font-family: ui-monospace, monospace; font-size: 2rem; letter-spacing: 4px; text-align: center;
            background: #111; border: 1px dashed var(--color-border, #444); border-radius: 8px; padding: 12px; margin: 12px 0; }
        .tg-caja a { color: var(--color-primary, #007bff); word-break: break-all; }
        .tg-acciones { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; flex-wrap: wrap; }
        .tg-acciones button { border: 1px solid var(--color-border, #444); background: #333; color: #fff; border-radius: 6px;
            padding: 8px 14px; cursor: pointer; font-weight: bold; }
        .tg-acciones .tg-primario { background: var(--color-primary, #007bff); border-color: var(--color-primary, #007bff); }
        .tg-acciones .tg-peligro { background: #a92b36; border-color: #a92b36; }
        .tg-error { color: var(--color-danger, #ff4b4b) !important; }
    `;

    async function api(method, path) {
        const response = await fetch(path, { method, credentials: 'same-origin' });
        let data = {};
        try { data = await response.json(); } catch (_) { data = {}; }
        if (!response.ok) throw new Error(data.error || `Error ${response.status}`);
        return data;
    }

    function el(tag, props = {}, children = []) {
        const node = document.createElement(tag);
        Object.assign(node, props);
        children.forEach((child) => node.append(child));
        return node;
    }

    function abrir() {
        const caja = el('div', { className: 'tg-caja', role: 'dialog' });
        caja.setAttribute('aria-modal', 'true');
        caja.setAttribute('aria-label', 'Vincular Telegram');
        const fondo = el('div', { className: 'tg-fondo' }, [caja]);
        let temporizador = null;
        const cerrar = () => { clearInterval(temporizador); fondo.remove(); };
        fondo.addEventListener('click', (event) => { if (event.target === fondo) cerrar(); });
        document.addEventListener('keydown', function onKey(event) {
            if (event.key === 'Escape') { cerrar(); document.removeEventListener('keydown', onKey); }
        });
        document.body.append(fondo);

        function pintar(contenido, botones) {
            caja.replaceChildren(el('h2', { textContent: 'Telegram' }), ...contenido,
                el('div', { className: 'tg-acciones' }, [...botones, el('button', { type: 'button', textContent: 'Cerrar', onclick: cerrar })]));
        }

        function error(mensaje) {
            pintar([el('p', { className: 'tg-error', textContent: mensaje })], []);
        }

        async function estado() {
            clearInterval(temporizador);
            pintar([el('p', { textContent: 'Cargando…' })], []);
            try {
                const vinculo = await api('GET', '/api/telegram/vinculo');
                if (vinculo.vinculado) {
                    const quien = vinculo.telegram_username ? `@${vinculo.telegram_username}` : 'un Telegram';
                    pintar([
                        el('p', { textContent: `Tu cuenta está vinculada con ${quien}.` }),
                        el('p', { textContent: 'El bot actúa con los permisos de tu cuenta. Si cambias tu contraseña o desvinculas, deja de funcionar de inmediato.' }),
                    ], [
                        el('button', { type: 'button', className: 'tg-peligro', textContent: 'Desvincular', onclick: desvincular }),
                        el('button', { type: 'button', textContent: 'Vincular otro', onclick: generar }),
                    ]);
                } else {
                    pintar([
                        el('p', { textContent: 'Vincula tu cuenta con el bot de Telegram para consultar y hacer cambios desde el teléfono, con los mismos permisos que tienes aquí.' }),
                    ], [el('button', { type: 'button', className: 'tg-primario', textContent: 'Generar código', onclick: generar })]);
                }
            } catch (e) {
                error(e.message);
            }
        }

        async function generar() {
            try {
                const datos = await api('POST', '/api/telegram/codigo');
                const contenido = [
                    el('p', { textContent: 'Envía este código al bot con /vincular, o abre el enlace en tu teléfono. Es de un solo uso.' }),
                    el('div', { className: 'tg-codigo', textContent: datos.codigo }),
                ];
                if (datos.enlace) {
                    contenido.push(el('p', {}, [el('a', { href: datos.enlace, target: '_blank', rel: 'noopener', textContent: datos.enlace })]));
                }
                if (!datos.bot_activo) {
                    contenido.push(el('p', { className: 'tg-error', textContent: 'El bot no está activo en el servidor todavía.' }));
                }
                const vence = el('p');
                contenido.push(vence);
                const expira = new Date(datos.expira_en).getTime();
                const tic = () => {
                    const s = Math.max(0, Math.round((expira - Date.now()) / 1000));
                    vence.textContent = s ? `Vence en ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}.` : 'El código venció; genera otro.';
                    if (!s) clearInterval(temporizador);
                };
                tic();
                temporizador = setInterval(tic, 1000);
                pintar(contenido, [el('button', { type: 'button', textContent: 'Ya lo vinculé', onclick: estado })]);
            } catch (e) {
                error(e.message);
            }
        }

        async function desvincular() {
            if (!window.confirm('¿Desvincular tu Telegram? El bot dejará de funcionar para tu cuenta.')) return;
            try {
                await api('DELETE', '/api/telegram/vinculo');
                await estado();
            } catch (e) {
                error(e.message);
            }
        }

        estado();
    }

    function montar() {
        const destino = document.querySelector('.header-right');
        if (!destino || destino.querySelector('.tg-boton')) return;
        document.head.append(el('style', { textContent: estilos }));
        const boton = el('button', { type: 'button', className: 'tg-boton', textContent: 'Telegram', onclick: abrir });
        boton.title = 'Vincular tu cuenta con el bot de Telegram';
        destino.prepend(boton);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', montar, { once: true });
    } else {
        montar();
    }
})();
