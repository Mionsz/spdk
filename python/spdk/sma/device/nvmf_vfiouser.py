import os
import grpc
import logging
from socket import AddressFamily
from spdk.rpc.client import JSONRPCException
from .device import DeviceManager, DeviceException
from google.protobuf import wrappers_pb2 as wrap
from ..qmp import QMPClient, QMPError
from ..proto import sma_pb2
from ..proto import nvme_pb2

log = logging.getLogger(__name__)


class NvmfVfioDeviceManager(DeviceManager):
    def __init__(self, client):
        super().__init__('vfiouser', 'nvme', client)

    def init(self, config):
        log.debug(f'Config: Initializing vfiouser with: "{config}"')
        self._hosts = {}
        self._root_path = config.get('root_path', '/tmp/vfio-user/sma')
        hosts = config.get('hosts')
        if hosts is None or not type(hosts) == list:
            hosts = []
        for pt in hosts:
            host_id = pt.get('id')
            bus_id = pt.get('bus')
            address = pt.get('address')
            port = pt.get('port')
            if host_id is None or bus_id is None or address is None:
                raise ValueError('Host config error, host_id, bus_id and address are mandatory')
            host = {'id': int(host_id), 'bus': str(bus_id)}
            if port is None:
                host['family'] = AddressFamily.AF_UNIX
                host['addr'] = address
            else:
                host['family'] = AddressFamily.AF_INET
                host['addr'] = (address, int(port))
            self._hosts[host['id']] = host
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

    def _get_id_from_params(self, hostid, pfid, vfid):
        '''
        Wrap device params into unique ID
        '''
        return f'{self.name}-{hostid}-{pfid}-{vfid}'

    def _get_nqn_from_params(self, hostid, pfid, vfid):
        '''
        Wrap device params into NQN using ID from _get_id_from_params
        '''
        return f'nqn.2016-06.io.spdk:{self._get_id_from_params(hostid, pfid, vfid)}'

    def _get_params_from_nqn(self, nqn: str):
        '''
        Unwrap given NQN back to device params and return (hostid, pfid, vfid)
        '''
        params = nqn.split(':')
        if not params[0].lower() == 'nqn.2016-06.io.spdk' or len(params) < 2:
            log.debug(f'Invalid NQN passed to _get_params_from_nqn() "{nqn}"')
            return None, None, None
        ids = params[1].split('-')
        if len(ids) != 4:
            log.debug(f'Invalid ID "{params[1]}" in NQN passed to _get_params_from_nqn() "{nqn}"')
            return None, None, None
        return int(ids[1]), int(ids[2]), int(ids[3])

    def _get_socket_path(self, hostid, pfid, vfid):
        return os.path.join(self._root_path, str(hostid), str(pfid), str(vfid))

    def _create_socket_path(self, hostid, pfid, vfid):
        socket_pth = self._get_socket_path(hostid, pfid, vfid)
        try:
            if not os.path.exists(socket_pth):
                os.makedirs(socket_pth)
            return socket_pth
        except OSError as e:
            raise DeviceException(grpc.StatusCode.INTERNAL, 'Path creation failed') from e

    def _remove_socket_path(self, hostid, pfid, vfid):
        socket_pth = self._get_socket_path(hostid, pfid, vfid)
        bar = os.path.join(socket_pth, 'bar0')
        cntrl = os.path.join(socket_pth, 'cntrl')
        try:
            if os.path.exists(bar):
                os.remove(bar)
            if os.path.exists(cntrl):
                os.remove(cntrl)
        except OSError as e:
            logging.warning(f'OSError while cleaning vfiouser sockets "{bar}" and "{cntrl}": {e}')

    def _check_params(self, request, params):
        for param in params:
            if not request.HasField(param):
                raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT,
                                      f'Missing required parameter: {param}')

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
        params = nvme_pb2.CreateDeviceParameters()
        if not request.params.Unpack(params):
            raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT, 'Failed to unpack request')
        self._check_params(params, ['host_id', 'physical_id', 'virtual_id'])
        host = self._hosts.get(params.host_id.value)
        if host is None:
            raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT, f'Invalid host identifier')
        pfid = params.physical_id.value
        vfid = params.virtual_id.value
        nqn = self._get_nqn_from_params(host['id'], pfid, vfid)
        id = self._get_id_from_params(host['id'], pfid, vfid)
        traddr = self._create_socket_path(host['id'], pfid, vfid)
        addr = {'traddr': traddr, 'trtype': 'vfiouser'}
        try:
            with self._client() as client:
                subsys_created = False
                subsys = self._get_subsystem_by_nqn(client, nqn)
                if subsys is None:
                    client.call('nvmf_create_subsystem', {'nqn': nqn, 'allow_any_host': True})
                    subsys = self._get_subsystem_by_nqn(client, nqn)
                    subsys_created = True
                if self._check_addr(addr, subsys['listen_addresses']):
                    client.call('nvmf_subsystem_add_listener', {'nqn': nqn, 'listen_address': addr})
            with QMPClient(host['addr'], host['family']) as qclient:
                if not qclient.device_list_properties(id):
                    qmp_params = {
                        'driver': 'vfio-user-pci',
                        'x-enable-migration': 'on',
                        'socket': os.path.join(traddr, 'cntrl'),
                        'bus': host['bus'],
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
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(value=f'{self.protocol}:{nqn}'))

    def delete_device(self, request):
        nqn = self._remove_prefix(request.id.value)
        hostid, pfid, vfid = self._get_params_from_nqn(nqn)
        if hostid is None or pfid is None or vfid is None:
            logging.info(f'Tried removing device with invlid NQN: {nqn}')
            return
        id = self._get_id_from_params(hostid, pfid, vfid)
        host = self._hosts.get(hostid)
        if host is None:
            logging.info(f'Tried removing from non-existing QMP host. ID "{hostid}", NQN "{nqn}"')
            return
        try:
            with self._client() as client:
                if self._get_subsystem_by_nqn(client, nqn) is not None:
                    with QMPClient(host['addr'], host['family']) as qclient:
                        if qclient.device_list_properties(id):
                            qclient.device_del(id)
                            client.call('nvmf_delete_subsystem', {'nqn': nqn})
                            self._remove_socket_path(hostid, pfid, vfid)
                        else:
                            logging.info(f'Tried removing non-existing QMP device: {id}')
                else:
                    logging.info(f'Tried removing non-existing device: {nqn}')
        except (QMPError, JSONRPCException) as e:
            raise DeviceException(grpc.StatusCode.INTERNAL, f'Failed deleting {nqn}') from e

    def attach_volume(self, request):
        self._check_params(request, ['volume_guid', 'device_id'])
        nqn = self._remove_prefix(request.device_id.value)
        volume = request.volume_guid.value
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
        self._check_params(request, ['volume_guid', 'device_id'])
        nqn = self._remove_prefix(request.device_id.value)
        volume = request.volume_guid.value
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
