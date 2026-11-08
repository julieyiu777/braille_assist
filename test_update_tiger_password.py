"""Verify that a rejected password never replaces the saved connection."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from update_tiger_password import main


class PasswordUpdateTests(unittest.TestCase):
    def test_rejected_password_keeps_existing_file_and_hides_driver_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            original = 'TIGER_DATABASE_URL=postgres://user:old@localhost:5432/db?sslmode=require\n'
            path.write_text(original)
            with patch('update_tiger_password.getpass', side_effect=['new', 'new']), \
                 patch('update_tiger_password.psycopg.connect', side_effect=Exception(
                     'password authentication failed: private-driver-details')), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, 'rejected this password') as error:
                    main(path)
            self.assertNotIn('private-driver-details', str(error.exception))
            self.assertEqual(path.read_text(), original)

    def test_accepted_password_is_encoded_and_other_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('OTHER=keep\nTIGER_DATABASE_URL=postgres://user:old@localhost:5432/db?sslmode=require\n')
            with patch('update_tiger_password.getpass', side_effect=['new@:/', 'new@:/']), \
                 patch('update_tiger_password.psycopg.connect') as connect, \
                 contextlib.redirect_stdout(io.StringIO()):
                main(path)
            connect.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
            self.assertEqual(path.read_text(), 'OTHER=keep\nTIGER_DATABASE_URL=postgres://user:new%40%3A%2F@localhost:5432/db?sslmode=require\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
