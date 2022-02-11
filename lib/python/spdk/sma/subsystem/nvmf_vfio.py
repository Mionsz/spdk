import os
from google.protobuf import wrappers_pb2 as wrap
import logging
import re

from pecan import request
from .nvmf import Nvmf, NvmfTr, NvmeErr, NvmfException
from ..proto import sma_pb2
from ..qmp import QMPClient


class NvmfVfioSubsystem(Nvmf):
    def __init__(self, client):
        super().__init__(client, NvmfTr.VFIOUSER)

    def _create_path(self, path):
        try:
            print('path {path}')
            if not os.path.exists(path):
                print('creating {path}')
                os.makedirs(path)
        except OSError as e:
            raise NvmfException(NvmeErr.TRANSPORT_UNAV,
                                "Path creation failed.") from e

    def create_device(self, request):
        params = self._unpack_request(request)
        self._check_params(params, ['subnqn', 'traddr'])
        addr = self._get_params(params, [('traddr',)])
        nqn = params.subnqn.value
        addr['trtype'] = self.get_trtype()

        for client in self._client_safe(NvmeErr.DEVICE_CREATE):
            created = self._check_create_subsystem(client, nqn)
            if not self._check_listener(client, nqn, addr):
                self._create_path(addr['traddr'])
                self._create_listener(client, nqn, addr, created)
        with QMPClient() as qclient:
            id = re.sub("[^0-9a-zA-Z]+", "1", nqn)
            qclient.exec_device_add(addr['traddr'], "spdk_pci", id)
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=self._nvme_tr.prefix_add(nqn)))

    def remove_device(self, request):
        for client in self._client_safe(NvmeErr.DEVICE_CREATE):
            nqn = self._nvme_tr.prefix_rem(request.id.value)
            if self._get_subsystem_by_nqn(client, nqn) is not None:
                with QMPClient() as qclient:
                    id = re.sub("[^0-9a-zA-Z]+", "1", nqn)
                    qclient.exec_device_del(id)
                if not client.call('nvmf_delete_subsystem', {'nqn': nqn}):
                    raise NvmfException(NvmeErr.DEVICE_REMOVE, self._nvme_tr, nqn)
            else:
                logging.info(f'Tried to remove a non-existing device: {nqn}')

    def owns_device(self, id):
        return self._nvme_tr.check_prefix(id)
