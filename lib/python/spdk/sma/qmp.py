#!/usr/bin/env python3

import socket
from socket import error as SocketError
import time
import json
import logging
import sys
from typing import (Any, Dict)
from argparse import ArgumentParser

log = logging.getLogger(__name__)


QMPMessage = Dict[str, Any]
'''
Base class for all QMPBaseClass messages
'''


class QMPError(Exception):
    '''
    Base Exception Class for QMPClient implementation
    '''
    def __init__(self, message, code='internal', args=None):
        self.code = repr(code)
        self.message = repr(message)
        self.description = f'QMP Error ({self.code}): {self.message}'

    def __str__(self):
        return repr(self.description)


class QMPSocketError(QMPError):
    '''
    Exception Class for socket exceptions in QMPClient implementation
    '''
    def __init__(self, message, code='socket', args=None):
        super().__init__(message, code, args)


class QMPRequestError(QMPError):
    '''
    Exception Class for handling request response errors
    '''
    def __init__(self, reply: QMPMessage):
        self.error_class = reply.get('error', {}).get('class', 'Undefined')
        self.error_msg = reply.get('error', {}).get('desc', 'Unknown')
        super().__init__(self.error_class, self.error_class)


class QMPClient():
    '''
    QMPBaseClass implements a low level connection to QMP socket

    :param family is one of [socket.AF_INET, socket.AF_UNIX]
    :param address is tuple(address, port) for socket.AF_INET
                   or a path string for socket.AF_UNIX
    :param timeout: timeout in seconds to use for the connection
    :raise QMPError: for most error cases
    '''
    def __init__(self,
                 address=('127.0.0.1', 55556),
                 family: socket.AddressFamily = socket.AF_INET,
                 timeout: float = 8.0):
        self._exec_id = 0
        self._rcv_loop_timeout = 2*timeout
        self._socketf = None
        self._address = address
        try:
            self._socket = socket.socket(family, socket.SOCK_STREAM)
            self._socket.settimeout(timeout)
        except OSError as e:
            raise QMPSocketError('Create: exception while creating') from e

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self._disconnect_socket()

    def _start(self):
        '''
        Exit negotiation mode and enter command mode

        Based on: https://wiki.qemu.org/Documentation/QMP
        Part of communication done after connect.
        As stated in Capabilities Negotiation paragraph, for new connection
        QMP sends greetings msg and enters capabilities negotiation mode.
        To enter command mode, the qmp_capabilities command must be issued.
        Can be issued only once per session or the QMP will report an error.
        '''
        self._connect()
        self._qmp_capabilities = self._receive()
        if 'QMP' not in self._qmp_capabilities:
            raise QMPError('NegotiateCap: protocol error, wrong message')
        self.exec('qmp_capabilities')

    def _get_next_exec_id(self):
        self._exec_id += 1
        return str(self._exec_id)

    def _connect(self):
        try:
            if not self._is_connected():
                self._socket.connect(self._address)
                self._socketf = self._socket.makefile(mode='rw', encoding='utf-8')
        except SocketError as e:
            raise QMPSocketError('Connect: could not connect') from e

    def _disconnect_socket(self):
        if self._socket is not None:
            self._socket.close()
        if self._socketf is not None:
            self._socketf.close()
        self._socket = None
        self._socketf = None

    def _is_connected(self) -> bool:
        return self._socketf is not None

    def _check_event(self, event, received):
        '''
        Method for cheking if "received" is the "event" we are waiting for.
        :param event: dictionary description of event, mandatory fields are
                      'event' = QMP name of the event
                      'data' = event specific params in form of a dict.
        :param received: received QMP event to check.
        '''
        if  event['event'].lower() != received['event'].lower():
            return False
        for it in event.get('data', {}).items():
            if it not in received.get('data', {}).items():
                return False
        return True

    def _receive(self, event=None) -> QMPMessage:
        timeout_begin = time.time()
        while self._rcv_loop_timeout > (time.time() - timeout_begin):
            try:
                data = self._socketf.readline()
                if data is None:
                    raise QMPSocketError('Receive: socket got disconnected')
                log.debug(f'Received: {data}')
                resp = json.loads(data)
            except SocketError as e:
                raise QMPSocketError('Receive: socket read failed') from e
            except EOFError as e:
                raise QMPSocketError('Receive: socket unexpected EOF') from e
            except json.JSONDecodeError as e:
                raise QMPError('Receive: QMP message decode failed') from e
            if 'event' not in resp:
                return resp
            elif event is not None and self._check_event(event, resp):
                return resp
        raise QMPRequestError('Timed out while processing QMP receive loop.')

    def _send(self, msg):
        log.debug(f'Sending: {msg}')
        try:
            self._socket.sendall(bytes(json.dumps(msg), 'utf-8'))
        except TimeoutError as e:
            raise QMPSocketError('Send: got socket timeout error') from e
        except SocketError as e:
            raise QMPSocketError('Send: got system socket error') from e

    def exec(self, cmd: str, args: Dict[str, object] = None) -> QMPMessage:
        '''
        Execute QMP cmd and read result. Returns resulting message

        :param cmd: string name of the command to execute
        :param args: optional arguments dictionary to pass
        '''
        cmd_id = self._get_next_exec_id()
        msg = {'execute': cmd, 'id': cmd_id}
        if args is not None and len(args):
            msg['arguments'] = args

        self._send(msg)
        result = self._receive()

        if result.get('id') != cmd_id:
            raise QMPError('QMP Protocol Error, invalid result id')
        elif 'error' in result:
            raise QMPRequestError(result)
        return result


def parse_argv():
    parser = ArgumentParser(description='QEMU Machine Protocol (QMP) client')
    parser.add_argument('--address', '-a', default='127.0.0.1',
                        help='IP address of QMP server instance to connect to')
    parser.add_argument('--port', '-p', default=55556, type=int,
                        help='Port number of QMP server instance to connect to')
    return parser.parse_args()


def main(args):
    argv = parse_argv()
    request = json.loads(sys.stdin.read())
    with QMPClient((argv.address, argv.port)) as cli:
        result = cli.exec(request['execute'], request.get('arguments'))
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main(sys.argv[1:])
