from google.protobuf import wrappers_pb2 as wrap
from spdk.sma import DeviceManager
from spdk.sma.proto import sma_pb2


class TestDeviceManager1(DeviceManager):
    def __init__(self, client):
        super().__init__('plugin1-device1', 'protocol1', client)

    def create_device(self, request):
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'{self.protocol}:{self.name}'))


class TestDeviceManager2(DeviceManager):
    def __init__(self, client):
        super().__init__('plugin1-device2', 'protocol2', client)

    def create_device(self, request):
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'{self.protocol}:{self.name}'))


devices = [TestDeviceManager1, TestDeviceManager2]
