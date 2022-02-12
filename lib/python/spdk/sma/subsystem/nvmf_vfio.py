import logging
import re
import os
import grpc
from spdk.rpc.client import JSONRPCException
from google.protobuf import wrappers_pb2 as wrap
from .subsystem import Subsystem, SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_vfio_pb2
from ..qmp import QMPClient, QMPError

log = logging.getLogger(__name__)


class NvmfVfioException(SubsystemException):
    def __init__(self, code, message, args=None):
        self.args = repr(args)
        super().__init__(code, message)


class NvmfVfioSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('nvmf_vfio', client)
        self._trtype = 'vfiouser'
        self._root_path = '/var/run/vfio-user/sma'
        self._controllers = {}
        self._has_transport = self._create_transport()

    def _get_name(self):
        return self.name
    
    def _get_trtype(self):
        return self._trtype

    def _prefix_add(self, nqn):
        return f'{self._get_name()}:{nqn}'

    def _prefix_rem(self, nqn):
        return nqn.removeprefix(f'{self._get_name()}:')

    def _get_id_from_nqn(self, nqn):
        return re.sub("[^0-9a-zA-Z]+", "0", nqn)

    def _get_path_from_id(self, id):
        return os.path.join(self._root_path, id)

    def _get_path_from_nqn(self, nqn):
        id = self._get_id_from_nqn(nqn)
        return self._get_path_from_id(id)

    def _create_socket_path(self, id):
        socket_pth = self._get_path_from_id(id)
        try:
            if not os.path.exists(socket_pth):
                os.makedirs(socket_pth)
            return socket_pth
        except OSError as e:
            raise NvmfVfioException(
                        grpc.StatusCode.INTERNAL,
                        'Path creation failed.', socket_pth) from e

    def _create_transport(self):
        try:
            with self._client() as client:
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == self._get_trtype():
                        return True
                # TODO: take the transport params from config
                return client.call('nvmf_create_transport',
                                   {'trtype': self._get_trtype()})
        except JSONRPCException:
            logging.error(f'Transport query NVMe/{self._get_trtype()} failed')
            return False

    def _unpack_request(self, request):
        params = nvmf_vfio_pb2.CreateDeviceParameters()
        if not request.params.Unpack(params):
                raise NvmfVfioException(
                            grpc.StatusCode.INTERNAL,
                            'Failed to unpack request', request)
        return params

    def _check_params(self, request, params):
        for param in params:
            if not request.HasField(param):
                raise NvmfVfioException(
                            grpc.StatusCode.INTERNAL,
                            'Could not find param', request)

    def _to_low_case_set(self, dict_in) -> set:
        '''
        Function for creating set from a dictionary with all value
        keys made a lower case string. Designed for address compaction

        :param dict_in is a dictionary to work on
        :return set of pairs with values converted to lower case string
        '''
        return {(K, str(V).lower()) for K, V in dict_in.items()}

    def _check_addr(self, addr, addr_list):
        '''
        Function for transport comparration without known variable set.
        Comparration is made based on inclusion of address set.
        Use with caution for small dictionaries (like 1-2 elements)

        :param dict_in is a dictionary to work on
        :return True is addr was found in addr_list
                False is addr is not a subset of addr_list
        '''
        low_case = self._to_low_case_set(addr)
        return bool(list(filter(lambda i: (low_case.issubset(
                                self._to_low_case_set(i))), addr_list)))

    def _get_subsystem_by_nqn(self, client, nqn):
        subsystems = client.call('nvmf_get_subsystems')
        for subsystem in subsystems:
            if subsystem['nqn'] == nqn:
                return subsystem
        return None

    def _check_create_subsystem(self, client, nqn):
        '''
        NVMe-oF create NQN subsystem is one does not exists

        :param client is a JSONRPCClient socket
        :param nqn is subsystem NQN we are probing for

        :raise NvmfVfioException if result is unexpected
        :raise JSONRPCException for socket related errors
        :return True if subsys was created
                False when subsys already exists
        '''
        if self._get_subsystem_by_nqn(client, nqn) is None:
            args = {'nqn': nqn, 'allow_any_host': True}
            result = client.call('nvmf_create_subsystem', args)
            if not result:
                raise NvmfVfioException(
                            grpc.StatusCode.INTERNAL,
                            'Failed to create subsystem', args)
            return True
        return False

    def _check_listener(self, client, nqn, addr):
        subsystem = self._get_subsystem_by_nqn(client, nqn)
        if subsystem is None:
            raise NvmfVfioException(
                        grpc.StatusCode.INTERNAL,
                        f'Failed check for {self.getName()} listener', addr)
        return self._check_addr(addr, subsystem['listen_addresses'])

    def _create_listener(self, client, nqn, addr, clean_on_fail=False):
        args = {'nqn': nqn, 'listen_address': addr}
        result = client.call('nvmf_subsystem_add_listener', args)
        if not result:
            if clean_on_fail:
                client.call('nvmf_delete_subsystem', nqn)
            raise NvmfVfioException(
                    grpc.StatusCode.INTERNAL,
                    "Failed to create listener", args)

    def create_device(self, request):
        params = self._unpack_request(request)
        self._check_params(params, ['trbus', 'qtraddr', 'qtrsvcid'])
        nqn = params.subnqn.value
        id = self._get_id_from_nqn(nqn)
        traddr = self._create_socket_path(id)
        addr = { 'traddr': traddr,
                 'trtype': self._get_trtype() }

        trbus = params.trbus.value
        qaddress = (params.qtraddr.value, int(params.qtrsvcid.value))
        try:
            with self._client() as client:
                subsys_created = self._check_create_subsystem(client, nqn)
                if not self._check_listener(client, nqn, addr):
                    self._create_listener(client, nqn, addr, subsys_created)
            with QMPClient(qaddress) as qclient:
                if not qclient.exec_device_list_properties(id):
                    qclient.exec_device_add(addr['traddr'], trbus, id)
        except JSONRPCException as e:
            raise NvmfVfioException(
                grpc.StatusCode.INTERNAL,
                "JSONRPCException failed to create device", params) from e
        except QMPError as e:
            # TODO: subsys and listener cleanup
            raise NvmfVfioException(
                grpc.StatusCode.INTERNAL,
                "QMPClient failed to create device", params) from e
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=self._prefix_add(nqn)))

    def remove_device(self, request):
        raise NotImplementedError()

    def connect_volume(self, request):
        raise NotImplementedError()

    def disconnect_volume(self, request):
        raise NotImplementedError()

    def attach_volume(self, request):
        raise NotImplementedError()

    def detach_volume(self, request):
        raise NotImplementedError()

    def owns_device(self, id):
        return id.startswith(self._get_name())

    def owns_controller(self, id):
        raise NotImplementedError()
