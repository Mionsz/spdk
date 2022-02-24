from google.protobuf import wrappers_pb2 as wrap
from spdk.sma import subsystem
from spdk.sma.proto import sma_pb2


class TestSubsystem1(subsystem.Subsystem):
    def __init__(self, client):
        super().__init__('plugin1-subsys1', 'protocol1', client)

    def create_device(self, request):
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'{self.protocol}:{self.name}'))


class TestSubsystem2(subsystem.Subsystem):
    def __init__(self, client):
        super().__init__('plugin1-subsys2', 'protocol2', client)

    def create_device(self, request):
        return sma_pb2.CreateDeviceResponse(id=wrap.StringValue(
                    value=f'{self.protocol}:{self.name}'))


subsystems = [TestSubsystem1, TestSubsystem2]
