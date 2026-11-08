"""Privately replace the Tiger Data password in this project's .env file."""
from getpass import getpass
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg


def main(path=None):
    path = Path(path) if path is not None else Path(__file__).resolve().parent / '.env'
    lines = path.read_text().splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if line.removeprefix('export ').partition('=')[0].strip() == 'TIGER_DATABASE_URL']
    if len(matches) != 1:
        raise ValueError('Expected exactly one TIGER_DATABASE_URL entry in .env.')
    index = matches[0]
    value = lines[index].partition('=')[2].strip().strip('\"\'')
    url = urlsplit(value)
    if url.scheme not in ('postgres', 'postgresql') or '@' not in url.netloc:
        raise ValueError('Expected a PostgreSQL connection URL with a username.')
    password = getpass('New database password from Tiger Console (hidden): ')
    if not password:
        raise ValueError('Password cannot be empty.')
    if password != getpass('Confirm new password (hidden): '):
        raise ValueError('Passwords did not match. No changes made.')
    credentials, host = url.netloc.rsplit('@', 1)
    username = credentials.split(':', 1)[0]
    updated = urlunsplit(url._replace(netloc=f'{username}:{quote(password, safe="")}@{host}'))
    print('Checking the new password with Tiger Data before saving...')
    try:
        with psycopg.connect(updated, connect_timeout=5,
                             options='-c statement_timeout=10000') as connection:
            connection.execute('SELECT 1')
    except Exception as error:
        if (getattr(error, 'sqlstate', None) == '28P01'
                or 'password authentication failed' in str(error).lower()):
            raise SystemExit(
                'Tiger Data rejected this password. .env was not changed. '
                'Reset the DATABASE password for this service in Tiger Console '
                'and enter that password here; changing it only in this terminal '
                'does not change the database password.'
            ) from None
        raise SystemExit(
            'Could not verify the connection to Tiger Data. .env was not changed. '
            'Check your internet connection and that the database service is running.'
        ) from None
    lines[index] = f'TIGER_DATABASE_URL={updated}\n'
    # Replace atomically and retain private file permissions, without backups.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as target:
            temporary = Path(target.name)
            os.chmod(temporary, 0o600)
            target.writelines(lines)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print('Tiger Data accepted the password. Updated .env. Restart the app.')
    print('If TIGER_DATABASE_URL is also exported in your terminal, unset it first.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError):
        raise SystemExit('Could not update the password. Check .env and ensure both password entries match.') from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('Cancelled. No changes made.') from None
