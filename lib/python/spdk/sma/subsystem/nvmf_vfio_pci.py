import grpc
from google.protobuf import wrappers_pb2 as wrap
import logging
import uuid
from .nvmf import Nvmf, NvmfTr, NvmeErr, NvmfException
from ..proto import sma_pb2
from ..proto import nvmf_vfio_pci_pb2


class NvmfVfioUserPciSubsystem(Nvmf):
    def __init__(self, client):
        super().__init__(NvmfTr.VFIOUSER, client)

    def create_device(self, request):
        params = self._unpack_request(request)
        self._check_params(params, ['subnqn', 'traddr'])
        with self._client_safe(NvmeErr.DEVICE_CREATE) as client:
            nqn = params.subnqn.value
            created = self._check_create_subsystem(client, nqn)
            addr = self._get_params(params, [('traddr',)])
            self._check_create_listener(client, nqn, addr, created)
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=self._nvme_tr.prefix_add(nqn)))

    def remove_device(self, request):
        with self._client_safe(NvmeErr.DEVICE_REMOVE) as client:
            nqn = self._nvme_tr.prefix_rem(request.id.value)
            subsystem = self._get_subsystem_by_nqn(nqn)
            if subsystem is not None:
                if not client.call('nvmf_delete_subsystem', {'nqn': nqn}):
                    raise NvmfException(NvmeErr.DEVICE_REMOVE, self._nvme_tr, nqn)
            else:
                logging.info(f'Tried to remove a non-existing device: {nqn}')
