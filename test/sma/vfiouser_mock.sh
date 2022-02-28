#!/usr/bin/env bash

testdir=$(readlink -f "$(dirname "$0")")
rootdir=$(readlink -f "$testdir/../..")

source "$rootdir/test/common/autotest_common.sh"
source "$testdir/vfiouser_common.sh"
source "$testdir/common.sh"

function cleanup() {
	killprocess $tgtpid
	killprocess $smapid
	killprocess $qmp_0_pid
	if [ -e "${VFO_ROOT_PATH}" ]; then rm -rf "${VFO_ROOT_PATH}"; fi
}

trap "cleanup; exit 1" SIGINT SIGTERM EXIT

VM_0_id=0
VM_0_bus=spdk_bus_on_0
VM_0_addr=127.0.0.1
VM_0_qmp=10005
VM_0_mask=1
VM_0_numa_node=0

VFO_ROOT_PATH="/tmp/sma/vfio-user/mock"

if [ -e "${VFO_ROOT_PATH}" ]; then rm -rf "${VFO_ROOT_PATH}"; fi
mkdir -p "${VFO_ROOT_PATH}"

NQN_PFID_1_VFID_0=$(get_nqn_from_params 1 0)
NQN_PFID_2_VFID_0=$(get_nqn_from_params 2 0)
NQN_PFID_3_VFID_0=$(get_nqn_from_params 3 0)

$rootdir/build/bin/spdk_tgt &
tgtpid=$!
waitforlisten $tgtpid

# Prepare the target
rpc_cmd bdev_null_create null0 100 4096
rpc_cmd bdev_null_create null1 100 4096

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

$rootdir/test/sma/qmp_mock.py --address ${VM_0_addr} --port ${VM_0_qmp} --bus ${VM_0_bus} &
qmp_0_pid=$!

# Wait until the QMP server starts listening
qmp_waitforlisten ${VM_0_addr} ${VM_0_qmp}

# Make sure a TCP transport has been created
rpc_cmd nvmf_get_transports --trtype VFIOUSER

# Create a couple of devices and verify them via RPC
ID_PFID_1_VFID_0=$(create_device 1 0 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0

# Check that there are two subsystems (1 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 2 ]]

ID_PFID_2_VFID_0=$(create_device 2 0 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
[[ "$ID_PFID_1_VFID_0" != "$ID_PFID_2_VFID_0" ]]

# Check that there are three subsystems (2 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 3 ]]

ID_PFID_3_VFID_0=$(create_device 3 0 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
rpc_cmd nvmf_get_subsystems $NQN_PFID_3_VFID_0
[[ "$ID_PFID_3_VFID_0" != "$ID_PFID_1_VFID_0" ]]
[[ "$ID_PFID_3_VFID_0" != "$ID_PFID_2_VFID_0" ]]

# Check that there are four subsystems (3 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 4 ]]

# Verify the method is idempotent and sending the same gRPCs won't create new
# devices and will return the same IDs
TMP_PFID_1_VFID_0=$(create_device 1 0 | jq -r '.id')
TMP_PFID_2_VFID_0=$(create_device 2 0 | jq -r '.id')
TMP_PFID_3_VFID_0=$(create_device 3 0 | jq -r '.id')

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

delete_device "$ID_PFID_2_VFID_0"
delete_device "$ID_PFID_3_VFID_0"
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0
NOT rpc_cmd nvmf_get_subsystems $NQN_PFID_3_VFID_0
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 1 ]]

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

attach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_1" ]]

# Attach the same device again and see that it won't fail
attach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
attach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_1" ]]

# Cross detach volumes and verify they not fail and have not been removed from the subsystems
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_1"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_0"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_0" ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces[0].uuid') == "$BDEV_UUID_1" ]]

# Detach volumes and verify they have been removed from the subsystems
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_1_VFID_0 | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $NQN_PFID_2_VFID_0 | jq -r '.[0].namespaces | length') -eq 0 ]]

# Detach volumes once again and verify they will not fail
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_0"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_1"
detach_volume "$ID_PFID_1_VFID_0" "$BDEV_UUID_1"
detach_volume "$ID_PFID_2_VFID_0" "$BDEV_UUID_0"

delete_device "$ID_PFID_1_VFID_0"
delete_device "$ID_PFID_2_VFID_0"

cleanup
trap - SIGINT SIGTERM EXIT
