import logging
from socket import AddressFamily
from spdk.rpc.client import JSONRPCException
from .device import DeviceManager

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

    def owns_device(self, id):
        return id.startswith(self.protocol)
