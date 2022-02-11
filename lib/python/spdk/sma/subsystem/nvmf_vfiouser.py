import logging
from spdk.rpc.client import JSONRPCException
from .subsystem import Subsystem

log = logging.getLogger(__name__)


class NvmfVfioSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('vfiouser', 'nvme', client)

    def init(self, config):
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

    def owns_device(self, id):
        return id.startswith(self.protocol)
