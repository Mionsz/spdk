from concurrent import futures
from contextlib import contextmanager
from multiprocessing import Lock
import grpc
import logging
from .subsystem import subsystem
from .proto import sma_pb2 as pb2
from .proto import sma_pb2_grpc as pb2_grpc


class UnsupportedSubsystemException(Exception):
    pass


class StorageManagementAgent(pb2_grpc.StorageManagementAgentServicer):
    _lock = Lock()

    def __init__(self, client, addr, port):
        self._subsystems = {}
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        self._client = lambda: self._get_client(client)
        self._server.add_insecure_port(f'{addr}:{port}')
        pb2_grpc.add_StorageManagementAgentServicer_to_server(self, self._server)

    @contextmanager
    def _get_client(self, client):
        # For now, use this to synchronize multiple RPC calls via the with client block
        self._lock.acquire()
        try:
            yield client()
        finally:
            self._lock.release()

    def _log_method(f):
        def wrapper(self, request, context):
            logging.debug(f'{f.__name__}\n{request}')
            return f(self, request, context)
        return wrapper

    def register_subsystem(self, subsys_cls):
        subsys = subsys_cls(self._client)
        self._subsystems[subsys.name] = subsys

    def run(self):
        self._server.start()
        self._server.wait_for_termination()

    def _find_subsystem(self, name):
        subsys = self._subsystems.get(name)
        if subsys is None:
            raise UnsupportedSubsystemException()
        return subsys

    @_log_method
    def CreateDevice(self, request, context):
        response = pb2.CreateDeviceResponse()
        try:
            if not request.HasField('type'):
                raise subsystem.SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                                   'Missing required field: type')
            subsys = self._find_subsystem(request.type.value)
            response = subsys.create_device(request)
        except UnsupportedSubsystemException:
            context.set_details('Invalid device type')
            context.set_code(grpc.StatusCode.INTERNAL)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_log_method
    def RemoveDevice(self, request, context):
        response = pb2.RemoveDeviceResponse()
        try:
            if not request.HasField('id'):
                raise subsystem.SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                                   'Missing required field: id')
            for subsys in self._subsystems.values():
                try:
                    if subsys.owns_device(request.id.value):
                        break
                except NotImplementedError:
                    pass
            else:
                raise subsystem.SubsystemException(grpc.StatusCode.NOT_FOUND,
                                                   'Invalid device ID')
            subsys.remove_device(request)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_log_method
    def ConnectController(self, request, context):
        response = pb2.ConnectControllerResponse()
        try:
            if not request.HasField('type'):
                raise subsystem.SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                                   'Missing required field: type')
            subsys = self._find_subsystem(request.type.value)
            response = subsys.connect_controller(request)
        except UnsupportedSubsystemException:
            context.set_details('Invalid controller type')
            context.set_code(grpc.StatusCode.INTERNAL)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_log_method
    def DisconnectController(self, request, context):
        response = pb2.DisconnectControllerResponse()
        try:
            if not request.HasField('id'):
                raise subsystem.SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                                   'Missing required field: id')
            for subsys in self._subsystems.values():
                try:
                    if subsys.owns_controller(request.id.value):
                        break
                except NotImplementedError:
                    pass
            else:
                raise subsystem.SubsystemException(grpc.StatusCode.NOT_FOUND,
                                                   'Invalid controller ID')
            subsys.disconnect_controller(request)
        except UnsupportedSubsystemException:
            context.set_details('Invalid controller type')
            context.set_code(grpc.StatusCode.INTERNAL)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_log_method
    def AttachVolume(self, request, context):
        response = pb2.AttachVolumeResponse()
        try:
            if not request.HasField('device_id'):
                raise subsystem.SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                                   'Missing required field: device_id')
            for subsys in self._subsystems.values():
                try:
                    if subsys.owns_device(request.device_id.value):
                        break
                except NotImplementedError:
                    pass
            else:
                raise subsystem.SubsystemException(grpc.StatusCode.NOT_FOUND,
                                                   'Invalid device ID')
            subsys.attach_volume(request)
        except UnsupportedSubsystemException:
            context.set_details('Invalid controller type')
            context.set_code(grpc.StatusCode.INTERNAL)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_log_method
    def DetachVolume(self, request, context):
        response = pb2.DetachVolumeResponse()
        try:
            for subsys in self._subsystems.values():
                try:
                    subsys.detach_volume(request)
                except NotImplementedError:
                    pass
        except UnsupportedSubsystemException:
            context.set_details('Invalid controller type')
            context.set_code(grpc.StatusCode.INTERNAL)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        return response
