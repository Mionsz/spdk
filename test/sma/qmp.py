#!/usr/bin/env python3

from argparse import ArgumentParser
import json
import logging
import signal
import socketserver
import threading
import time

log = logging.getLogger(__name__)


class QmpMessageBase():
    def __init__(self, id, data):
        self.data = data
        if id is not None:
            self.data['id'] = id
        log.debug(f'Message {self.__str__()}')

    def __str__(self):
        return json.dumps(self.data)

    def to_bytes(self):
        return bytes(self.__str__() + '\r\n', 'utf-8')


class QmpResponse(QmpMessageBase):
    def __init__(self, id=None, args={}):
        super().__init__(id, {'return': args})


class QmpError(QmpMessageBase):
    def __init__(self, id=None, cls='GenericError', desc='GenericError'):
        super().__init__(id, {'error': {'class': cls, 'desc': desc}})


class QmpEvent(QmpMessageBase):
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
    def __init__(self, address, port, handler):
        super().__init__((address, port), handler)
        self.events = []
        self.dev_tree = [{'id': 'spdk_bus', 'fail': 'false', 'children': [
                         {'id': 'device_id', 'socket': 'full_path'}]}]

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

    def device_add(self, cmd_id, args):
        if not self._check_params(args, ['id', 'bus', 'socket']):
            return QmpError(cmd_id)
        if self._get_device(args.get('id')) is not None:
            return QmpError(cmd_id, desc='Id already exists')
        for bus in self.dev_tree:
            if bus.get('id') == args.get('bus'):
                # RPC for dev add
                new_dev = {'id': args.get('id'),
                           'socket': args.get('socket'),
                           'driver': 'vfio-user-pci',
                           'x-enable-migration': 'on'}
                bus['children'].append(new_dev)
                return QmpResponse(cmd_id)
        return QmpError(cmd_id, desc=f'Bus {args.get("bus")} not found')

    def device_remove(self, cmd_id, args):
        if not self._check_params(args, ['id']):
            return QmpError(cmd_id)
        dev_id = args.get('id')
        for bus in self.dev_tree:
            if bus.get('id') == dev_id:
                return QmpError(cmd_id)
            for it in bus.get('children', {}):
                if it.get('id') == dev_id:
                    # RPC for dev remove
                    self.events.append(QmpEvent("DEVICE_DELETED", {'device': dev_id}))
                    bus['children'].remove(it)
                    return QmpResponse(cmd_id)
        return QmpError(cmd_id, 'DeviceNotFound')

    def device_list_properties(self, cmd_id, args):
        if not self._check_params(args, ['typename']):
            return QmpError(cmd_id)
        dev = self._get_device(args.get('typename'))
        if dev is None:
            return QmpError(cmd_id, 'DeviceNotFound')
        return QmpResponse(cmd_id, dev)

    def cmd_exec(self, msg):
        cmd = msg.get('execute').lower()
        args = msg.get('arguments')
        cmd_id = msg.get('id')
        if cmd == 'device_add':
            resp = self.device_add(cmd_id, args)
        elif cmd == 'device_remove':
            resp = self.device_remove(cmd_id, args)
        elif cmd == 'device-list-properties':
            resp = self.device_list_properties(cmd_id, args)
        else:
            resp = QmpError(cmd_id, 'CommandNotFound')
        return resp

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

    def get_message(self):
        while True:
            try:
                data = str(self.request.recv(2048).strip(), 'utf-8')
                if data is None or data == '':
                    self._conn = False
                    log.debug('Client disconnected.')
                    return None
                log.debug(f'Received message: {data}')
                data = json.loads(data)
                if data.get('execute') is None:
                    raise json.JSONDecodeError('Command syntax error.')
                return data
            except json.JSONDecodeError as e:
                log.error(f'Data decode error: {str(e)}')
                self.request.sendall(QmpError(None).to_bytes())

    def negotiation_mode(self):
        welcome = {'QMP': {
            'version': {
                'qemu': {'micro': '0', 'minor': '0', 'major': '3'},
                'package': 'v3.0.0'},
            'capabilities': ['oob']}}
        self.request.sendall((bytes(json.dumps(welcome) + '\r\n', 'utf-8')))
        msg = self.get_message()
        while self._conn and msg.get('execute').lower() != 'qmp_capabilities':
            self.request.sendall(QmpError(None, 'CommandNotFound').to_bytes())
            msg = self.get_message()
        if self._conn:
            self.request.sendall(QmpResponse(msg.get('id')).to_bytes())

    def handle(self):
        self.number = 1
        self._conn = True
        self.negotiation_mode()
        while self._conn:
            if len(self.server.events) > 0:
                time.sleep(1)
                self.request.sendall(self.server.events.pop().to_bytes())
            msg = self.get_message()
            if msg is None:
                return
            resp = self.server.cmd_exec(msg)
            self.request.sendall(resp.to_bytes())


def parse_argv():
    parser = ArgumentParser(description='Mock QEMU Machine Protocol (QMP) server')
    parser.add_argument('--address', '-a', default='127.0.0.1',
                        help='IP address for QMP server to listen on')
    parser.add_argument('--port', '-p', default=45556, type=int,
                        help='Port number for QMP server to listen on')
    return parser.parse_args()


if __name__ == '__main__':
    argv = parse_argv()
    server = QmpMockServer(argv.address, argv.port, QmpHandler)
    server.run()
