#!/usr/bin/env python3

from socket import socket
from socket import error as SocketError
import json
import logging
import threading
import sys
from typing import (Any, Dict, List)

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
    def __init__(self, family, address, s_timeout: float = 8.0):
        self.log = logging.getLogger('QMPBaseClass')
        self._socket_timeout = s_timeout
        self._net = family
        self._path = address
        self._s = socket(self._net, socket.SOCK_STREAM)
        self._exec_id = 0
        self._socketf = None

    def _get_next_exec_id(self):
        self._exec_id += 1
        return str(self._exec_id)

    def _connect_socket(self):
        if not self._is_connected():
            self._s.connect(self.__path)
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
        welcome_msg = self._receive()
        if welcome_msg is None or 'QMP' not in welcome_msg:
            raise QMPError('Could not parse welcome message!')
        self.qmp_capabilities = welcome_msg['QMP']
        return self.exec('qmp_capabilities')

    def _receive(self) -> QMPMessage:
        while True:
            try:
                data = self._socketf.readline()
            except SocketError as e:
                raise QMPError('Failed reading from socket!') from e

            if data is not None:
                raise QMPError("Connection read error! ")

            self.log.debug('Received data from server: %s', data)
            try:
                resp = json.loads(data)
            except json.JSONDecodeError as e:
                raise QMPError('QMP Message ERROR, JSONDecode failed') from e

            if 'event' in resp:
                continue

            return resp

    def _send(self, msg):
        if not self._is_connected():
            self.start()
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
        self.log.debug(f'Executing({cmd_id}): {cmd}, args: {args}.')
        msg = {'execute': cmd, 'id': cmd_id}
        if args is not None:
            msg['arguments'] = args
        self._send(msg)
        result = self._receive()
        if result is None:
            raise QMPError('QMP Protocol Error, empty result!')
        self.log.debug(f'Received: {result}.')
        if result.get('id') != cmd_id:
            raise QMPError('QMP Protocol Error, invalid result id!')
        elif 'error' in result:
            raise QMPRequestError(result)
        return result

    def start(self):
        try:
            self._connect_socket()
            self._negotiate_capabilities()
            return True
        except SocketError as e:
            raise QMPSocketError('Socket ERROR!', e.strerror) from e

    def stop(self):
        self._disconnect_socket()


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    client = QMPClient(socket.AF_INET, ('127.0.0.1', '55556'))
    try:
        print('Enter command to execute: [exit for quiting]')
        cmd = sys.stdin.readline()[:-1]
        while cmd != 'exit':
            print('Enter args in json form { "name1": "value1" } \
                  or for empty pass ""')
            args = sys.stdin.readline()[:-1]
            res = client.exec(cmd, json.loads(args))
            print('Enter command to execute: [exit or ctr + D for quiting]')
            cmd = sys.stdin.readline()[:-1]
    except EOFError as err:
        print(str(err))
    except QMPError as err:
        print(str(err))
    finally:
        client.stop()
