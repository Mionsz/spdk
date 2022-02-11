import logging
import grpc
from spdk.rpc.client import JSONRPCException
from google.protobuf import wrappers_pb2 as wrap
from .subsystem import Subsystem, SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_vfio_pb2
from ..qmp import QMPClient, QMPError

log = logging.getLogger(__name__)


class NvmfVfioSubsystem(Subsystem):
    def __init__(self, client):
        super().__init__('nvmf_vfio', client)
        self._trtype = 'vfiouser'
        self._has_transport = self._create_transport()

    def _create_transport(self):
        try:
            with self._client() as client:
                transports = client.call('nvmf_get_transports')
                for transport in transports:
                    if transport['trtype'].lower() == self._trtype:
                        return True
                # TODO: take the transport params from config
                return client.call('nvmf_create_transport',
                                   {'trtype': self._trtype})
        except JSONRPCException:
            logging.error(f'Transport query NVMe/{self._trtype} failed')
            return False

    def owns_device(self, id):
        return id.startswith(self.name)
