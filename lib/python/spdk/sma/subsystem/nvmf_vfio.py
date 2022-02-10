import grpc
import re
from google.protobuf import wrappers_pb2 as wrap
import logging
import uuid
from .nvmf import Nvmf, NvmfTr, NvmeErr, NvmfException
from spdk.rpc.client import JSONRPCException
from .subsystem import SubsystemException
from ..proto import sma_pb2
from ..proto import nvmf_vfio_pb2
from ..qmp import QMPClient


class NvmfVfioSubsystem(Nvmf):
    def __init__(self, client):
        super().__init__(client, NvmfTr.VFIOUSER)

    def _get_subsystem_trid(self, request):
        params = self._unpack_request(request)
        self._check_params(params, ['subnqn', 'traddr'])
        addr = self._get_params(params, [('traddr',)])

    def create_device(self, request):
        params = self._unpack_request(request)
        self._check_params(params, ['subnqn', 'traddr'])
        addr = self._get_params(params, [('traddr',)])
        nqn = params.subnqn.value
        addr['traddr'] = "/var/run/vfio-user/domain/vfio-user2/2"
        addr['trtype'] = self.get_trtype()
        
        for client in self._client_safe(NvmeErr.DEVICE_CREATE):
            created = self._check_create_subsystem(client, nqn)
            self._check_create_listener(client, nqn, addr, created)
        with QMPClient() as qclient:            
            bus = "spdk_pci"
            safe_nqn = re.sub("[^0-9a-zA-Z]+", "1", nqn)
            qclient.exec_device_add(addr['traddr'], bus, safe_nqn)
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=self._nvme_tr.prefix_add(nqn)))

    def remove_device(self, request):
        for client in self._client_safe(NvmeErr.DEVICE_CREATE):
            nqn = self._nvme_tr.prefix_rem(request.id.value)
            subsystem = self._get_subsystem_by_nqn(client, nqn)
            if subsystem is not None:
                with QMPClient() as qclient:
                    qclient.exec_device_del(nqn)
                if not client.call('nvmf_delete_subsystem', {'nqn': nqn}):
                    raise NvmfException(NvmeErr.DEVICE_REMOVE, self._nvme_tr, nqn)
            else:
                logging.info(f'Tried to remove a non-existing device: {nqn}')
