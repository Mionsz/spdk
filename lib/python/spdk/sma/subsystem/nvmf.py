from ast import Set
import enum
import grpc
import logging
from spdk.rpc.client import JSONRPCException
from .subsystem import Subsystem, SubsystemException
from ..proto import nvmf_tcp_pb2
from ..proto import nvmf_vfio_pb2

log = logging.getLogger(__name__)


class NvmfTr(enum.Enum):
    TCP_IP4 = ('nvmf_tcp', 'tcp', 'ipv4', 'NVMe/TCP IPv4')
    TCP_IP6 = ('nvmf_tcp', 'tcp', 'ipv6', 'NVMe/TCP IPv6')
    VFIOUSER = ('nvmf_vfio', 'vfiouser', 'ipv4', 'NVMe/VFIOUSER PCI')

    def get_proto_class(self):
        return eval(f'{self.get_prefix()}_pb2')

    def get_desc(self):
        return self.value[3]

    def get_prefix(self):
        return self.value[0]

    def get_trtype(self):
        return self.value[1]

    def get_adrfam(self):
        return self.value[2]

    def prefix_add(self, nqn):
        return nqn.removeprefix(f'{self.get_prefix()}:')

    def prefix_rem(self, nqn):
        return f'{self.get_prefix()}:{nqn}'

    def check_prefix(self, nqn):
        return nqn.startswith(self.get_prefix())


class NvmeErr(enum.Enum):
    PARAMS_MISSING = (grpc.StatusCode.INVALID_ARGUMENT, 'Missing required field')
    PARAMS_INVALID = (grpc.StatusCode.INVALID_ARGUMENT, 'Failed to parse/unpack parameters')
    BDEV_NOT_FOUND = (grpc.StatusCode.NOT_FOUND, 'BDEV couldn\'t be found. Invalid volume GUID')
    BDEV_CONNECT_CTRL = (grpc.StatusCode.INTERNAL, 'Failed to connect the BDEV controller')
    BDEV_DISCONN_CTRL = (grpc.StatusCode.INTERNAL, 'Failed to disconnect the BDEV controller')
    DEVICE_CREATE = (grpc.StatusCode.INTERNAL, 'Failed to create the device')
    DEVICE_REMOVE = (grpc.StatusCode.INTERNAL, 'Failed to remove the device')
    DEVICE_ARGUMENT = (grpc.StatusCode.INVALID_ARGUMENT, 'Failed to parse device parameters')
    VOLUME_CONNECT = (grpc.StatusCode.INTERNAL, 'Failed to connect the volume')
    VOLUME_DISCONNECT = (grpc.StatusCode.INTERNAL, 'Failed to disconnect the volume')
    VOLUME_NOT_FOUND = (grpc.StatusCode.INVALID_ARGUMENT, 'Volume couldn\'t be found')
    VOLUME_ATTACH = (grpc.StatusCode.INTERNAL, 'Failed to attach the volume')
    VOLUME_DETACH = (grpc.StatusCode.INTERNAL, 'Failed to detach the volume')
    SUBSYS_CREATE = (grpc.StatusCode.INTERNAL, 'Failed to create subsystem')
    SUBSYS_NOT_FOUND = (grpc.StatusCode.NOT_FOUND, 'Subsystem couldn\'t be found. Invalid device ID')
    SUBSYS_ADD_LISTENER = (grpc.StatusCode.INTERNAL, 'Failed to add subsystem listener')
    TRANSPORT_UNAV = (grpc.StatusCode.INTERNAL, 'Specified transport is unavailable')

    def get_code(self):
        return self.value[0].value

    def get_desc(self):
        return f'{self}{self.get_code()}'

    def get_full_desc(self, nvme_tr):
        return f'{self.get_desc()} for {nvme_tr[3]}: {self.value[1]}!'


# Example exception print:
# NvmeErr.DEVICE_CREATE(13, internal): NVMe/TCP IPv4. Failed to create the device! ARGS: Something more to print
class NvmfException(SubsystemException):
    def __init__(self, nvme_err: NvmeErr, nvme_tr, *args):
        super().__init__(nvme_err.value[0], f'{nvme_err.get_full_desc(nvme_tr)} ARGS: {args}')


class Nvmf(Subsystem):
    def __init__(self, client, transport: NvmfTr):
        super().__init__(transport.get_prefix(), client)
        self._nvme_tr = transport
        self._subsys_proto = transport.get_proto_class()
        self._has_transport = False
        self._controllers = {}
        self.__check_transport()

    def get_trtype(self):
        return self._nvme_tr.get_trtype()

    def _client_safe(self, nvme_err: NvmeErr):
        try:
            with self._client() as client:
                yield client

        except JSONRPCException as ex:
            raise NvmfException(nvme_err, self._nvme_tr) from ex

    def __check_transport(self):
        for client in self._client_safe(NvmeErr.TRANSPORT_UNAV):
            if self._has_transport:
                return True
            transports = client.call('nvmf_get_transports')
            for transport in transports:
                if transport['trtype'].lower() == self.get_trtype():
                    self._has_transport = True
                    break
            else:
                self._has_transport = client.call('nvmf_create_transport',
                                                  {'trtype': self.get_trtype()})
        return self._has_transport

    def _unpack_request(self, request):
        params = self._subsys_proto.CreateDeviceParameters()
        if not request.params.Unpack(params):
            raise NvmfException(NvmeErr.PARAMS_INVALID, self._nvme_tr)
        return params

    def _to_low_case_set(self, dict_in) -> set:
        return {(K, str(V).lower()) for K, V in dict_in.items()}

    def _check_addr(self, addr, addr_list):
        low_case = self._to_low_case_set(addr)
        return bool(list(filter(lambda i: (low_case.issubset(
                                self._to_low_case_set(i))), addr_list)))

    def _get_params(self, request, params):
        result = {}
        for grpc_param, *rpc_param in params:
            if request.HasField(grpc_param):
                rpc_param = rpc_param[0] if rpc_param else grpc_param
                result[rpc_param] = getattr(request, grpc_param).value
        return result

    def _check_params(self, request, params):
        for param in params:
            if not request.HasField(param):
                raise NvmfException(NvmeErr.PARAMS_MISSING, self._nvme_tr, param)

    def _get_subsystem_by_nqn(self, client, nqn):
        subsystems = client.call('nvmf_get_subsystems')
        for subsystem in subsystems:
            if subsystem['nqn'] == nqn:
                return subsystem
        return None

    def _check_create_subsystem(self, client, nqn):
        subsystem = self._get_subsystem_by_nqn(client, nqn)
        if subsystem is None:
            args = {'nqn': nqn, 'allow_any_host': True}
            result = client.call('nvmf_create_subsystem', args)
            if not result:
                raise NvmfException(NvmeErr.SUBSYS_CREATE, self._nvme_tr, args)
            return True
        return False

    def _check_listener(self, client, nqn, addr):
        subsystem = self._get_subsystem_by_nqn(client, nqn)
        if subsystem is None:
            raise NvmfException(NvmeErr.SUBSYS_NOT_FOUND, self._nvme_tr, nqn)
        return self._check_addr(addr, subsystem['listen_addresses'])

    def _create_listener(self, client, nqn, addr, clean_on_fail=False):
        args = {'nqn': nqn, 'listen_address': addr}
        result = client.call('nvmf_subsystem_add_listener', args)
        if not result:
            if clean_on_fail:
                client.call('nvmf_delete_subsystem', nqn)
            raise NvmfException(NvmeErr.SUBSYS_ADD_LISTENER, self._nvme_tr, args)
