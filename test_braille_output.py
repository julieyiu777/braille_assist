"""Test the original ESP32 serial protocol without moving physical servos."""
from collections import deque
import contextlib
import io
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from braille_output import (BrailleError, BrailleOutput, BrailleTimeout, READY,
                            SerialBrailleDriver, normalize_word, resolve_port)


class FakeClock:
    def __init__(self):
        self.value=0.

    def __call__(self):
        self.value+=.05
        return self.value


class FakeSerial:
    def __init__(self, respond=True):
        self.lines=deque()
        self.writes=[]
        self.respond=respond
        self.closed=False

    def write(self,value):
        self.writes.append(value)
        if self.respond:
            if value==b'?\n':
                self.lines.extend([b'boot text\r\n',(READY+'\r\n').encode()])
            else:
                character=value.decode().strip()
                self.lines.extend([(READY+'\r\n').encode(),b'Back at rest.\r\n',
                    f'Showing {character} on channels: 0 1 6\r\n'.encode(),b'Back at ',b'rest.\r\n'])
        return len(value)

    def flush(self):
        pass

    def read_until(self,expected,size):
        return self.lines.popleft() if self.lines else b''

    def close(self):
        self.closed=True


class SerialProtocolTests(unittest.TestCase):
    def test_exact_original_protocol_sends_one_letter_and_waits_for_matching_completion(self):
        serial=FakeSerial();driver=SerialBrailleDriver(serial,clock=FakeClock())
        stop=threading.Event();driver.wait_ready(stop)
        for character in 'pens':
            result=driver.send_character(character,stop)
            self.assertTrue(result['acknowledged'])
            self.assertFalse(result['position_feedback'])
            self.assertEqual(result['channels'],[0,1,6])
        self.assertEqual(serial.writes,[b'?\n',b'p\n',b'e\n',b'n\n',b's\n'])
        driver.close();self.assertTrue(serial.closed)

    def test_readiness_probes_do_not_send_servo_letters(self):
        serial=FakeSerial(False);driver=SerialBrailleDriver(serial,clock=FakeClock())
        with self.assertRaises(BrailleTimeout):
            driver.wait_ready(threading.Event(),startup_timeout=2.)
        self.assertTrue(all(value==b'?\n' for value in serial.writes))

    def test_timeout_does_not_resend_a_character(self):
        serial=FakeSerial(False);driver=SerialBrailleDriver(serial,timeout=.2,clock=FakeClock())
        with self.assertRaises(BrailleTimeout):
            driver.send_character('p',threading.Event())
        self.assertEqual(serial.writes,[b'p\n'])

    def test_wrong_character_acknowledgment_is_an_error(self):
        serial=FakeSerial(False);serial.lines.append(b'Showing z on channels: 0\n')
        driver=SerialBrailleDriver(serial,clock=FakeClock())
        with self.assertRaises(BrailleError):
            driver.send_character('p',threading.Event())

    def test_servo_controller_failure_stops_readiness(self):
        serial=FakeSerial(False);serial.lines.append(b'PCA9685 did not respond at 0x40.\n')
        driver=SerialBrailleDriver(serial,clock=FakeClock())
        with self.assertRaisesRegex(BrailleError,'PCA9685'):
            driver.wait_ready(threading.Event())

    def test_repeated_characters_are_independent_commands(self):
        serial=FakeSerial();driver=SerialBrailleDriver(serial,clock=FakeClock())
        for character in 'see':
            driver.send_character(character,threading.Event())
        self.assertEqual(serial.writes,[b's\n',b'e\n',b'e\n'])

    def test_cancel_does_not_send_a_character(self):
        serial=FakeSerial();driver=SerialBrailleDriver(serial)
        stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):driver.send_character('p',stop)
        self.assertEqual(serial.writes,[])

    def test_auto_selection_ignores_bluetooth_and_requires_one_usb_board(self):
        bluetooth=SimpleNamespace(device='/dev/cu.Bluetooth',description='Bluetooth',vid=None)
        usb=SimpleNamespace(device='/dev/cu.usbmodem1',description='ESP32 USB',vid=0x303a)
        with patch('braille_output.serial_ports',return_value=[bluetooth,usb]):
            self.assertEqual(resolve_port('auto'),usb.device)
        with patch('braille_output.serial_ports',return_value=[bluetooth]):
            with self.assertRaises(BrailleError):resolve_port('auto')
        with patch('braille_output.serial_ports',return_value=[usb,usb]):
            with self.assertRaises(BrailleError):resolve_port('auto')

    def test_label_normalization_rejects_unsupported_text_before_moving_anything(self):
        self.assertEqual(normalize_word(' RED_BULL '),'red bull')
        for text in ('','pen2','café','pens!'):
            with self.assertRaises(ValueError):normalize_word(text)

    def test_arduino_sketch_preserves_users_firmware(self):
        self.assertEqual(Path('braile_training.txt').read_bytes(),
                         Path('firmware/braille_serial/braille_serial.ino').read_bytes())


class WordQueueTests(unittest.TestCase):
    def test_words_do_not_interleave_and_active_duplicates_are_not_requeued(self):
        started=threading.Event();release=threading.Event();sent=[]
        class Driver:
            def wait_ready(self,stop):pass
            def send_character(self,character,stop):
                sent.append(character)
                if len(sent)==1:
                    started.set()
                    if not release.wait(2):raise RuntimeError('Test synchronization failed')
                return dict(character=character,acknowledged=True,simulated=False,position_feedback=False)
            def close(self):pass
        with contextlib.redirect_stderr(io.StringIO()):
            output=BrailleOutput(driver_factory=Driver)
            try:
                self.assertEqual(output.submit('pens'),'queued')
                self.assertTrue(started.wait(1))
                self.assertEqual(output.submit('pens'),'duplicate')
                self.assertEqual(output.submit('soda'),'queued')
                self.assertEqual(output.submit('chips'),'queued')
                release.set();output.wait_idle(timeout=3)
                self.assertEqual(''.join(sent),'penssodachips')
                events=output.drain_events()
                completed=[event['payload']['word'] for event in events if event['kind']=='braille_word_completed']
                self.assertEqual(completed,['pens','soda','chips'])
            finally:
                release.set();output.close()

    def test_failure_stops_remaining_letters_and_pending_words(self):
        sent=[]
        class Driver:
            def wait_ready(self,stop):pass
            def send_character(self,character,stop):
                sent.append(character)
                if character=='e':raise BrailleTimeout('No completion acknowledgment')
                return dict(character=character,acknowledged=True)
            def close(self):pass
        with contextlib.redirect_stderr(io.StringIO()):
            output=BrailleOutput(driver_factory=Driver)
            try:
                output.submit('pens');output.submit('soda')
                with self.assertRaises(BrailleError):output.wait_idle(timeout=2)
                self.assertEqual(sent,['p','e'])
                self.assertEqual(output.submit('chips'),'unavailable')
            finally:output.close()

    def test_dry_run_never_claims_hardware_acknowledgment(self):
        with contextlib.redirect_stderr(io.StringIO()):
            output=BrailleOutput(dry_run=True)
            try:
                output.submit('pens');output.wait_idle(timeout=2)
                events=[event for event in output.drain_events() if event['kind']=='braille_character']
                self.assertEqual([event['payload']['character'] for event in events],list('pens'))
                self.assertTrue(all(event['payload']['simulated'] for event in events))
                self.assertTrue(all(not event['payload']['acknowledged'] for event in events))
            finally:output.close()


if __name__=='__main__':
    unittest.main()
