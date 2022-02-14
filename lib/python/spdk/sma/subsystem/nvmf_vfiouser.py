import logging
import re
import grpc
import os
from socket import AddressFamily as af
from spdk.rpc.client import JSONRPCException
from google.protobuf import wrappers_pb2 as wrap
from .subsystem import Subsystem, SubsystemException
from ..qmp import QMPClient, QMPError
from ..proto import sma_pb2
from ..proto import nvme_pb2

log = logging.getLogger(__name__)


class NvmfVfioSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('vfiouser', 'nvme', client)

    def init(self, config):
        self._root_path = config.get('root_path', '/var/run/vfio-user/sma')
        self._bus = config.get('qmp_pci_bus')
        addr = config.get('qmp_address')
        if addr is None or self._bus is None:
            raise ValueError('Configuration error, qmp_address and qmp_pci_bus are mandatory.')
        port = config.get('qmp_port')
        if port is None:
            self._qmp_family = af.AF_UNIX
            self._qmp_addr = addr
        else:
            self._qmp_family = af.AF_INET
            self._qmp_addr = (addr, int(port))
        self._has_transport = self._create_transport()

    def _create_transport(self):
        try:
            with self._client() as client:
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == 'vfiouser':
                        return True
                return client.call('nvmf_create_transport',
                                   {'trtype': 'vfiouser'})
        except JSONRPCException:
            logging.error(f'Transport query NVMe/vfiouser failed')
            return False

    def _prefix_rem(self, id):
        '''
        Remove prefix from id passed in subsystem requests
        '''
        return id[id.startswith(f'{self.protocol}:') and len(f'{self.protocol}:'):]

    def _get_id_from_nqn(self, nqn):
        return re.sub('[^0-9a-zA-Z]+', 'a', nqn)

    def _get_path_from_id(self, id):
        return os.path.join(self._root_path, id)

    def _get_path_from_nqn(self, nqn):
        id = self._get_id_from_nqn(nqn)
        return self._get_path_from_id(id)

    def _get_nqn_from_params(self, bus, pfid, vfid):
        '''
        Wrap device params into NQN

        :param bus the QEMU bus ID to use
        :param pfid physical ID of device
        :param vfid virtual ID of device
        :return generated NQN as string
        '''
        return f'nqn.2016-06.io.spdk:{bus}:{pfid}:{vfid}'

    def _get_params_from_nqn(self, nqn: str):
        '''
        Unwrap given NQN back to device params

        :param nqn NQN str in format "nqn.2016-06.io.spdk:BUS:PF:VF"
        :return BUS, PF, VF
        :raise SubsystemException in case of invalid format
        '''
        params = nqn.split(':')
        if not params[0].lower() != 'nqn.2016-06.io.spdk' or len(params) < 4:
            raise SubsystemException(
                        grpc.StatusCode.INTERNAL,
                        'Invalid NQN passed in request.')
        return str(params[1]), int(params[2]), int(params[3])

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

    def _check_params(self, request, params):
        for param in params:
            if not request.HasField(param):
                raise SubsystemException(
                            grpc.StatusCode.INTERNAL,
                            'Could not find param')

    def _check_addr(self, addr, addrlist):
        return bool(list(filter(lambda a: (
            a['trtype'].lower() == 'vfiouser' and 
            a['traddr'] == addr['traddr']), addrlist)))

    def _get_subsystem_by_nqn(self, client, nqn):
        subsystems = client.call('nvmf_get_subsystems')
        for subsystem in subsystems:
            if subsystem['nqn'] == nqn:
                return subsystem
        return None

    def _check_create_subsystem(self, client, nqn):
        '''
        Creates NVMeoF subsystem with NQN if one doesn't already exists

        :param client is an object that can send SPDK's jsonrpc requests
        :param nqn is subsystem NQN we are probing for

        :return True if subsys was created
                False when subsys already exists
        '''
        if self._get_subsystem_by_nqn(client, nqn) is None:
            args = {'nqn': nqn, 'allow_any_host': True}
            client.call('nvmf_create_subsystem', args)
            return True
        return False

    def _check_listener(self, client, nqn, addr):
        subsystem = self._get_subsystem_by_nqn(client, nqn)
        if subsystem is None:
            raise SubsystemException(
                        grpc.StatusCode.INTERNAL,
                        f'Failed check for {self.name} listener')
        return self._check_addr(addr, subsystem['listen_addresses'])

    def _create_listener(self, client, nqn, addr):
        args = {'nqn': nqn, 'listen_address': addr}
        client.call('nvmf_subsystem_add_listener', args)
        return True

    def create_device(self, request):
        params = nvme_pb2.CreateDeviceParameters()
        if not request.params.Unpack(params):
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to unpack request')
        self._check_params(params, ['physical_id', 'virtual_id'])
        pfid = params.physical_id.value
        vfid = params.virtual_id.value
        nqn = self._get_nqn_from_params(self._bus, pfid, vfid)
        id = self._get_id_from_nqn(nqn)
        traddr = self._create_socket_path(id)
        addr = {'traddr': traddr, 'trtype': 'vfiouser'}
        try:
            with self._client() as client:
                sub_crt = self._check_create_subsystem(client, nqn)
                if not self._check_listener(client, nqn, addr):
                    ls_crt  = self._create_listener(client, nqn, addr)
            with QMPClient(self._qmp_addr, self._qmp_family) as qclient:
                if not qclient.device_list_properties(id):
                    qmp_params = {
                        'driver': 'vfio-user-pci',
                        'x-enable-migration': 'on',
                        'socket': os.path.join(traddr, 'cntrl'),
                        'bus': self._bus,
                        'id': id
                    }
                    qclient.device_add(qmp_params)
        except [QMPError, JSONRPCException] as e:
            logging.error(f'Exception occurred, trying to clean up. {e}')
            try:
                if sub_crt:
                    with self._client() as client:
                        logging.debug(f'Cleanup, removing subsys {repr(nqn)}')
                        client.call('nvmf_delete_subsystem', nqn)
                elif ls_crt:
                    with self._client() as client:
                        logging.debug(f'Cleanup, removing listener {addr}')
                        client.call('nvmf_subsystem_remove_listener', addr)
            except JSONRPCException:
                logging.error(f'Delete subsystem {nqn} failed. ' \
                              'Cleanup after exception failed')
            raise SubsystemException(
                grpc.StatusCode.INTERNAL,
                'Exception while trying to create VFIOUSER device') from e
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'{self.protocol}:{nqn}'))

    def remove_device(self, request):
        nqn = self._prefix_rem(request.id.value)
        id = self._get_id_from_nqn(nqn)
        try:
            with self._client() as client:
                if self._get_subsystem_by_nqn(client, nqn) is not None:
                    with QMPClient() as qclient:
                        if qclient.device_list_properties(id):
                            qclient.device_del(id)
                            client.call('nvmf_delete_subsystem', {'nqn': nqn})
                            self._remove_socket_path(id)
                        else:
                            logging.info(f'Tried removing non-existing QMP device: {id}')
                else:
                    logging.info(f'Tried removing non-existing device: {nqn}')
        except [QMPError, JSONRPCException] as e:
            raise SubsystemException(grpc.StatusCode.INTERNAL, f'Exception while deleting device {nqn}') from e
        return sma_pb2.RemoveDeviceResponse()

    def attach_volume(self, request):
        self._check_params(request, ['volume_guid', 'device_id'])
        nqn = self._prefix_rem(request.device_id.value)
        try:
            with self._client() as client:
                bdev = client.call('bdev_get_bdevs', {'name': request.volume_guid.value})[0]
                if bdev is None:
                    raise SubsystemException(grpc.StatusCode.NOT_FOUND, 'Invalid volume GUID')
                subsystem = self._get_subsystem_by_nqn(client, nqn)
                if subsystem is None:
                    raise SubsystemException(grpc.StatusCode.NOT_FOUND, 'Invalid device ID')
                if bdev['name'] not in [ns['name'] for ns in subsystem['namespaces']]:
                    params = {'nqn': nqn, 'namespace': {'bdev_name': bdev['name']}}
                    client.call('nvmf_subsystem_add_ns', params)
        except JSONRPCException as e:
            raise SubsystemException(grpc.StatusCode.INTERNAL, 'Failed to attach volume') from e

    def owns_device(self, id):
        return id.startswith(self.protocol)
