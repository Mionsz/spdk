#!/usr/bin/env python3

import socket
from socket import error as SocketError
import json
import logging
import sys
from typing import (Any, Dict)

log = logging.getLogger(__name__)


QMPMessage = Dict[str, Any]
'''
Base class for all QMPBaseClass messages
'''


class QMPError(Exception):
    '''
    Base Exception Class for QMPClient implementation
    '''


class QMPSocketError(QMPError):
    pass


class QMPRequestError(QMPError):
    '''
    Exception Class for handling request response errors
    '''
    def __init__(self, reply: QMPMessage):
        err = reply.get('error', {}).get('class', 'Undefined')
        desc = reply.get('error', {}).get('desc', 'Unknown')
        super().__init__(err, desc)


class QMPClient():
    '''
    QMPBaseClass implements a low level connection to QMP socket

    :param family is one of socket.AF_INET or socket.AF_UNIX
    :param address is tuple(adress, port) for socket.AF_INET
                   or a path string for socket.AF_UNIX
    :param s_timeout: timeout in seconds to use for the connection
    :raise QMPError: for most error cases
    '''
    def __init__(self, family=socket.AF_INET,
                       address=('127.0.0.1', 55556),
                       s_timeout: float = 8.0):
        self._socket_timeout = s_timeout
        self._net = family
        self._path = address
        self._s = socket.socket(self._net, socket.SOCK_STREAM)
        self._exec_id = 0
        self._socketf = None

    def __enter__(self):
        self._start()
        return self
    
    def __exit__(self):
        self._disconnect_socket()

    def _start(self):
        try:
            self._connect_socket()
            self._negotiate_capabilities()
            return True
        except SocketError as e:
            raise QMPSocketError('Socket ERROR!', e.strerror) from e

    def _get_next_exec_id(self):
        self._exec_id += 1
        return str(self._exec_id)

    def _connect_socket(self):
        if not self._is_connected():
            self._s.connect(self._path)
            self._s.settimeout(self._socket_timeout)
            self._socketf = self._s.makefile(mode='rw', encoding='utf-8')

    def _disconnect_socket(self):
        if self._s is not None:
            self._s.close()
            self._s = None
        if self._socketf is not None:
            self._socketf.close()
            self._socketf = None

    def _is_connected(self) -> bool:
        return self._socketf is not None

    def _negotiate_capabilities(self) -> QMPMessage:
        self._qmp_capabilities = self._receive()
        if 'QMP' not in self._qmp_capabilities:
            raise QMPError('Could not parse welcome message!')
        return self.exec('qmp_capabilities')

    def _receive(self) -> QMPMessage:
        while True:
            try:
                data = self._socketf.readline()
                if data is None:
                    raise QMPError('Data is none, got disconnected!')
                log.debug(f'Received: {data}')
                resp = json.loads(data)
            except SocketError as e:
                raise QMPError('Failed reading from socket!') from e
            except EOFError as e:
                raise QMPError('Got unexpected EOF from socket!') from e
            except json.JSONDecodeError as e:
                raise QMPError('QMP Message ERROR, decode failed') from e

            if 'event' not in resp:
                return resp

    def _send(self, msg):
        log.debug(f'Sending: {msg}')
        try:
            self._s.sendall(bytes(json.dumps(msg), 'utf-8'))
        except socket.timeout:
            raise QMPError('Socket timeouted occured while sending command!')
        except SocketError as err:
            raise QMPError('Socket ERROR while sending command!') from err

    def exec(self, cmd: str, args: Dict[str, object] = None) -> QMPMessage:
        '''
        Execute QMP cmd and read result. Returns resulting message

        :param cmd: string name of the command to execute
        :param args: optional arguments dictionary to pass
        '''
        cmd_id = self._get_next_exec_id()
        msg = {'execute': cmd, 'id': cmd_id}
        if args is not None:
            msg['arguments'] = args

        self._send(msg)
        result = self._receive()

        if result.get('id') != cmd_id:
            raise QMPError('QMP Protocol Error, invalid result id!')
        elif 'error' in result:
            raise QMPRequestError(result)
        return result


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    with QMPClient() as cli:
        print('Enter command to execute: [exit for quiting]')
        cmd = sys.stdin.readline()[:-1]
        while cmd != 'exit':
            print('args as json: {"n":"val"} or pass empty')
            args = sys.stdin.readline()[:-1]
            if args == "" or args == "\"\"":
                args = None
            else:
                args = json.loads(args)
            res = cli.exec(cmd, args)
            print('Enter command to execute: [exit for quiting]')
            cmd = sys.stdin.readline()[:-1]
