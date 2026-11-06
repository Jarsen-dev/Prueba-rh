import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import telegram_bot


class TelegramBotTests(unittest.TestCase):
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

    @patch.object(telegram_bot.bot, 'reply_to')
    @patch.object(telegram_bot, 'buscar_empleado')
    def test_unauthorized_delete_does_not_query_the_database(self, buscar, reply_to):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            chat=SimpleNamespace(type='private'),
            text='/eliminar 123',
        )

        with patch.object(telegram_bot, 'ADMIN_IDS', {123}):
            telegram_bot.handle_eliminar(message)

        buscar.assert_not_called()
        self.assertIn('No tienes autorización', reply_to.call_args.args[1])

    @patch.object(telegram_bot.bot, 'reply_to')
    @patch.object(telegram_bot, 'buscar_empleado')
    def test_delete_is_rejected_in_group_chats(self, buscar, reply_to):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            chat=SimpleNamespace(type='group'),
            text='/eliminar 123',
        )

        with patch.object(telegram_bot, 'ADMIN_IDS', {123}):
            telegram_bot.handle_eliminar(message)

        buscar.assert_not_called()
        self.assertIn('chat privado', reply_to.call_args.args[1])

    @patch.object(telegram_bot.bot, 'send_message')
    @patch.object(telegram_bot.bot, 'answer_callback_query')
    @patch.object(telegram_bot.bot, 'reply_to')
    @patch.object(telegram_bot, 'buscar_empleado')
    @patch.object(telegram_bot, 'get_db_connection')
    def test_delete_requires_admin_confirmation_before_database_delete(
        self,
        get_db_connection,
        buscar,
        reply_to,
        answer_callback_query,
        send_message,
    ):
        buscar.return_value = ((5, 'Ana López', '123', 'Operadora', 'Producción', 'Matutino'), None)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = (5,)
        get_db_connection.return_value = connection
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            chat=SimpleNamespace(type='private'),
            text='/eliminar 123',
        )

        with patch.object(telegram_bot, 'ADMIN_IDS', {123}), patch.dict(
            telegram_bot.config, {'DB_WRITE_LOCK': '0'}
        ):
            telegram_bot.handle_eliminar(message)

            self.assertEqual(cursor.execute.call_count, 0)
            self.assertEqual(len(telegram_bot.pending_deletions), 1)

            confirmation_id = next(iter(telegram_bot.pending_deletions))
            callback = SimpleNamespace(
                id='callback-1',
                data=f'delete:{confirmation_id}',
                from_user=SimpleNamespace(id=123),
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=456, type='private'),
                ),
            )
            telegram_bot.handle_confirmacion_eliminacion(callback)

        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn('DELETE FROM registro', cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[1], (5, '123'))
        connection.commit.assert_called_once()
        self.assertFalse(telegram_bot.pending_deletions)
        answer_callback_query.assert_called_once()
        send_message.assert_called_once()

    @patch.object(telegram_bot.bot, 'answer_callback_query')
    @patch.object(telegram_bot, 'get_db_connection')
    def test_another_admin_cannot_confirm_someone_elses_deletion(
        self,
        get_db_connection,
        answer_callback_query,
    ):
        telegram_bot.pending_deletions['confirmation'] = {
            'admin_id': 123,
            'registro_id': 5,
            'no_empleado': '123',
            'expires_at': telegram_bot.time.monotonic() + 60,
        }
        callback = SimpleNamespace(
            id='callback-2',
            data='delete:confirmation',
            from_user=SimpleNamespace(id=456),
            message=SimpleNamespace(chat=SimpleNamespace(id=789, type='private')),
        )

        with patch.object(telegram_bot, 'ADMIN_IDS', {123, 456}):
            telegram_bot.handle_confirmacion_eliminacion(callback)

        get_db_connection.assert_not_called()
        answer_callback_query.assert_called_once()


if __name__ == '__main__':
    unittest.main()
