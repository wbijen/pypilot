import builtins
import importlib
import socket
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'pypilot'))

builtins._ = lambda value: value

import bufferedsocket


class FakePoll(object):
    def __init__(self, ready=None):
        self.ready = list(ready or [True])

    def register(self, *_args, **_kwargs):
        pass

    def poll(self, _timeout):
        if self.ready:
            state = self.ready.pop(0)
        else:
            state = True
        return [(1, select.POLLOUT)] if state else []


class FakeSocket(object):
    def __init__(self, send_sizes=None):
        self.send_sizes = list(send_sizes or [])
        self.sent = []
        self.closed = False
        self.blocking = None

    def setblocking(self, value):
        self.blocking = value

    def fileno(self):
        return 123

    def close(self):
        self.closed = True

    def send(self, data):
        if self.closed:
            raise OSError('closed')
        size = self.send_sizes.pop(0) if self.send_sizes else len(data)
        if isinstance(size, Exception):
            raise size
        size = min(size, len(data))
        self.sent.append(bytes(data[:size]))
        return size

    def recv(self, _size):
        return b''


import select


class BufferedSocketTests(unittest.TestCase):
    def make_socket(self, send_sizes=None, poll_ready=None, cls=None):
        cls = cls or bufferedsocket.PythonLineBufferedNonBlockingSocket
        fake_socket = FakeSocket(send_sizes)
        with mock.patch.object(bufferedsocket.select, 'poll', side_effect=lambda: FakePoll(poll_ready)):
            buffered = cls(fake_socket, ('127.0.0.1', 23322))
        return buffered, fake_socket

    def test_replaceable_updates_are_coalesced(self):
        buffered, fake_socket = self.make_socket()
        buffered.write('heading=1\n')
        buffered.write('heading=2\n')

        self.assertEqual(buffered.out_buffer_messages, 1)
        self.assertEqual(buffered.out_buffer, 'heading=2\n')

        buffered.flush()
        self.assertEqual(b''.join(fake_socket.sent), b'heading=2\n')
        self.assertEqual(buffered.out_buffer_messages, 0)

    def test_control_messages_are_preserved(self):
        buffered, _ = self.make_socket()
        buffered.write('watch={"heading": 1}\n')
        buffered.write('watch={"heading": 2}\n')
        buffered.write('error=test\n')

        self.assertEqual(buffered.out_buffer_messages, 3)
        self.assertIn('watch={"heading": 1}\n', buffered.out_buffer)
        self.assertIn('watch={"heading": 2}\n', buffered.out_buffer)
        self.assertIn('error=test\n', buffered.out_buffer)

    def test_partial_send_keeps_remaining_bytes(self):
        buffered, fake_socket = self.make_socket(send_sizes=[3, 64])
        buffered.sendfail_cnt = 7
        buffered.write('heading=123\n')

        buffered.flush()
        self.assertEqual(buffered.sendfail_cnt, 0)
        self.assertEqual(b''.join(fake_socket.sent), b'hea')
        self.assertEqual(buffered.out_buffer, 'ding=123\n')
        self.assertEqual(buffered.out_buffer_messages, 1)

        buffered.flush()
        self.assertEqual(b''.join(fake_socket.sent), b'heading=123\n')
        self.assertEqual(buffered.out_buffer_messages, 0)

    def test_backpressure_drops_old_replaceable_messages(self):
        buffered, fake_socket = self.make_socket()
        large_quoted_value = '"' + ('x' * 180) + '"\n'
        for index in range(400):
            buffered.write('value%d=%s' % (index, large_quoted_value))

        self.assertFalse(fake_socket.closed)
        self.assertLess(buffered.out_buffer_messages, 400)
        self.assertLessEqual(buffered.out_buffer_size, bufferedsocket.TCP_BUFFER_DROP)

    def test_disconnects_when_nonreplaceable_messages_exceed_limits(self):
        buffered, fake_socket = self.make_socket()
        for index in range(bufferedsocket.TCP_MESSAGE_CLOSE + 1):
            buffered.write('watch={"value": %d}\n' % index)
            if fake_socket.closed:
                break

        self.assertTrue(fake_socket.closed)
        self.assertFalse(buffered.socket)
        self.assertEqual(buffered.out_buffer_messages, 0)

    def test_server_style_updates_coalesce_before_flush(self):
        sys.modules.setdefault('zeroconf_service', types.SimpleNamespace(zeroconf=None))
        sys.modules.setdefault('nonblockingpipe', types.SimpleNamespace(NonBlockingPipe=lambda *args, **kwargs: (None, None)))
        server = importlib.import_module('server')

        buffered, fake_socket = self.make_socket()
        value = server.pypilotValue(object(), 'heading', info={'writable': True},
                                    connection=buffered, msg='heading=0\n')

        value.set('heading=1\n', False)
        value.set('heading=2\n', False)
        self.assertEqual(buffered.out_buffer, 'heading=2\n')

        buffered.flush()
        self.assertEqual(b''.join(fake_socket.sent), b'heading=2\n')

    def drain_live_socket(self, buffered):
        for _ in range(20):
            buffered.flush()
            if not buffered.out_queue and not buffered.out_partial:
                return
            time.sleep(0.01)
        self.fail('buffer did not drain')

    def test_real_socketpair_preserves_latest_update_and_control_message(self):
        left, right = socket.socketpair()
        try:
            buffered = bufferedsocket.LineBufferedNonBlockingSocket(left, ('local', 0))
            buffered.write('heading=1\n')
            buffered.write('heading=2\n')
            buffered.write('watch={"heading": 1}\n')

            self.drain_live_socket(buffered)
            right.settimeout(1)
            self.assertEqual(right.recv(4096).decode(), 'heading=2\nwatch={"heading": 1}\n')
        finally:
            left.close()
            right.close()

    def test_server_style_updates_coalesce_over_real_socketpair(self):
        sys.modules.setdefault('zeroconf_service', types.SimpleNamespace(zeroconf=None))
        sys.modules.setdefault('nonblockingpipe', types.SimpleNamespace(NonBlockingPipe=lambda *args, **kwargs: (None, None)))
        server = importlib.import_module('server')

        left, right = socket.socketpair()
        try:
            buffered = bufferedsocket.LineBufferedNonBlockingSocket(left, ('local', 0))
            value = server.pypilotValue(object(), 'heading', info={'writable': True},
                                        connection=buffered, msg='heading=0\n')
            value.set('heading=10\n', False)
            value.set('heading=11\n', False)

            self.drain_live_socket(buffered)
            right.settimeout(1)
            self.assertEqual(right.recv(4096).decode(), 'heading=11\n')
        finally:
            left.close()
            right.close()


if __name__ == '__main__':
    unittest.main()
