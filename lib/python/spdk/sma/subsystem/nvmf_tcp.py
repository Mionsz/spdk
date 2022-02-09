import grpc
from google.protobuf import wrappers_pb2 as wrap
import logging
import uuid
from .nvmf import Nvmf, NvmfTr, NvmeErr, NvmfException
from spdk.rpc.client import JSONRPCException
from .subsystem import SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_tcp_pb2


class NvmfTcpSubsystem(Nvmf):
    def __init__(self, client):
        super().__init__(NvmfTr.TCP_IP4, client)

    def _add_volume(self, ctrlr_name, volume_guid):
        volumes = self._controllers.get(ctrlr_name, [])
        if volume_guid in volumes:
            return
        self._controllers[ctrlr_name] = volumes + [volume_guid]

    def _remove_volume(self, volume_guid):
        for ctrlr, volumes in self._controllers.items():
            if volume_guid in volumes:
                volumes.remove(volume_guid)
                return len(volumes) == 0, ctrlr
        return False, None

    def _check_transport(f):
        def wrapper(self, *args):
            if not self._has_transport:
                raise NvmfException(NvmeErr.TRANSPORT_UNAV, self._nvme_tr)
            return f(self, *args)
        return wrapper

    @_check_transport
    def create_device(self, request):
        params = nvmf_tcp_pb2.CreateDeviceParameters()
        if not request.params.Unpack(params):
            raise SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                     'Failed to parse device parameters')
        self._check_params(params, ['subnqn', 'adrfam', 'traddr', 'trsvcid'])
        try:
            with self._client() as client:
                subsystems = client.call('nvmf_get_subsystems')
                for subsystem in subsystems:
                    if subsystem['nqn'] == params.subnqn.value:
                        break
                else:
                    subsystem = None
                    result = client.call('nvmf_create_subsystem',
                                         {'allow_any_host': True,
                                          **self._get_params(params, [
                                                ('subnqn', 'nqn')])})
                    if not result:
                        raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                 'Failed to create NVMe/TCP subsystem')
                addr = self._get_params(params, [
                                ('adrfam',),
                                ('traddr',),
                                ('trsvcid',)])
                if subsystem is None or not self._check_addr(addr,
                                                             subsystem['listen_addresses']):
                    result = client.call('nvmf_subsystem_add_listener',
                                         {'nqn': params.subnqn.value,
                                          'listen_address': {
                                              'trtype': 'tcp', **addr}})
                    # TODO: we should probably clean-up the subsystem in case
                    # the add_listener call fails
                    if not result:
                        raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                 'Failed to add TCP listener')
        except JSONRPCException:
            # TODO parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to create the device')
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'nvmf_tcp:{params.subnqn.value}'))

    @_check_transport
    def remove_device(self, request):
        with self._client() as client:
            nqn = request.id.value.removeprefix('nvmf_tcp:')
            subsystems = client.call('nvmf_get_subsystems')
            for subsystem in subsystems:
                if subsystem['nqn'] == nqn:
                    result = client.call('nvmf_delete_subsystem',
                                         {'nqn': nqn})
                    if not result:
                        raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                 'Failed to remove device')
                    break
            else:
                logging.info(f'Tried to remove a non-existing device: {nqn}')

    @_check_transport
    def connect_volume(self, request):
        params = nvmf_tcp_pb2.ConnectVolumeParameters()
        if not request.params.Unpack(params):
            raise SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                     'Failed to parse parameters')
        self._check_params(params, ['subnqn', 'adrfam', 'traddr', 'trsvcid'])
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
                    if bdev is not None and request.guid.value == bdev['uuid']:
                        break
                else:
                    # Detach the controller only if we've just connected it
                    if not existing:
                        try:
                            client.call('bdev_nvme_detach_controller',
                                        {'name': cname})
                        except JSONRPCException:
                            pass
                    raise SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                             'Volume couldn\'t be found')
                self._add_volume(cname, request.guid.value)
                return sma_pb2.ConnectVolumeResponse()
        except JSONRPCException:
            # TODO: parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to connect the volume')

    @_check_transport
    def disconnect_volume(self, request):
        try:
            with self._client() as client:
                disconnect, cname = self._remove_volume(request.guid.value)
                if not disconnect:
                    return cname is not None
                controllers = client.call('bdev_nvme_get_controllers')
                for controller in controllers:
                    if controller['name'] == cname:
                        result = client.call('bdev_nvme_detach_controller',
                                             {'name': cname})
                        if not result:
                            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                     'Failed to disconnect the volume')
                        return True
                else:
                    logging.info('Tried to disconnect volume fron non-existing ' +
                                 f'controller: {cname}')
            return False
        except JSONRPCException:
            # TODO: parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to disconnect the volume')

    @_check_transport
    def attach_volume(self, request):
        self._check_params(request, ['volume_guid'])
        nqn = request.device_id.value.removeprefix('nvmf_tcp:')
        try:
            with self._client() as client:
                bdevs = client.call('bdev_get_bdevs')
                for bdev in bdevs:
                    if bdev['uuid'] == request.volume_guid.value:
                        break
                else:
                    raise SubsystemException(grpc.StatusCode.NOT_FOUND,
                                             'Invalid volume GUID')
                subsystems = client.call('nvmf_get_subsystems')
                for subsys in subsystems:
                    if subsys['nqn'] == nqn:
                        break
                else:
                    raise SubsystemException(grpc.StatusCode.NOT_FOUND,
                                             'Invalid device ID')
                if bdev['name'] not in [ns['name'] for ns in subsys['namespaces']]:
                    result = client.call('nvmf_subsystem_add_ns',
                                         {'nqn': nqn,
                                          'namespace': {
                                              'bdev_name': bdev['name']}})
                    if not result:
                        raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                 'Failed to attach volume')
        except JSONRPCException:
            # TODO: parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to disconnect controller')

    @_check_transport
    def detach_volume(self, request):
        self._check_params(request, ['volume_guid', 'device_id'])
        nqn = request.device_id.value.removeprefix('nvmf_tcp:')
        volume = request.volume_guid.value
        try:
            with self._client() as client:
                bdevs = client.call('bdev_get_bdevs')
                for bdev in bdevs:
                    if bdev['uuid'] == volume:
                        break
                else:
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
                        raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                 'Failed to detach volume')
                    break
        except JSONRPCException:
            # TODO: parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to disconnect controller')

    def owns_device(self, id):
        return id.startswith('nvmf_tcp')
