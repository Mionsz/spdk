#!/usr/bin/env python3

from argparse import ArgumentParser
import json
import logging
import signal
import socketserver
import threading
import time
from typing import Dict

log = logging.getLogger(__name__)


class QMPServerException(Exception):
    def __init__(self, description):
        self._description = description

    def __str__(self):
        return repr(self._description)


class QMPDataSyntaxException(QMPServerException):
    pass


class QMPSocketException(QMPServerException):
    pass


class QmpBaseRequest():
    def __init__(self, data: str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise QMPDataSyntaxException(f'JSONDecodeError when parsing "{data}"')
        if data.get('execute') is None:
            raise QMPDataSyntaxException(f'Command syntax error. Execute not found in "{data}"')
        self.data = data

    def cmd(self):
        return self.data.get('execute').lower()

    def args(self):
        return self.data.get('arguments')

    def id(self):
        return self.data.get('id')


class QmpBaseResponse():
    def __init__(self, id, data: Dict):
        self.data = data
        if id is not None:
            self.data['id'] = id
        log.debug(f'Message {self.__str__()}')

    def welcome_message():
        return QmpBaseResponse(None, {'QMP': {'version': {
                                     'qemu': {'micro': '0', 'minor': '0', 'major': '3'},
                                     'package': 'v3.0.0'}, 'capabilities': ['oob']}})

    def __str__(self):
        return json.dumps(self.data)

    def to_bytes(self):
        return bytes(self.__str__() + '\r\n', 'utf-8')


class QmpResponse(QmpBaseResponse):
    def __init__(self, id=None, args={}):
        super().__init__(id, {'return': args})


class QmpError(QmpBaseResponse):
    def __init__(self, id=None, cls='GenericError', desc='GenericError'):
        super().__init__(id, {'error': {'class': cls, 'desc': desc}})


class QmpEvent(QmpBaseResponse):
    def __init__(self, event, args=None):
        ts = time.time()
        data = {'event': event, 'timestamp': {
            'seconds': int(ts),
            'microseconds': int((ts-int(ts))*1000000)
        }}
        if args is not None:
            data['data'] = args
        super().__init__(None, data)


class QmpMockServer(socketserver.TCPServer):
    def __init__(self, bus, address, port, handler):
        super().__init__((address, port), handler)
        self.events = []
        self.dev_tree = [{'id': bus, 'fail': 'false', 'children': [
                         {'id': 'device_id', 'socket': 'full_path'}]}]
        self._commands = {
            'device_add': self.device_add,
            'device_del': self.device_del,
            'query-pci': self.query_pci
        }

    def _check_params(self, request, params):
        if request is None or params is None:
            return False
        for param in params:
            if param not in request:
                return False
        return True

    def _get_device(self, dev_id):
        for bus in self.dev_tree:
            if bus.get('id') == dev_id:
                return bus
            for it in bus.get('children', {}):
                if it.get('id') == dev_id:
                    return it
        return None

    def device_add(self, msg: QmpBaseRequest):
        if not self._check_params(msg.args(), ['id', 'bus', 'socket']):
            return QmpError(msg.id())
        if self._get_device(msg.args().get('id')) is not None:
            return QmpError(msg.id(), desc='Selected device ID already exist')
        for bus in self.dev_tree:
            if bus.get('id') == msg.args().get('bus'):
                # RPC for dev add
                new_dev = {'id': msg.args().get('id'),
                           'socket': msg.args().get('socket'),
                           'driver': 'vfio-user-pci',
                           'x-enable-migration': 'on'}
                bus['children'].append(new_dev)
                return QmpResponse(msg.id())
        return QmpError(msg.id(), desc=f'Bus name "{msg.args().get("bus")}" not found')

    def device_del(self, msg: QmpBaseRequest):
        if not self._check_params(msg.args(), ['id']):
            return QmpError(msg.id())
        dev_id = msg.args().get('id')
        for bus in self.dev_tree:
            if bus.get('id') == dev_id:
                return QmpError(msg.id())
            for it in bus.get('children', {}):
                if it.get('id') == dev_id:
                    # RPC for dev remove
                    self.events.append(QmpEvent("DEVICE_DELETED", {'device': dev_id}))
                    bus['children'].remove(it)
                    return QmpResponse(msg.id())
        return QmpError(msg.id(), 'DeviceNotFound')

    def query_pci(self, msg: QmpBaseRequest):
        return QmpResponse(msg.id(), self.dev_tree)

    def cmd_exec(self, msg: QmpBaseRequest):
        cmd = self._commands.get(msg.cmd())
        if cmd is None:
            return QmpError(msg.id(), 'CommandNotFound')
        return cmd(msg)

    def run(self):
        event = threading.Event()

        def signal_handler(signum, frame):
            event.set()

        for signum in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(signum, signal_handler)

        server_thread = threading.Thread(target=self.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        event.wait()
        self.shutdown()


class QmpHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for QMP server.

    Instantiated once per connection to the server.
    handle() is reserved for communication implementation with client.
    """
    def __init__(self, request, client_address, server: QmpMockServer):
        self.request = request
        self.client_address = client_address
        self.server = server
        self.socketf = self.request.makefile(mode='rw', encoding='utf-8')
        try:
            self.handle()
        finally:
            self.socketf.close()

    def send_message(self, msg: QmpBaseResponse):
        try:
            self.request.sendall(msg.to_bytes())
        except OSError as e:
            raise QMPSocketException(f'Critical. Send message error for "{str(msg)}": {e}') from e

    def get_message(self) -> QmpBaseRequest:
        while True:
            try:
                data = self.socketf.readline().strip()
                if data is None or data == '':
                    raise QMPSocketException('Data is none. Connection closed by peer')
                log.debug(f'Received message: {data}')
                return QmpBaseRequest(data)
            except QMPDataSyntaxException as e:
                log.debug(f'Get message QMPDataSyntaxException for "{data}": {str(e)}')
                self.send_message(QmpError(None, desc=str(e)))
            except OSError as e:
                raise QMPSocketException(f'Critical. Read message error: "{e}"') from e

    def negotiate_capabilities(self):
        self.send_message(QmpBaseResponse.welcome_message())
        msg = self.get_message()
        while msg.cmd() != 'qmp_capabilities':
            self.send_message(QmpError(None, 'CommandNotFound', 'Negotiation mode still active'))
            msg = self.get_message()
        self.send_message(QmpResponse(msg.id()))

    def handle(self):
        try:
            self.negotiate_capabilities()
            while True:
                if len(self.server.events) > 0:
                    time.sleep(1)
                    self.send_message(self.server.events.pop())
                resp = self.server.cmd_exec(self.get_message())
                self.send_message(resp)
        except QMPSocketException as e:
            log.debug(f'Socket QMPSocketException, exiting now: {e}')


def parse_argv():
    parser = ArgumentParser(description='Mock QEMU Machine Protocol (QMP) server')
    parser.add_argument('--address', '-a', default='127.0.0.1',
                        help='IP address for QMP server to listen on')
    parser.add_argument('--port', '-p', default=10500, type=int,
                        help='Port number for QMP server to listen on')
    parser.add_argument('--bus', '-b', default='spdk_bus',
                        help='Hot-pluggable bus name')
    return parser.parse_args()


if __name__ == '__main__':
    argv = parse_argv()
    server = QmpMockServer(argv.bus, argv.address, argv.port, QmpHandler)
    server.run()
