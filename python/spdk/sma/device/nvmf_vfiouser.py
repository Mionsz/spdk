import os
import grpc
import logging
from socket import AddressFamily
from spdk.rpc.client import JSONRPCException
from .device import DeviceManager, DeviceException
from google.protobuf import wrappers_pb2 as wrap
from ..qmp import QMPClient, QMPError
from ..proto import sma_pb2

log = logging.getLogger(__name__)


class NvmfVfioDeviceManager(DeviceManager):
    def __init__(self, client):
        super().__init__('vfiouser', 'nvme', client)

    def init(self, config):
        log.debug(f'Config: Initializing vfiouser with: "{config}"')
        if config.get('bus') is None or config.get('address') is None:
            self._host = None
        else:
            self._host = {'root_path': config.get('root_path', '/tmp/sma/vfiouser'),
                          'bus': config.get('bus')}
            if config.get('port') is None:
                self._host['family'] = AddressFamily.AF_UNIX
                self._host['addr'] = config['address']
            else:
                self._host['family'] = AddressFamily.AF_INET
                self._host['addr'] = (config['address'], int(config['port']))
        self._has_transport = self._create_transport()

    def _create_transport(self):
        try:
            with self._client() as client:
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == 'vfiouser':
                        return True
                return client.call('nvmf_create_transport', {'trtype': 'vfiouser'})
        except JSONRPCException:
            logging.error(f'Transport query NVMe/vfiouser failed')
            return False

    def _remove_prefix(self, id):
        '''
        Remove prefix from id passed in subsystem requests
        '''
        return id[len(f'{self.protocol}:'):]

    def _get_id_from_params(self, pfid, vfid):
        '''
        Wrap device params into unique ID
        '''
        return f'{self.name}-{pfid}-{vfid}'

    def _get_nqn_from_params(self, pfid, vfid):
        '''
        Wrap device params into NQN using ID from _get_id_from_params
        '''
        return f'nqn.2016-06.io.spdk:{self._get_id_from_params(pfid, vfid)}'

    def _get_params_from_nqn(self, nqn: str):
        '''
        Unwrap given NQN back to device params and return (pfid, vfid)
        '''
        params = nqn.split(':')
        if not params[0].lower() == 'nqn.2016-06.io.spdk' or len(params) < 2:
            log.debug(f'Invalid NQN passed to _get_params_from_nqn() "{nqn}"')
            return None, None
        ids = params[1].split('-')
        if len(ids) != 3 or not ids[0] == self.name:
            log.debug(f'Invalid ID "{params[1]}" in NQN passed to _get_params_from_nqn() "{nqn}"')
            return None, None
        return int(ids[1]), int(ids[2])

    def _get_socket_path(self, pfid, vfid):
        return os.path.join(self._host['root_path'], str(pfid), str(vfid))

    def _create_socket_path(self, pfid, vfid):
        socket_pth = self._get_socket_path(pfid, vfid)
        try:
            if not os.path.exists(socket_pth):
                os.makedirs(socket_pth)
            return socket_pth
        except OSError as e:
            raise DeviceException(grpc.StatusCode.INTERNAL, 'Path creation failed') from e

    def _remove_socket_path(self, pfid, vfid):
        socket_pth = self._get_socket_path(pfid, vfid)
        bar = os.path.join(socket_pth, 'bar0')
        cntrl = os.path.join(socket_pth, 'cntrl')
        try:
            if os.path.exists(bar):
                os.remove(bar)
            if os.path.exists(cntrl):
                os.remove(cntrl)
        except OSError as e:
            logging.warning(f'OSError while cleaning vfiouser sockets "{bar}" and "{cntrl}": {e}')

    def _check_addr(self, addr, addrlist):
        return bool(list(filter(lambda a: (
            a['trtype'].lower() == 'vfiouser' and
            a['traddr'] == addr['traddr']), addrlist)))

    def _get_subsystem_by_nqn(self, client, nqn):
        try:
            return client.call('nvmf_get_subsystems', {'nqn': nqn})[0]
        except JSONRPCException:
            return None

    def _get_bdev_by_guid(self, client, guid):
        try:
            return client.call('bdev_get_bdevs', {'name': guid})[0]
        except JSONRPCException:
            return None

    def create_device(self, request):
        pfid = request.nvme.physical_id
        vfid = request.nvme.virtual_id
        if self._host is None:
            raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT, 'No host specified in config')
        nqn = self._get_nqn_from_params(pfid, vfid)
        id = self._get_id_from_params(pfid, vfid)
        traddr = self._create_socket_path(pfid, vfid)
        addr = {'traddr': traddr, 'trtype': 'vfiouser'}
        try:
            with self._client() as client:
                subsys_created = False
                subsys = self._get_subsystem_by_nqn(client, nqn)
                if subsys is None:
                    client.call('nvmf_create_subsystem', {'nqn': nqn, 'allow_any_host': True})
                    subsys = self._get_subsystem_by_nqn(client, nqn)
                    subsys_created = True
                if not self._check_addr(addr, subsys['listen_addresses']):
                    client.call('nvmf_subsystem_add_listener', {'nqn': nqn, 'listen_address': addr})
            with QMPClient(self._host['addr'], self._host['family']) as qclient:
                if id not in str(qclient.query_pci()):
                    qmp_params = {
                        'driver': 'vfio-user-pci',
                        'x-enable-migration': 'on',
                        'socket': os.path.join(traddr, 'cntrl'),
                        'bus': self._host['bus'],
                        'id': id
                    }
                    qclient.device_add(qmp_params)
        except (QMPError, JSONRPCException) as e:
            logging.error(f'Exception occurred, trying to clean up. {e}')
            try:
                if subsys_created:
                    with self._client() as client:
                        logging.debug(f'Cleanup, removing subsys {repr(nqn)}')
                        client.call('nvmf_delete_subsystem', {'nqn': nqn})
            except JSONRPCException:
                logging.error(f'Delete subsystem {nqn} failed. Cleanup after exception failed')
            raise DeviceException(grpc.StatusCode.INTERNAL,
                                  'Exception while trying to create VFIOUSER device') from e
        return sma_pb2.CreateDeviceResponse(id=f'{self.protocol}:{nqn}')

    def delete_device(self, request):
        nqn = self._remove_prefix(request.id)
        if self._host is None:
            logging.info(f'Tried removing NQN "{nqn}" from not defined QMP host')
            return
        pfid, vfid = self._get_params_from_nqn(nqn)
        if pfid is None or vfid is None:
            logging.info(f'Tried removing device with invlid NQN: {nqn}')
            return
        id = self._get_id_from_params(pfid, vfid)
        try:
            with self._client() as client:
                if self._get_subsystem_by_nqn(client, nqn) is not None:
                    with QMPClient(self._host['addr'], self._host['family']) as qclient:
                        if id in str(qclient.query_pci()):
                            qclient.device_del(id)
                            client.call('nvmf_delete_subsystem', {'nqn': nqn})
                            self._remove_socket_path(pfid, vfid)
                        else:
                            logging.info(f'Tried removing non-existing QMP device: {id}')
                else:
                    logging.info(f'Tried removing non-existing device: {nqn}')
        except (QMPError, JSONRPCException) as e:
            raise DeviceException(grpc.StatusCode.INTERNAL, f'Failed deleting {nqn}') from e

    def attach_volume(self, request):
        nqn = self._remove_prefix(request.device_id)
        volume = request.volume_guid
        try:
            with self._client() as client:
                bdev = self._get_bdev_by_guid(client, volume)
                if bdev is None:
                    raise DeviceException(grpc.StatusCode.NOT_FOUND,
                                          f'Invalid volume GUID "{volume}"')
                subsystem = self._get_subsystem_by_nqn(client, nqn)
                if subsystem is None:
                    raise DeviceException(grpc.StatusCode.NOT_FOUND, f'Invalid device ID "{nqn}"')
                if bdev['name'] not in [ns['name'] for ns in subsystem['namespaces']]:
                    params = {'nqn': nqn, 'namespace': {'bdev_name': bdev['name']}}
                    client.call('nvmf_subsystem_add_ns', params)
        except JSONRPCException as e:
            raise DeviceException(grpc.StatusCode.INTERNAL, 'Failed to attach volume') from e

    def detach_volume(self, request):
        nqn = self._remove_prefix(request.device_id)
        volume = request.volume_guid
        try:
            with self._client() as client:
                bdev = self._get_bdev_by_guid(client, volume)
                if bdev is None:
                    logging.info(f'Tried detaching non-existing volume "{volume}", NQN "{nqn}"')
                    return
                subsystem = self._get_subsystem_by_nqn(client, nqn)
                if subsystem is None:
                    logging.info(f'Tried detaching "{volume}" from non-existing NQN "{nqn}"')
                    return
                for ns in subsystem['namespaces']:
                    if ns['name'] == bdev['name']:
                        client.call('nvmf_subsystem_remove_ns', {'nqn': nqn, 'nsid': ns['nsid']})
                        return
        except JSONRPCException as e:
            raise DeviceException(grpc.StatusCode.INTERNAL, 'Failed to detach volume') from e

    def owns_device(self, id):
        return id.startswith(self.protocol)
