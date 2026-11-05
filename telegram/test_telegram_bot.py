import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import telegram_bot


def crear_update(user_id=123, chat_type='private', text='/eliminar 123'):
    mensaje = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=chat_type),
        effective_message=mensaje,
        callback_query=None,
        message=mensaje,
    )


class TelegramBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        telegram_bot.pending_deletions.clear()

    def test_parse_admin_ids_accepts_comma_separated_numeric_ids(self):
        self.assertEqual(telegram_bot.parse_admin_ids('123, 456'), {123, 456})

    def test_parse_admin_ids_rejects_non_numeric_values(self):
        with self.assertRaises(RuntimeError):
            telegram_bot.parse_admin_ids('123,admin')

    @patch('telegram_bot.psycopg2.connect')
    def test_host_bot_connects_to_loopback_database_port(self, connect):
        with patch.object(telegram_bot.Path, 'exists', return_value=False), patch.dict(
            telegram_bot.config,
            {
                'POSTGRES_HOST': 'postgres',
                'POSTGRES_PORT': '5432',
                'TELEGRAM_POSTGRES_PORT': '5434',
                'POSTGRES_PASSWORD': 'test-password',
            },
        ):
            telegram_bot.get_db_connection()

        self.assertEqual(connect.call_args.kwargs['host'], '127.0.0.1')
        self.assertEqual(connect.call_args.kwargs['port'], '5434')

    @patch('telegram_bot.buscar_empleado')
    async def test_unauthorized_delete_does_not_query_the_database(self, buscar):
        update = crear_update(user_id=999)
        with patch.object(telegram_bot, 'ADMIN_IDS', {123}):
            await telegram_bot.handle_eliminar(
                update,
                SimpleNamespace(args=['123']),
            )

        buscar.assert_not_called()
        update.effective_message.reply_text.assert_awaited_once()
        self.assertIn(
            'No tienes autorización',
            update.effective_message.reply_text.call_args.args[0],
        )

    @patch('telegram_bot.buscar_empleado')
    async def test_delete_is_rejected_in_group_chats(self, buscar):
        update = crear_update(chat_type='group')
        with patch.object(telegram_bot, 'ADMIN_IDS', {123}):
            await telegram_bot.handle_eliminar(
                update,
                SimpleNamespace(args=['123']),
            )

        buscar.assert_not_called()
        self.assertIn(
            'chat privado',
            update.effective_message.reply_text.call_args.args[0],
        )

    @patch('telegram_bot.eliminar_registro', return_value=True)
    @patch('telegram_bot.buscar_empleado')
    async def test_delete_requires_confirmation_before_deleting(self, buscar, eliminar):
        buscar.return_value = (
            (5, 'Ana López', '123', 'Operadora', 'Producción', 'Matutino'),
            None,
        )
        update = crear_update()
        with patch.object(telegram_bot, 'ADMIN_IDS', {123}), patch.dict(
            telegram_bot.config, {'DB_WRITE_LOCK': '0'}
        ):
            await telegram_bot.handle_eliminar(
                update,
                SimpleNamespace(args=['123']),
            )

            eliminar.assert_not_called()
            self.assertEqual(len(telegram_bot.pending_deletions), 1)

            confirmation_id = next(iter(telegram_bot.pending_deletions))
            callback_query = SimpleNamespace(
                data=f'delete:{confirmation_id}',
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            callback_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_chat=SimpleNamespace(type='private'),
                callback_query=callback_query,
            )
            await telegram_bot.handle_confirmacion_eliminacion(
                callback_update,
                SimpleNamespace(),
            )

        eliminar.assert_called_once_with(5, '123')
        callback_query.edit_message_text.assert_awaited_once()
        self.assertIn(
            'Se eliminó',
            callback_query.edit_message_text.call_args.args[0],
        )
        self.assertFalse(telegram_bot.pending_deletions)

    @patch('telegram_bot.eliminar_registro')
    async def test_another_admin_cannot_confirm_someone_elses_deletion(self, eliminar):
        telegram_bot.pending_deletions['confirmation'] = {
            'admin_id': 123,
            'registro_id': 5,
            'no_empleado': '123',
            'expires_at': telegram_bot.time.monotonic() + 60,
        }
        callback_query = SimpleNamespace(
            data='delete:confirmation',
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=456),
            effective_chat=SimpleNamespace(type='private'),
            callback_query=callback_query,
        )

        with patch.object(telegram_bot, 'ADMIN_IDS', {123, 456}):
            await telegram_bot.handle_confirmacion_eliminacion(
                update,
                SimpleNamespace(),
            )

        eliminar.assert_not_called()
        self.assertIn(
            'no está disponible',
            callback_query.edit_message_text.call_args.args[0],
        )

    def test_application_registers_admin_handlers(self):
        application = telegram_bot.crear_aplicacion()
        commands = {
            command
            for handler in application.handlers[0]
            if hasattr(handler, 'commands')
            for command in handler.commands
        }

        self.assertTrue({
            'start', 'ayuda', 'consultar', 'eliminar', 'cancelar_eliminar',
        }.issubset(commands))


if __name__ == '__main__':
    unittest.main()
