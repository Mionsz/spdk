import grpc
from google.protobuf import wrappers_pb2 as wrap
import logging
import uuid
from spdk.rpc.client import JSONRPCException
from .subsystem import Subsystem, SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_tcp_pb2


class NvmfTcpSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('nvmf_tcp', client)
        self._has_transport = False
        self.__check_transport()

    def __check_transport(self):
        try:
            with self._client() as client:
                # If the transport has already been created we're done
                if self._has_transport:
                    return True
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == 'tcp':
                        return True
                # TODO: take the transport params from config
                self._has_transport = client.call('nvmf_create_transport',
                                                  {'trtype': 'tcp'})
                return self._has_transport
        except JSONRPCException:
            logging.error('Failed to query for NVMe/TCP transport')
            return False

    def _check_transport(f):
        def wrapper(self, *args):
            if not self.__check_transport():
                raise SubsystemException(grpc.StatusCode.INTERNAL,
                                         'NVMe/TCP transport is unavailable')
            return f(self, *args)
        return wrapper

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
                raise SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                         f'Missing required field: {param}')

    def _check_addr(self, addr, addrlist):
        return next(filter(lambda a: (
            a['trtype'].lower() == 'tcp' and
            a['adrfam'].lower() == addr['adrfam'].lower() and
            a['traddr'].lower() == addr['traddr'].lower() and
            a['trsvcid'].lower() == addr['trsvcid'].lower() and
            a.get('subnqn') == addr.get('subnqn')), addrlist), None) is not None

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
    def connect_controller(self, request):
        params = nvmf_tcp_pb2.ConnectControllerParameters()
        if not request.params.Unpack(params):
            raise SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                     'Failed to parse controller parameters')
        self._check_params(params, ['subnqn', 'adrfam', 'traddr', 'trsvcid'])
        try:
            with self._client() as client:
                addr = self._get_params(params, [
                                ('adrfam',),
                                ('traddr',),
                                ('trsvcid',),
                                ('subnqn',)])

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
                    break
                else:
                    cname = str(uuid.uuid1())
                    names = client.call('bdev_nvme_attach_controller',
                                        {'name': cname,
                                         'trtype': 'tcp',
                                         **addr})
                    bdevs = client.call('bdev_get_bdevs')
                response = sma_pb2.ConnectControllerResponse(
                    controller=wrap.StringValue(value=f'nvmf_tcp:{cname}'),
                    volumes=[wrap.StringValue(value=b['uuid'])
                             for b in bdevs if b['name'] in names])
                return response
        except JSONRPCException:
            # TODO: parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to connect the controller')

    @_check_transport
    def disconnect_controller(self, request):
        try:
            name = request.id.value.removeprefix('nvmf_tcp:')
            with self._client() as client:
                controllers = client.call('bdev_nvme_get_controllers')
                for controller in controllers:
                    if controller['name'] == name:
                        result = client.call('bdev_nvme_detach_controller',
                                             {'name': name})
                        if not result:
                            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                                     'Failed to disconnect controller')
                        break
                else:
                    logging.info(f'Tried to disconnect non-existing controller: {name}')
        except JSONRPCException:
            # TODO: parse the exception's error
            raise SubsystemException(grpc.StatusCode.INTERNAL,
                                     'Failed to disconnect controller')

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

    def owns_controller(self, id):
        return id.startswith('nvmf_tcp')
