#!/usr/bin/env bash

testdir=$(readlink -f "$(dirname "$0")")
rootdir=$(readlink -f "$testdir/../..")
VFO_ROOT_PATH="/tmp/vfio-user/sma"

source "$rootdir/test/common/autotest_common.sh"
source "$rootdir/test/vhost/common.sh"
source "$testdir/vfiouser_common.sh"
source "$testdir/common.sh"

function cleanup() {
	vm_kill_all
	killprocess $tgtpid
	killprocess $smapid
	rm -r /tmp/vfio-user/sma
}

trap "cleanup; exit 1" SIGINT SIGTERM EXIT

VM_IMAGE="${HOME}/qemu/image/spdk_test_image.qcow2"
QEMU_BIN="${HOME}/qemu/vfio-user-v0.93/build/qemu-system-x86_64"

mkdir -p "VFO_ROOT_PATH"

VM_0_qemu_id=0
VM_0_qemu_bus=spdk_bus_on_0
VM_0_qemu_addr=127.0.0.1
VM_0_qemu_port=10005
VM_0_qemu_mask=1
VM_0_qemu_numa_node=0

VM_1_qemu_id=1
VM_1_qemu_bus=spdk_bus_on_1
VM_1_qemu_addr=127.0.0.1
VM_1_qemu_port=10105
VM_1_qemu_mask=2
VM_1_qemu_numa_node=0

# Cleanup old VM:
used_vms="${VM_0_qemu_id} ${VM_1_qemu_id}"
vm_kill_all

# Run pre-configuration script for 2 QEMU VMs
QEMU_BIN="${QEMU_BIN}" vm_setup --disk-type=virtio --force=0 --os=$VM_IMAGE --qemu-args=-device --qemu-args=pcie-pci-bridge,id=spdk_pcie_pci_bridge --qemu-args=-device --qemu-args=pci-bridge,id=${VM_0_qemu_bus},bus=spdk_pcie_pci_bridge,chassis_nr=13,addr=0x5
QEMU_BIN="${QEMU_BIN}" vm_setup --disk-type=virtio --force=1 --os=$VM_IMAGE --qemu-args=-device --qemu-args=pcie-pci-bridge,id=spdk_pcie_pci_bridge --qemu-args=-device --qemu-args=pci-bridge,id=${VM_1_qemu_bus},bus=spdk_pcie_pci_bridge,chassis_nr=13,addr=0x5

# Run pre-configured VM and wait for them to start
vm_run $used_vms
vm_wait_for_boot 300 $used_vms

# Read and update QMP port numbers
VM_0_qemu_port=$(vm_qmp_port 0)
VM_1_qemu_port=$(vm_qmp_port 1)

# Start SPDK
$rootdir/build/bin/spdk_tgt &
tgtpid=$!
waitforlisten $tgtpid

# Prepare the target
rpc_cmd bdev_null_create null0 100 4096
rpc_cmd bdev_null_create null1 100 4096

# Start SMA server
SMA_LOGLEVEL=DEBUG $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		subsystems:
		  - name: 'vfiouser'
		    params:
		      root_path: '${VFO_ROOT_PATH}'
		      hosts:
		        - id: ${VM_0_qemu_id}
		          bus: '${VM_0_qemu_bus}'
		          address: '${VM_0_qemu_addr}'
		          port: ${VM_0_qemu_port}
		        - id: ${VM_1_qemu_id}
		          bus: '${VM_1_qemu_bus}'
		          address: '${VM_1_qemu_addr}'
		          port: ${VM_1_qemu_port}
	EOF
) &
smapid=$!

# Wait until the SMA starts listening
sma_waitforlisten

# Make sure a TCP transport has been created
rpc_cmd nvmf_get_transports --trtype VFIOUSER
vm_nqn=$(vm_nopass_exec ${VM_0_qemu_id} "nvme list-subsys -o json | jq -r '.Subsystems[0].NQN'")
# Create a couple of devices and verify them via RPC and SSH
devid_0_0=$(create_device ${VM_0_qemu_id} 1 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${VM_0_qemu_id} 1 0)

ssh_0_0=$(vm_nopass_exec ${VM_0_qemu_id} "nvme list-subsys -o json | jq -r '.Subsystems[0].NQN'")
nqn_0_0=$(get_nqn_from_params ${VM_0_qemu_id} 1 0)
[[ "${ssh_0_0}" == "${nqn_0_0}" ]]

devid_1_0=$(create_device ${VM_1_qemu_id} 1 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${VM_1_qemu_id} 1 0)

ssh_1_0=$(vm_nopass_exec ${VM_1_qemu_id} "nvme list-subsys -o json | jq -r '.Subsystems[0].NQN'")
nqn_1_0=$(get_nqn_from_params ${VM_1_qemu_id} 1 0)
[[ "${ssh_1_0}" == "${nqn_1_0}" ]]

devid_0_1=$(create_device ${VM_0_qemu_id} 2 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${VM_0_qemu_id} 1 0)
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${VM_0_qemu_id} 2 0)
[[ "$devid_0_0" != "$devid_0_1" ]]

devid_1_1=$(create_device ${VM_1_qemu_id} 2 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${VM_1_qemu_id} 1 0)
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${VM_1_qemu_id} 2 0)
[[ "$devid_1_0" != "$devid_1_1" ]]

# Check that there are three subsystems (5 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 5 ]]

# Verify the method is idempotent and sending the same gRPCs won't create new
# devices and will return the same IDs
tmp_0_0=$(create_device ${VM_0_qemu_id} 1 | jq -r '.id')
tmp_0_1=$(create_device ${VM_0_qemu_id} 2 | jq -r '.id')
tmp_1_0=$(create_device ${VM_1_qemu_id} 1 | jq -r '.id')
tmp_1_1=$(create_device ${VM_1_qemu_id} 2 | jq -r '.id')

[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 5 ]]
[[ "$tmp_0_0" == "$devid_0_0" ]]
[[ "$tmp_0_1" == "$devid_0_1" ]]
[[ "$tmp_1_0" == "$devid_1_0" ]]
[[ "$tmp_1_1" == "$devid_1_1" ]]

cleanup
trap - SIGINT SIGTERM EXIT
