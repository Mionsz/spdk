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

    def owns_device(self, id):
        return id.startswith(self.protocol)
