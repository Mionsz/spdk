#!/usr/bin/env bash

testdir=$(readlink -f "$(dirname "$0")")
rootdir=$(readlink -f "$testdir/../..")

source "$rootdir/test/common/autotest_common.sh"
source "$rootdir/test/vhost/common.sh"
source "$testdir/vfiouser_common.sh"
source "$testdir/common.sh"

function cleanup() {
	vm_kill_all
	killprocess $tgtpid
	killprocess $smapid
	if [ -e "${VFO_ROOT_PATH}" ]; then rm -rf "${VFO_ROOT_PATH}"; fi
}

trap "cleanup; exit 1" SIGINT SIGTERM EXIT

VM_PASSWORD=intel123
VM_BIN_DIR="/home/mlinkiew/qemu"

VM_IMAGE="${VM_BIN_DIR}/image/spdk_test_image.qcow2"
QEMU_BIN="${VM_BIN_DIR}/vfio-user-v0.93/build/qemu-system-x86_64"

VM_0_id=0
VM_0_bus=spdk_bus_on_0
VM_0_addr=127.0.0.1
VM_0_qmp=10005
VM_0_mask=1
VM_0_numa_node=0

VFO_ROOT_PATH="/tmp/sma/vfio-user/qemu"

if [ -e "${VFO_ROOT_PATH}" ]; then rm -rf "${VFO_ROOT_PATH}"; fi
mkdir -p "${VFO_ROOT_PATH}"

# Cleanup old VM:
used_vms=${VM_0_id}
vm_kill_all

# Run pre-configuration script for 2 QEMU VMs
QEMU_BIN="${QEMU_BIN}" vm_setup --disk-type=virtio --force=$VM_0_id --os=$VM_IMAGE --pci-bridge=${VM_0_bus} --type-q35

# Run pre-configured VM and wait for them to start
vm_run ${used_vms}
vm_wait_for_boot 300 ${used_vms}

# Read and update QMP port numbers
VM_0_qmp=$(vm_qmp_port $VM_0_id)

# Start SPDK
$rootdir/build/bin/spdk_tgt &
tgtpid=$!
waitforlisten $tgtpid

# Prepare the target
rpc_cmd bdev_null_create null0 100 4096
rpc_cmd bdev_null_create null1 100 4096

# Start SMA server
$rootdir/scripts/sma.py -c <(
	cat <<- EOF
		devices:
		- name: 'vfiouser'
		  params:
		    root_path: "${VFO_ROOT_PATH}"
		    bus: "${VM_0_bus}"
		    address: "${VM_0_addr}"
		    port: ${VM_0_qmp}
	EOF
) &
smapid=$!

# Wait until the SMA starts listening
sma_waitforlisten

# Make sure a TCP transport has been created
rpc_cmd nvmf_get_transports --trtype VFIOUSER

# Make sure no nvme subsystems are present
[[ $(vm_exec ${VM_0_id} nvme list-subsys -o json | jq -r '.Subsystems | length') -eq 0 ]]

NQN_PFID_1_VFID_0=$(get_nqn_from_params 1 0)
NQN_PFID_2_VFID_0=$(get_nqn_from_params 2 0)
NQN_PFID_3_VFID_0=$(get_nqn_from_params 3 0)

# Create a couple of devices and verify them via RPC and SSH
# VM 1
ID_PFID_1_VFID_0=$(create_device 1 0 | jq -r '.id')
rpc_cmd nvmf_get_subsystems ${NQN_PFID_1_VFID_0}
vm_check_subsys_nqn $VM_0_id $NQN_PFID_1_VFID_0

# Check that there are two subsystems (1 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 2 ]]

ID_PFID_2_VFID_0=$(create_device 2 0 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
[[ "$ID_PFID_1_VFID_0" != "$ID_PFID_2_VFID_0" ]]
vm_check_subsys_nqn $VM_0_id $NQN_PFID_2_VFID_0

# Check that there are three subsystems (2 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 3 ]]

ID_PFID_3_VFID_0=$(create_device 3 0 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_3_VFID_0
[[ "$ID_PFID_3_VFID_0" != "$ID_PFID_1_VFID_0" ]]
[[ "$ID_PFID_3_VFID_0" != "$ID_PFID_2_VFID_0" ]]
vm_check_subsys_nqn $VM_0_id $NQN_PFID_3_VFID_0

# Check that there are four subsystems (3 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 4 ]]

# Verify the method is idempotent and sending the same gRPCs won't create new
# devices and will return the same IDs
TMP_PFID_1_VFID_0=$(create_device 1 0 | jq -r '.id')
TMP_PFID_2_VFID_0=$(create_device 2 0 | jq -r '.id')
TMP_PFID_3_VFID_0=$(create_device 3 0 | jq -r '.id')

[[ $(vm_count_nvme ${VM_0_id}) -eq 3 ]]

[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 4 ]]
[[ "$TMP_PFID_1_VFID_0" == "$ID_PFID_1_VFID_0" ]]
[[ "$TMP_PFID_2_VFID_0" == "$ID_PFID_2_VFID_0" ]]
[[ "$TMP_PFID_3_VFID_0" == "$ID_PFID_3_VFID_0" ]]

# Now remove them verifying via RPC
delete_device "$ID_PFID_1_VFID_0"
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_3_VFID_0
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 3 ]]
[[ $(vm_count_nvme ${VM_0_id}) -eq 2 ]]

delete_device "$ID_PFID_2_VFID_0"
delete_device "$ID_PFID_3_VFID_0"
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_3_VFID_0
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 1 ]]
[[ $(vm_count_nvme ${VM_0_id}) -eq 0 ]]

# Finally check that removing a non-existing device is also sucessful
delete_device "$ID_PFID_1_VFID_0"
delete_device "$ID_PFID_2_VFID_0"
delete_device "$ID_PFID_3_VFID_0"

# Check volume attach/detach
ID_PFID_1_VFID_0=$(create_device 1 0 | jq -r '.id')
ID_PFID_2_VFID_0=$(create_device 2 0 | jq -r '.id')
BDEV_UUID_0=$(rpc_cmd bdev_get_bdevs -b null0 | jq -r '.[].uuid')
BDEV_UUID_1=$(rpc_cmd bdev_get_bdevs -b null1 | jq -r '.[].uuid')

# Attach the first volume to a first subsystem
attach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_0

attach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_1" ]]
vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_1

# Attach the same device again and see that it won't fail
attach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
attach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_1" ]]
vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_0
NOT vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_1
vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_1
NOT vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_0

# Cross detach volumes and verify they not fail and have not been removed from the subsystems
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_1"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_0"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_1" ]]
vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_0
NOT vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_1
vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_1
NOT vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_0

# Detach volumes and verify they have been removed from the subsystems
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 0 ]]
NOT vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_0
NOT vm_check_subsys_volume $VM_0_id $NQN_PFID_1_VFID_0 $BDEV_UUID_1

# Detach volumes once again and verify they will not fail
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_1"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_0"

delete_device "$ID_PFID_1_VFID_0"
delete_device "$ID_PFID_2_VFID_0"

cleanup
trap - SIGINT SIGTERM EXIT
