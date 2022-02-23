from concurrent import futures
from contextlib import contextmanager
from multiprocessing import Lock
import grpc
import logging
from .device import DeviceException
from .proto import sma_pb2 as pb2
from .proto import sma_pb2_grpc as pb2_grpc


class StorageManagementAgent(pb2_grpc.StorageManagementAgentServicer):
    def __init__(self, addr, port, root_cert, priv_key, cert_chain):
        self._devices = {}
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        self._lock = Lock()
        if priv_key is not None and cert_chain is not None and root_cert is not None:
            with open(priv_key, 'rb') as f:
                private_key = f.read()
            with open(cert_chain, 'rb') as f:
                certificate_chain = f.read()
            with open(cert_chain, 'rb') as f:
                root_certificate = f.read()
            server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),),
                                                             root_certificate, require_client_auth=True)
            self._server.add_secure_port(f'{addr}:{port}', server_credentials)
        else:
            self._server.add_insecure_port(f'{addr}:{port}')
        pb2_grpc.add_StorageManagementAgentServicer_to_server(self, self._server)

    def _grpc_method(f):
        def wrapper(self, request, context):
            logging.debug(f'{f.__name__}\n{request}')
            return f(self, request, context)
        return wrapper

    def register_device(self, device_manager):
        self._devices[device_manager.protocol] = device_manager

    def start(self):
        self._server.start()

    def stop(self):
        self._server.stop(None)

    def _find_device_by_name(self, name):
        return self._devices.get(name)

    def _find_device_by_id(self, id):
        for device in self._devices.values():
            try:
                if device.owns_device(id):
                    return device
            except NotImplementedError:
                pass
        return None

    @_grpc_method
    def CreateDevice(self, request, context):
        response = pb2.CreateDeviceResponse()
        try:
            manager = self._find_device_by_name(request.WhichOneof('params'))
            if manager is None:
                raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT,
                                      'Unsupported device type')
            response = manager.create_device(request)
        except DeviceException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_grpc_method
    def DeleteDevice(self, request, context):
        response = pb2.DeleteDeviceResponse()
        try:
            device = self._find_device_by_id(request.id)
            if device is None:
                raise DeviceException(grpc.StatusCode.NOT_FOUND,
                                      'Invalid device ID')
            device.delete_device(request)
        except DeviceException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_grpc_method
    def AttachVolume(self, request, context):
        response = pb2.AttachVolumeResponse()
        try:
            device = self._find_device_by_id(request.device_id)
            if device is None:
                raise DeviceException(grpc.StatusCode.NOT_FOUND, 'Invalid device ID')
            device.attach_volume(request)
        except DeviceException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_grpc_method
    def DetachVolume(self, request, context):
        response = pb2.DetachVolumeResponse()
        try:
            device = self._find_device_by_id(request.device_id)
            if device is not None:
                device.detach_volume(request)
        except DeviceException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        return response

    @_grpc_method
    def ConnectVolume(self, request, context):
        response = pb2.ConnectVolumeResponse()
        try:
            if not request.HasField('type'):
                raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT,
                                      'Missing required field: type')
            subsys = self._find_subsystem(request.type.value)
            response = subsys.connect_volume(request)
        except UnsupportedDeviceException:
            context.set_details('Invalid controller type')
            context.set_code(grpc.StatusCode.INTERNAL)
        except DeviceException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_grpc_method
    def DisconnectVolume(self, request, context):
        response = pb2.DisconnectVolumeResponse()
        try:
            if not request.HasField('guid'):
                raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT,
                                      'Missing required field: id')
            for subsys in self._subsystems.values():
                try:
                    if subsys.disconnect_volume(request):
                        break
                except NotImplementedError:
                    pass
        except DeviceException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response
