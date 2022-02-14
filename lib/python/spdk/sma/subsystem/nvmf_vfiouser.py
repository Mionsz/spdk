import logging
import re
from time import sleep
import grpc
import os
from spdk.rpc.client import JSONRPCException
from google.protobuf import wrappers_pb2 as wrap
from .subsystem import Subsystem, SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_vfio_pb2
from ..qmp import QMPClient, QMPError

log = logging.getLogger(__name__)


class NvmfVfioSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('nvmf_vfio', client)
        self._trtype = 'vfiouser'
        self._root_path = '/var/run/vfio-user/sma'
        self._has_transport = self._create_transport()

    def _create_transport(self):
        try:
            with self._client() as client:
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == self._trtype:
                        return True
                # TODO: take the transport params from config
                return client.call('nvmf_create_transport',
                                   {'trtype': self._trtype})
        except JSONRPCException:
            logging.error(f'Transport query NVMe/{self._trtype} failed')
            return False

    def _prefix_add(self, nqn):
        return f'{self._get_name()}:{nqn}'

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
            raise SubsystemException(
                        grpc.StatusCode.INTERNAL,
                        'Path creation failed.') from e

    def _remove_socket_path(self, id):
        socket_pth = self._get_path_from_id(id)
        bar = os.path.join(socket_pth, 'bar0')
        cntrl = os.path.join(socket_pth, 'cntrl')
        try:
            if os.path.exists(bar):
                os.remove(bar)
            if os.path.exists(cntrl):
                os.remove(cntrl)
        except OSError as e:
            raise SubsystemException(
                        grpc.StatusCode.INTERNAL,
                        'Path deletion failed.') from e

    def _unpack_request(self, request):
        params = nvmf_vfio_pb2.CreateDeviceParameters()
        if not request.params.Unpack(params):
                raise SubsystemException(
                            grpc.StatusCode.INTERNAL,
                            'Failed to unpack request')
        return params

    def _check_params(self, request, params):
        for param in params:
            if not request.HasField(param):
                raise SubsystemException(
                            grpc.StatusCode.INTERNAL,
                            'Could not find param')

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

    def _get_bdev_by_uuid(self, client, uuid):
        bdevs = client.call('bdev_get_bdevs')
        for bdev in bdevs:
            if bdev['uuid'] == uuid:
                return bdev
        return None

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
                raise SubsystemException(
                            grpc.StatusCode.INTERNAL,
                            'Failed to create subsystem')
            return True
        return False

    def _check_listener(self, client, nqn, addr):
        subsystem = self._get_subsystem_by_nqn(client, nqn)
        if subsystem is None:
            raise SubsystemException(
                        grpc.StatusCode.INTERNAL,
                        f'Failed check for {self.name} listener')
        return self._check_addr(addr, subsystem['listen_addresses'])

    def _create_listener(self, client, nqn, addr, clean_on_fail=False):
        args = {'nqn': nqn, 'listen_address': addr}
        result = client.call('nvmf_subsystem_add_listener', args)
        if not result:
            if clean_on_fail:
                client.call('nvmf_delete_subsystem', nqn)
            raise SubsystemException(
                    grpc.StatusCode.INTERNAL,
                    "Failed to create listener")

    def create_device(self, request):
        params = self._unpack_request(request)
        self._check_params(params, ['trbus', 'qtraddr', 'qtrsvcid'])
        nqn = params.subnqn.value
        id = self._get_id_from_nqn(nqn)
        traddr = self._create_socket_path(id)
        addr = { 'traddr': traddr,
                 'trtype': self._trtype }

        trbus = params.trbus.value
        qaddress = (params.qtraddr.value, int(params.qtrsvcid.value))
        try:
            with self._client() as client:
                subsys_created = self._check_create_subsystem(client, nqn)
                if not self._check_listener(client, nqn, addr):
                    self._create_listener(client, nqn, addr, subsys_created)
            # TODO: after couple of add/delete QEMU reports memory leak and crush
            with QMPClient(qaddress) as qclient:
                if not qclient.exec_device_list_properties(id):
                    qclient.exec_device_add(addr['traddr'], trbus, id)
        except JSONRPCException as e:
            raise SubsystemException(
                grpc.StatusCode.INTERNAL,
                "JSONRPCException failed to create device") from e
        except QMPError as e:
            # TODO: subsys and listener cleanup
            raise SubsystemException(
                grpc.StatusCode.INTERNAL,
                "QMPClient failed to create device") from e
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=self._prefix_add(nqn)))

    def remove_device(self, request):
        try:
            with self._client() as client:
                nqn = self._prefix_rem(request.id.value)
                id = self._get_id_from_nqn(nqn)
                if self._get_subsystem_by_nqn(client, nqn) is not None:
                    with QMPClient() as qclient:
                        if qclient.exec_device_list_properties(id):
                            qclient.exec_device_del(id)
                            # TODO: add wait for event device deleted instead sleep
                            sleep(1)
                    if not client.call('nvmf_delete_subsystem', {'nqn': nqn}):
                        raise SubsystemException(
                            grpc.StatusCode.INTERNAL,
                            "Failed to remove device")
                    self._remove_socket_path(id)
                else:
                    logging.info(f'Tried to remove a non-existing device: {nqn}')
        except JSONRPCException as e:
            raise SubsystemException(
                grpc.StatusCode.INTERNAL,
                "JSONRPCException failed to delete device") from e
        except QMPError as e:
            raise SubsystemException(
                grpc.StatusCode.INTERNAL,
                "QMPClient failed to delete device") from e

    def attach_volume(self, request):
        self._check_params(request, ['volume_guid', 'device_id'])
        nqn = self._prefix_rem(request.device_id.value)
        try:
            with self._client() as client:
                bdev = self._get_bdev_by_uuid(request.volume_guid.value)
                if bdev is None:
                    raise SubsystemException(grpc.StatusCode.NOT_FOUND,
                                            'Invalid volume GUID')
                subsystem = self._get_subsystem_by_nqn(nqn)
                if subsystem is None:
                    raise SubsystemException(grpc.StatusCode.NOT_FOUND,
                                            'Invalid device ID')
                if bdev['name'] not in [ns['name'] for ns in subsystem['namespaces']]:
                    params = {'nqn': nqn, 'namespace': {'bdev_name': bdev['name']}}
                    result = client.call('nvmf_subsystem_add_ns', params)
                    if not result:
                        raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                'Failed to attach volume')
        except JSONRPCException as e:
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                    'Failed to attach volume') from e

    def owns_device(self, id):
        return id.startswith(self.name)
