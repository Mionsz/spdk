import grpc
import logging
import uuid
from spdk.rpc.client import JSONRPCException
from .device import DeviceManager, DeviceException
from ..proto import sma_pb2
from ..proto import nvmf_tcp_pb2


class NvmfTcpDeviceManager(DeviceManager):
    def __init__(self, client):
        super().__init__('nvmf_tcp', 'nvmf_tcp', client)

    def init(self, config):
        self._has_transport = self._create_transport()
        self._controllers = {}

    def _create_transport(self):
        try:
            with self._client() as client:
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == 'tcp':
                        return True
                # TODO: take the transport params from config
                return client.call('nvmf_create_transport',
                                   {'trtype': 'tcp'})
        except JSONRPCException:
            logging.error('Failed to query for NVMe/TCP transport')
            return False

    def _check_transport(f):
        def wrapper(self, *args):
            if not self._has_transport:
                raise DeviceException(grpc.StatusCode.INTERNAL,
                                      'NVMe/TCP transport is unavailable')
            return f(self, *args)
        return wrapper

    def _get_params(self, request, params):
        result = {}
        for grpc_param, *rpc_param in params:
            rpc_param = rpc_param[0] if rpc_param else grpc_param
            result[rpc_param] = getattr(request, grpc_param)
        return result

    def _check_addr(self, addr, addrlist):
        return next(filter(lambda a: (
            a['trtype'].lower() == 'tcp' and
            a['adrfam'].lower() == addr['adrfam'].lower() and
            a['traddr'].lower() == addr['traddr'].lower() and
            a['trsvcid'].lower() == addr['trsvcid'].lower() and
            a.get('subnqn') == addr.get('subnqn')), addrlist), None) is not None

    def _add_volume(self, ctrlr_name, volume_guid):
        volumes = self._controllers.get(ctrlr_name, [])
        if volume_guid in volumes:
            return
        self._controllers[ctrlr_name] = volumes + [volume_guid]

    def _remove_volume(self, volume_guid):
        for ctrlr, volumes in self._controllers.items():
            if volume_guid in volumes:
                volumes.remove(volume_guid)
                if len(volumes) == 0:
                    self._controllers.pop(ctrlr)
                return len(volumes) == 0, ctrlr
        return False, None

    @_check_transport
    def create_device(self, request):
        params = request.nvmf_tcp
        with self._client() as client:
            try:
                subsystems = client.call('nvmf_get_subsystems')
                for subsystem in subsystems:
                    if subsystem['nqn'] == params.subnqn:
                        break
                else:
                    subsystem = None
                    result = client.call('nvmf_create_subsystem',
                                         {**self._get_params(params, [
                                                ('subnqn', 'nqn')])})
            except JSONRPCException:
                raise DeviceException(grpc.StatusCode.INTERNAL,
                                      'Failed to create NVMe/TCP device')
            try:
                for host in params.hosts:
                    client.call('nvmf_subsystem_add_host',
                                {'nqn': params.subnqn,
                                 'host': host})
                if subsystem is not None:
                    for host in [h['nqn'] for h in subsystem['hosts']]:
                        if host not in params.hosts:
                            client.call('nvmf_subsystem_remove_host',
                                        {'nqn': params.subnqn,
                                         'host': host})

                addr = self._get_params(params, [
                                ('adrfam',),
                                ('traddr',),
                                ('trsvcid',)])
                if subsystem is None or not self._check_addr(addr,
                                                             subsystem['listen_addresses']):
                    client.call('nvmf_subsystem_add_listener',
                                {'nqn': params.subnqn,
                                 'listen_address': {'trtype': 'tcp', **addr}})
            except JSONRPCException:
                try:
                    client.call('nvmf_delete_subsystem', {'nqn': params.subnqn})
                except JSONRPCException:
                    logging.warning(f'Failed to delete subsystem: {params.subnqn}')
                raise DeviceException(grpc.StatusCode.INTERNAL,
                                      'Failed to create NVMe/TCP device')

        return sma_pb2.CreateDeviceResponse(id=f'nvmf-tcp:{params.subnqn}')

    @_check_transport
    def delete_device(self, request):
        with self._client() as client:
            nqn = request.id.removeprefix('nvmf-tcp:')
            subsystems = client.call('nvmf_get_subsystems')
            for subsystem in subsystems:
                if subsystem['nqn'] == nqn:
                    result = client.call('nvmf_delete_subsystem',
                                         {'nqn': nqn})
                    if not result:
                        raise DeviceException(grpc.StatusCode.INTERNAL,
                                              'Failed to delete device')
                    break
            else:
                logging.info(f'Tried to delete a non-existing device: {nqn}')

    def _find_bdev(self, client, guid):
        try:
            return client.call('bdev_get_bdevs', {'name': guid})[0]
        except JSONRPCException:
            return None

    @_check_transport
    def attach_volume(self, request):
        nqn = request.device_id.removeprefix('nvmf-tcp:')
        try:
            with self._client() as client:
                bdev = self._find_bdev(client, request.volume_guid)
                if bdev is None:
                    raise DeviceException(grpc.StatusCode.NOT_FOUND,
                                          'Invalid volume GUID')
                subsystems = client.call('nvmf_get_subsystems')
                for subsys in subsystems:
                    if subsys['nqn'] == nqn:
                        break
                else:
                    raise DeviceException(grpc.StatusCode.NOT_FOUND,
                                          'Invalid device ID')
                if bdev['name'] not in [ns['name'] for ns in subsys['namespaces']]:
                    result = client.call('nvmf_subsystem_add_ns',
                                         {'nqn': nqn,
                                          'namespace': {
                                              'bdev_name': bdev['name']}})
                    if not result:
                        raise DeviceException(grpc.StatusCode.INTERNAL,
                                              'Failed to attach volume')
        except JSONRPCException:
            # TODO: parse the exception's error
            raise DeviceException(grpc.StatusCode.INTERNAL,
                                  'Failed to attach volume')

    @_check_transport
    def detach_volume(self, request):
        nqn = request.device_id.removeprefix('nvmf-tcp:')
        volume = request.volume_guid
        try:
            with self._client() as client:
                bdev = self._find_bdev(client, volume)
                if bdev is None:
                    logging.info(f'Tried to detach non-existing volume: {volume}')
                    return

                subsystems = client.call('nvmf_get_subsystems')
                for subsys in subsystems:
                    if subsys['nqn'] == nqn:
                        break
                else:
                    logging.info(f'Tried to detach volume: {volume} from non-existing ' +
                                 f'device: {nqn}')
                    return

                for ns in subsys['namespaces']:
                    if ns['name'] != bdev['name']:
                        continue
                    result = client.call('nvmf_subsystem_remove_ns',
                                         {'nqn': nqn,
                                          'nsid': ns['nsid']})
                    if not result:
                        raise DeviceException(grpc.StatusCode.INTERNAL,
                                              'Failed to detach volume')
                    break
        except JSONRPCException:
            # TODO: parse the exception's error
            raise DeviceException(grpc.StatusCode.INTERNAL,
                                  'Failed to detach volume')

    def connect_volume(self, request):
        params = request.nvmf_tcp
        try:
            with self._client() as client:
                addr = self._get_params(params, [
                                ('adrfam',),
                                ('traddr',),
                                ('trsvcid',),
                                ('subnqn',)])
                existing = False
                controllers = client.call('bdev_nvme_get_controllers')
                for controller in controllers:
                    for path in controller['ctrlrs']:
                        trid = path['trid']
                        if self._check_addr(addr, (trid,)):
                            cname = controller['name']
                            bdevs = client.call('bdev_get_bdevs')
                            nbdevs = [(b['name'], b['driver_specific']['nvme'])
                                      for b in bdevs if b.get(
                                        'driver_specific', {}).get('nvme') is not None]
                            names = [name for name, nvme in nbdevs if
                                     self._check_addr(addr, [n['trid'] for n in nvme])]
                            break
                    else:
                        continue
                    existing = True
                    break
                else:
                    cname = str(uuid.uuid1())
                    names = client.call('bdev_nvme_attach_controller',
                                        {'name': cname,
                                         'trtype': 'tcp',
                                         **addr})
                    bdevs = client.call('bdev_get_bdevs')
                # Check if the controller contains specified volume
                for name in names:
                    bdev = next(filter(lambda b: b['name'] == name, bdevs), None)
                    if bdev is not None and request.volume_guid == bdev['uuid']:
                        break
                else:
                    # Detach the controller only if we've just connected it
                    if not existing:
                        try:
                            client.call('bdev_nvme_detach_controller',
                                        {'name': cname})
                        except JSONRPCException:
                            pass
                    raise DeviceException(grpc.StatusCode.INVALID_ARGUMENT,
                                          'Volume couldn\'t be found')
                self._add_volume(cname, request.volume_guid)
                return sma_pb2.ConnectVolumeResponse()
        except JSONRPCException:
            # TODO: parse the exception's error
            raise DeviceException(grpc.StatusCode.INTERNAL, 'Failed to connect the volume')

    def disconnect_volume(self, request):
        try:
            with self._client() as client:
                disconnect, cname = self._remove_volume(request.volume_guid)
                if not disconnect:
                    return cname is not None
                controllers = client.call('bdev_nvme_get_controllers')
                for controller in controllers:
                    if controller['name'] == cname:
                        result = client.call('bdev_nvme_detach_controller',
                                             {'name': cname})
                        if not result:
                            raise DeviceException(grpc.StatusCode.INTERNAL,
                                                  'Failed to disconnect the volume')
                        return True
                else:
                    logging.info('Tried to disconnect volume fron non-existing ' +
                                 f'controller: {cname}')
            return False
        except JSONRPCException:
            # TODO: parse the exception's error
            raise DeviceException(grpc.StatusCode.INTERNAL, 'Failed to disconnect the volume')

    def owns_device(self, id):
        return id.startswith('nvmf-tcp')
