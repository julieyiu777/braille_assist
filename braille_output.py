"""Send detected words one character at a time to the existing ESP32 firmware."""
import argparse
from datetime import datetime, timezone
import math
import queue
import re
import sys
import threading
import time

READY = 'Enter one lowercase letter (a-z).'
REST = 'Back at rest.'


class BrailleError(RuntimeError):
    pass


class BrailleTimeout(BrailleError):
    pass


def normalize_word(text):
    word = ' '.join(text.lower().replace('_', ' ').split())
    if not word or len(word) > 64 or any(not ('a' <= char <= 'z' or char == ' ') for char in word):
        raise ValueError('Braille output supports up to 64 lowercase a-z letters and spaces')
    return word


def serial_ports():
    from serial.tools import list_ports
    return list(list_ports.comports())


def resolve_port(port):
    if port != 'auto':
        return port
    candidates = []
    for candidate in serial_ports():
        description = (candidate.device+' '+candidate.description).lower()
        if 'bluetooth' in description:
            continue
        if candidate.vid is not None or 'usb' in description or 'ttyacm' in description:
            candidates.append(candidate.device)
    if len(candidates) != 1:
        detail = ', '.join(candidates) if candidates else 'none'
        raise BrailleError(f'Expected one USB serial board; found {detail}. Connect the ESP32 and use --braille-port PORT.')
    return candidates[0]


class SerialBrailleDriver:
    """Protocol for braile_training.txt: letter + newline, Showing, Back at rest."""
    def __init__(self, connection, timeout=15., clock=time.monotonic):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Character timeout must be positive')
        self.connection = connection
        self.timeout = timeout
        self.clock = clock
        self.buffer = b''

    def _write(self, value):
        if self.connection.write(value) != len(value):
            raise BrailleError('Incomplete serial write; stopping to avoid replaying a character')
        self.connection.flush()

    def _line(self, deadline, stop):
        while self.clock() < deadline:
            if stop.is_set():
                raise InterruptedError('Braille output stopped')
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                text = line.decode('utf-8', errors='replace').strip()
                if 'PCA9685 did not respond' in text:
                    raise BrailleError(text)
                return text
            self.buffer += self.connection.read_until(b'\n', 256)
            if len(self.buffer) > 8192:
                raise BrailleError('Unexpected serial output; check the selected board and baud rate')
        raise BrailleTimeout('ESP32 acknowledgment timed out; remaining characters were not sent')

    def wait_ready(self, stop, startup_timeout=10.):
        start = self.clock()
        deadline = start+startup_timeout
        # An unsupported command asks the original firmware for its prompt
        # without moving a servo. It also works if startup text was missed.
        self._write(b'?\n')
        while self.clock() < deadline:
            try:
                line = self._line(min(deadline, self.clock()+1), stop)
            except BrailleTimeout:
                if self.clock() >= deadline:
                    break
                self._write(b'?\n')
                continue
            if line == READY:
                return
        raise BrailleTimeout('ESP32 did not become ready. Check the port, 115200 baud, and uploaded firmware.')

    def send_character(self, character, stop):
        if len(character) != 1 or not 'a' <= character <= 'z':
            raise ValueError('Send exactly one lowercase a-z character')
        if stop.is_set():
            raise InterruptedError('Braille output stopped')
        started = self.clock()
        self._write((character+'\n').encode('ascii'))
        deadline = started+self.timeout
        showing = False
        channels = []
        while True:
            line = self._line(deadline, stop)
            match = re.fullmatch(r'Showing ([a-z]) on channels:(.*)', line)
            if match:
                if match[1] != character:
                    raise BrailleError('ESP32 reported a different character; check for another serial writer')
                showing = True
                channels = [int(value) for value in match[2].split()]
            elif line == REST and showing:
                return dict(character=character, firmware_cycle_ms=round((self.clock()-started)*1000, 2),
                            channels=channels, acknowledged=True, simulated=False,
                            position_feedback=False)
            elif 'Turn on servo power' in line or 'rst:' in line:
                raise BrailleError('ESP32 restarted during a character; remaining characters were not sent')

    def close(self):
        self.connection.close()


def open_driver(port, timeout):
    import serial
    connection = serial.Serial(port=port, baudrate=115200, timeout=.1, write_timeout=2)
    return SerialBrailleDriver(connection, timeout)


class DryRunDriver:
    def wait_ready(self, stop):
        pass

    def send_character(self, character, stop):
        if stop.is_set():
            raise InterruptedError('Braille output stopped')
        print(f'DRY RUN serial command: {character!r} + newline', file=sys.stderr, flush=True)
        return dict(character=character, firmware_cycle_ms=None, channels=[],
                    acknowledged=False, simulated=True, position_feedback=False)

    def close(self):
        pass


class BrailleOutput:
    """Single serial writer; preserve whole words and deduplicate scheduled words."""
    def __init__(self, port=None, timeout=15., dry_run=False, driver_factory=None, word_gap=.8):
        self.stop = threading.Event()
        self.events = queue.SimpleQueue()
        self.pending = queue.Queue(maxsize=3)
        self.condition = threading.Condition()
        self.scheduled = set()
        self.active = None
        self.character = None
        self.position = 0
        self.length = 0
        self.status = 'connecting'
        self.error = None
        self.word_gap = word_gap
        self.driver_factory = driver_factory or (lambda: DryRunDriver() if dry_run else open_driver(port, timeout))
        self.thread = threading.Thread(target=self._run, name='braille-serial', daemon=True)
        self.thread.start()

    def _emit(self, kind, **payload):
        self.events.put(dict(kind=kind, observed_at=datetime.now(timezone.utc).isoformat(), payload=payload))

    def submit(self, text):
        word = normalize_word(text)
        with self.condition:
            if self.stop.is_set() or self.error:
                return 'unavailable'
            if word in self.scheduled:
                return 'duplicate'
            try:
                self.pending.put_nowait(word)
            except queue.Full:
                self._emit('braille_queue_full', word=word)
                return 'full'
            self.scheduled.add(word)
            self._emit('braille_word_queued', word=word)
            self.condition.notify_all()
            return 'queued'

    def snapshot(self):
        with self.condition:
            return dict(state=self.status, word=self.active, character=self.character,
                        position=self.position, length=self.length, queued=self.pending.qsize(), error=self.error)

    def drain_events(self):
        result = []
        while True:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                return result

    def wait_idle(self, timeout=None):
        deadline = time.monotonic()+timeout if timeout is not None else None
        with self.condition:
            while self.scheduled and not self.error:
                if deadline is not None and time.monotonic() >= deadline:
                    raise BrailleError('Word output has not completed')
                self.condition.wait(.1)
            if self.error:
                raise BrailleError(self.error)

    def _run(self):
        driver = None
        try:
            driver = self.driver_factory()
            driver.wait_ready(self.stop)
            with self.condition:
                self.status = 'ready'
                self.condition.notify_all()
            self._emit('braille_connected')
            while not self.stop.is_set():
                try:
                    word = self.pending.get(timeout=.1)
                except queue.Empty:
                    continue
                with self.condition:
                    self.active, self.position, self.length = word, 0, len(word.replace(' ', ''))
                    self.status = 'displaying'
                self._emit('braille_word_started', word=word)
                for word_index, part in enumerate(word.split(' ')):
                    if word_index and self.stop.wait(self.word_gap):
                        raise InterruptedError('Braille output stopped')
                    for character in part:
                        with self.condition:
                            self.character = character
                            self.position += 1
                            position = self.position
                        print(f'Braille: {word} [{position}/{self.length}] {character}', file=sys.stderr, flush=True)
                        self._emit('braille_character_started', word=word, character=character, position=position)
                        result = driver.send_character(character, self.stop)
                        self._emit('braille_character', word=word, position=position, **result)
                self._emit('braille_word_completed', word=word)
                with self.condition:
                    self.scheduled.discard(word)
                    self.active, self.character = None, None
                    self.status = 'ready'
                    self.condition.notify_all()
        except InterruptedError:
            self._emit('braille_canceled', word=self.active, character=self.character)
        except Exception as error:
            with self.condition:
                self.error = str(error)
                self.status = 'error'
                self.condition.notify_all()
            self._emit('braille_error', word=self.active, character=self.character, error=str(error))
            print(f'Braille output stopped: {error}', file=sys.stderr, flush=True)
        finally:
            try:
                if driver is not None:
                    driver.close()
            except Exception as error:
                with self.condition:
                    self.error = self.error or str(error)
                    self.status = 'error'
            with self.condition:
                self.scheduled.clear()
                if not self.error:
                    self.status = 'stopped'
                self.condition.notify_all()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2.5)


def record_braille_events(output, store):
    for event in output.drain_events():
        if store is not None:
            payload = event['payload']
            store.record(event['kind'], label=payload.get('word'),
                description=f"Braille {payload.get('word', '')} {payload.get('character', '')}".strip(),
                severity=1 if event['kind']=='braille_error' else 0,
                observed_at=event['observed_at'], payload=payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-ports', action='store_true')
    parser.add_argument('--port', default='auto')
    parser.add_argument('--word')
    parser.add_argument('--timeout', type=float, default=15.)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.list_ports:
        for port in serial_ports():
            print(f'{port.device}\t{port.description}')
        return
    if not args.word:
        parser.error('Provide --word TEXT or --list-ports')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be positive')
    output = None
    try:
        word = normalize_word(args.word)
        port = None if args.dry_run else resolve_port(args.port)
        output = BrailleOutput(port, args.timeout, args.dry_run)
        output.submit(word)
        output.wait_idle()
    except (BrailleError, ValueError, ImportError) as error:
        parser.exit(1, f'{error}\n')
    except KeyboardInterrupt:
        pass
    finally:
        if output:
            output.close()


if __name__=='__main__':
    main()
