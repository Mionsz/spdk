# Storage Management Agent {#sma}

Storage Management Agent (SMA) is a service providing a gRPC interface for
orchestrating SPDK applications.  It's a standalone application that allows
users to create and manage various types of devices (e.g. NVMe, virtio-blk,
etc.).  The major difference between SMA's API and the existing SPDK-RPC
interface is that it's designed to be consumed by orchestration frameworks such
as k8s or OpenStack, which don't necessarily care about all the low-level
details exposed by SPDK-RPCs.  This is especially important for deployments on
IPUs (Infrastructure Processing Unit), which require a lot of hardware-specific
options.

## Interface

The interface is defined in a protobuf files located in `python/spdk/sma/proto`
directory.  The generic interface common to all types of devices is defined in
`sma.proto` file, while device-specific options are defined in their separate
files (e.g. `nvme.proto` for NVMe).

## Running and Configuration

SMA can be started using a script located in `scripts/sma.py`.  It requires a
configuration file that specifies which types of devices to service, as well as
several other options (e.g. listen address, SPDK-RPC socket, etc.).  Device
types not listed in the configuration will be disabled and it won't be possible
to manage them.  The file uses YAML format.  Below is an example configuration
enabling two device types (NVMe/vfiouser and vhost-blk):

```yaml
address: 'localhost'
socket: '/var/tmp/spdk.sock'
port: 8080
devices:
  - name: 'vfiouser'
    params:
      root_path: '/var/tmp/vfiouser'
      bus: 'bus0'
      address: '127.0.0.1'
      port: 4444
  - name: 'vhost-blk'
```

## Plugins

SMA provides a way to load external plugins implementing support for specific
device types.  A plugin will be loaded if it's specified in the `SMA_PLUGINS`
environment variable (multiple plugins are separated with a colon) or if it's
specified in the `plugins` section of the config file.  For example, the
following two methods are equivalent:

```sh
$ SMA_PLUGINS=plugin1:plugin2 scripts/sma.py

$ cat sma.yaml
plugins:
  - 'plugin1'
  - 'plugin2'
$ scripts/sma.py -c sma.yaml
```

Each plugin needs to be in the python search path (either in one of the default
directories or added to `PYTHONPATH`).

A plugin is required to define a global variable called `devices` storing a list
of classes deriving from `spdk.sma.DeviceManager`.  This base class define the
interface each device needs to implement.  Additionally, each DeviceManager
needs to define a unique name that will be used to identify it in config file as
well as the name of the protocol it supports.  There can be many DeviceManagers
supporting the same protocol, but only one can be active at a time.  The name of
the protocol shall match the type specified in `CreateDeviceRequest.params`
(e.g. "nvme", "virtio_blk", etc.), as it'll be used to select the DeviceManager
to handle a gRPC request.  Finally, a DeviceManager needs to implement the
`own_device()` method returning a boolean value indicating whether a given
device handle is owned by that DeviceManager.
