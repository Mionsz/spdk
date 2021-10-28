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
    def __init__(self, addr, port):
        self._subsystems = {}
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        self._lock = Lock()
        self._server.add_insecure_port(f'{addr}:{port}')
        pb2_grpc.add_StorageManagementAgentServicer_to_server(self, self._server)

    def _grpc_method(f):
        def wrapper(self, request, context):
            logging.debug(f'{f.__name__}\n{request}')
            # For now, synchronize all gRPC methods
            self._lock.acquire()
            try:
                return f(self, request, context)
            finally:
                self._lock.release()
        return wrapper

    def register_subsystem(self, subsystem):
        self._subsystems[subsystem.name] = subsystem

    def run(self):
        self._server.start()
        self._server.wait_for_termination()

    def _find_subsystem(self, name):
        subsys = self._subsystems.get(name)
        if subsys is None:
            raise UnsupportedSubsystemException()
        return subsys

    @_grpc_method
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
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        except subsystem.SubsystemException as ex:
            context.set_details(ex.message)
            context.set_code(ex.code)
        except NotImplementedError:
            context.set_details('Method is not implemented by selected device type')
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        return response

    @_grpc_method
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