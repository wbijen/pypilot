#!/usr/bin/env python
#
#   Copyright (C) 2019 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.

import time, select, socket, os

TCP_BUFFER_WARN = 16384
TCP_BUFFER_DROP = 49152
TCP_BUFFER_CLOSE = 131072
TCP_MESSAGE_WARN = 128
TCP_MESSAGE_DROP = 512
TCP_MESSAGE_CLOSE = 2048
UDP_BUFFER_LIMIT = 400
CONTROL_MESSAGES = {'error', 'watch', 'values', 'udp_port', 'profiles', 'profile'}


class BufferedMessage(object):
    def __init__(self, text, key=False):
        self.text = text
        self.data = text.encode()
        self.key = key
        self.offset = 0

    def remaining(self):
        return len(self.data) - self.offset


class BufferedSocketBase(object):
    # TCP writes keep newline-terminated messages intact so replaceable updates
    # can be coalesced and old value traffic can be dropped before disconnecting.
    def init_socket(self, connection, address):
        connection.setblocking(0)
        self.socket = connection
        self.address = address

        self.out_queue = []
        self.out_buffer_size = 0
        self.out_buffer_messages = 0
        self.out_partial = ''
        self.out_buffer = ''
        self.replaceable_messages = {}
        self.buffer_warned = False

        self.udp_port = False
        self.udp_out_buffer = ''
        self.udp_socket = False

        self.pollout = select.poll()
        self.pollout.register(connection, select.POLLOUT)
        self.sendfail_msg = 1
        self.sendfail_cnt = 0

    def fileno(self):
        if self.socket:
            return self.socket.fileno()
        return 0

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = False
        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = False

    def _update_out_buffer(self):
        parts = []
        for entry in self.out_queue:
            if entry.offset:
                parts.append(entry.data[entry.offset:].decode())
            else:
                parts.append(entry.text)
        if self.out_partial:
            parts.append(self.out_partial)
        self.out_buffer = ''.join(parts)

    def _message_key(self, text):
        if not text.endswith('\n'):
            return False
        if '\n' in text[:-1] or '\r' in text[:-1]:
            return False
        if '=' not in text:
            return False
        key = text.split('=', 1)[0]
        if not key or key in CONTROL_MESSAGES:
            return False
        return key

    def _remove_message(self, index):
        entry = self.out_queue.pop(index)
        self.out_buffer_size -= entry.remaining()
        self.out_buffer_messages -= 1
        if entry.key and self.replaceable_messages.get(entry.key) is entry:
            del self.replaceable_messages[entry.key]
        return entry

    def _queue_message(self, text):
        key = self._message_key(text)
        if key:
            previous = self.replaceable_messages.get(key)
            if previous and not previous.offset:
                previous_remaining = previous.remaining()
                previous.text = text
                previous.data = text.encode()
                self.out_buffer_size += previous.remaining() - previous_remaining
                self._update_out_buffer()
                return

        entry = BufferedMessage(text, key)
        self.out_queue.append(entry)
        self.out_buffer_size += entry.remaining()
        self.out_buffer_messages += 1
        if key:
            self.replaceable_messages[key] = entry

    def _drop_replaceable_messages(self):
        dropped = 0
        dropped_bytes = 0
        index = 0
        while index < len(self.out_queue):
            if self.out_buffer_size <= TCP_BUFFER_WARN and self.out_buffer_messages <= TCP_MESSAGE_WARN:
                break
            entry = self.out_queue[index]
            if entry.offset or not entry.key:
                index += 1
                continue
            dropped += 1
            dropped_bytes += entry.remaining()
            self._remove_message(index)
        if dropped:
            print(_('pypilot socket dropped stale updates'), self.address,
                  dropped, dropped_bytes, self.out_buffer_size, self.out_buffer_messages)

    def _check_queue_limits(self):
        if not self.socket:
            return

        warn = self.out_buffer_size > TCP_BUFFER_WARN or self.out_buffer_messages > TCP_MESSAGE_WARN
        if warn and not self.buffer_warned:
            print(_('pypilot socket buffer warning'), self.address,
                  self.out_buffer_size, self.out_buffer_messages)
        self.buffer_warned = warn

        if self.out_buffer_size > TCP_BUFFER_DROP or self.out_buffer_messages > TCP_MESSAGE_DROP:
            self._drop_replaceable_messages()

        if self.out_buffer_size > TCP_BUFFER_CLOSE or self.out_buffer_messages > TCP_MESSAGE_CLOSE:
            print(_('overflow in pypilot socket'), self.address,
                  self.out_buffer_size, self.out_buffer_messages, os.getpid())
            self.out_queue = []
            self.replaceable_messages = {}
            self.out_buffer_size = 0
            self.out_buffer_messages = 0
            self.out_partial = ''
            self._update_out_buffer()
            self.close()

    def _queue_tcp(self, data):
        if self.out_partial:
            data = self.out_partial + data
            self.out_partial = ''

        start = 0
        while True:
            end = data.find('\n', start)
            if end < 0:
                break
            self._queue_message(data[start:end+1])
            start = end + 1

        if start < len(data):
            tail = data[start:]
            if start == 0:
                self._queue_message(tail)
            else:
                self.out_partial = tail

        self._check_queue_limits()
        self._update_out_buffer()

    def write(self, data, udp=False):
        if not self.socket:
            return
        if udp and self.udp_port:
            self.udp_out_buffer += data
            if len(self.udp_out_buffer) > UDP_BUFFER_LIMIT:
                print(_('overflow in pypilot udp socket'), self.address, len(self.udp_out_buffer))
                self.udp_out_buffer = ''
            return
        self._queue_tcp(data)

    def _flush_partial(self):
        if self.out_partial:
            self._queue_message(self.out_partial)
            self.out_partial = ''
            self._check_queue_limits()
            self._update_out_buffer()

    def flush(self):
        if self.udp_out_buffer:
            try:
                if not self.udp_socket:
                    self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                count = self.udp_socket.sendto(self.udp_out_buffer.encode(), (self.address[0], self.udp_port))
            except Exception as e:
                print('udp socket failed to send', e)
                count = 0
                self.close()
            if count != len(self.udp_out_buffer):
                print(_('failed to send udp packet'), self.address)
            self.udp_out_buffer = ''

        self._flush_partial()
        if not self.out_queue or not self.socket:
            return

        try:
            if not self.pollout.poll(0):
                if self.sendfail_cnt >= self.sendfail_msg:
                    print(_('pypilot socket failed to send to'), self.address, self.sendfail_cnt)
                    self.sendfail_msg *= 10
                self.sendfail_cnt += 1
                if self.sendfail_cnt > 100:
                    self.close()
                    return

            entry = self.out_queue[0]
            t0 = time.monotonic()
            count = self.socket.send(entry.data[entry.offset:])
            t1 = time.monotonic()

            if t1-t0 > .1:
                print(_('socket send took too long!?!?'), self.address, t1-t0, self.out_buffer_size)
            if count <= 0:
                print(_('socket send error'), self.address, count)
                self.close()
                return

            self.sendfail_cnt = 0
            self.sendfail_msg = 1
            entry.offset += count
            self.out_buffer_size -= count
            if entry.offset >= len(entry.data):
                self._remove_message(0)
            self._update_out_buffer()
        except Exception as e:
            print(_('pypilot socket exception'), self.address, e, os.getpid(), self.socket)
            self.close()


try:
    from pypilot.linebuffer import linebuffer
except Exception as linebuffer_error:
    linebuffer = False
    print(_('falling back to python nonblocking socket, will consume more cpu'), linebuffer_error)


class PythonLineBufferedNonBlockingSocket(BufferedSocketBase):
    def __init__(self, connection, address):
        self.init_socket(connection, address)
        self.b = False
        self.in_buffer = ''
        self.no_newline_pos = 0

    def recvdata(self):
        size = 4096
        try:
            data = self.socket.recv(size).decode()
        except Exception as e:
            print(_('error receiving data'), e)
            return False

        length = len(data)
        if length == 0:
            return False

        self.in_buffer += data
        if length == size:
            return length + self.recvdata()
        return length

    def readline(self):
        while self.no_newline_pos < len(self.in_buffer):
            c = self.in_buffer[self.no_newline_pos]
            if c == '\n' or c == '\r':
                ret = self.in_buffer[:self.no_newline_pos] + '\n'
                self.in_buffer = self.in_buffer[self.no_newline_pos+1:]
                if self.no_newline_pos:
                    self.no_newline_pos = 0
                    return ret
                continue
            self.no_newline_pos += 1
        return ''


if linebuffer:
    class CLineBufferedNonBlockingSocket(BufferedSocketBase):
        def __init__(self, connection, address):
            self.init_socket(connection, address)
            self.b = linebuffer.LineBuffer(connection.fileno())

        def recvdata(self):
            return self.b.recv()

        def readline(self):
            return self.b.line()

    LineBufferedNonBlockingSocket = CLineBufferedNonBlockingSocket
else:
    CLineBufferedNonBlockingSocket = False
    LineBufferedNonBlockingSocket = PythonLineBufferedNonBlockingSocket
