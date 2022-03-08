import grpc
from google.protobuf import wrappers_pb2 as wrap
import logging
from spdk.rpc.client import JSONRPCException
from .subsystem import Subsystem, SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_tcp_pb2


class NvmfTcpSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('nvmf-tcp', client)

    def init(self, config):
        self._has_transport = self._create_transport()

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
            a['trsvcid'].lower() == addr['trsvcid'].lower()), addrlist), None) is not None

    @_check_transport
    def create_device(self, request):
        params = nvmf_tcp_pb2.CreateDeviceParameters()
        if not request.params.Unpack(params):
            raise SubsystemException(grpc.StatusCode.INVALID_ARGUMENT,
                                     'Failed to parse device parameters')
        self._check_params(params, ['subnqn', 'adrfam', 'traddr', 'trsvcid'])
        with self._client() as client:
            try:
                subsystems = client.call('nvmf_get_subsystems')
                for subsystem in subsystems:
                    if subsystem['nqn'] == params.subnqn.value:
                        break
                else:
                    subsystem = None
                    result = client.call('nvmf_create_subsystem',
                                         {**self._get_params(params, [
                                                ('subnqn', 'nqn')])})
            except JSONRPCException:
                raise SubsystemException(grpc.StatusCode.INTERNAL,
                                         'Failed to create NVMe/TCP device')
            try:
                client.call('nvmf_subsystem_allow_any_host',
                            {'nqn': params.subnqn.value,
                             'allow_any_host': len(params.hosts) == 0})
                for host in params.hosts:
                    client.call('nvmf_subsystem_add_host',
                                {'nqn': params.subnqn.value,
                                 'host': host.value})
                if subsystem is not None:
                    for host in [h['nqn'] for h in subsystem['hosts']]:
                        if host not in [h.value for h in params.hosts]:
                            client.call('nvmf_subsystem_remove_host',
                                        {'nqn': params.subnqn.value,
                                         'host': host})

                addr = self._get_params(params, [
                                ('adrfam',),
                                ('traddr',),
                                ('trsvcid',)])
                if subsystem is None or not self._check_addr(addr,
                                                             subsystem['listen_addresses']):
                    client.call('nvmf_subsystem_add_listener',
                                {'nqn': params.subnqn.value,
                                 'listen_address': {'trtype': 'tcp', **addr}})
            except JSONRPCException:
                try:
                    client.call('nvmf_delete_subsystem', {'nqn': params.subnqn.value})
                except JSONRPCException:
                    logging.warning(f'Failed to remove subsystem: {params.subnqn.value}')
                raise SubsystemException(grpc.StatusCode.INTERNAL,
                                         'Failed to create NVMe/TCP device')

        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'nvmf-tcp:{params.subnqn.value}'))
