#!/usr/bin/env bash

testdir=$(readlink -f "$(dirname "$0")")
rootdir=$(readlink -f "$testdir/../..")

qmp_mock_0_id=0
qmp_mock_0_addr=127.0.0.1
qmp_mock_0_port=10500
qmp_mock_1_id=1
qmp_mock_1_addr=127.0.0.1
qmp_mock_1_port=10510

source "$rootdir/test/common/autotest_common.sh"
source "$testdir/vfiouser_common.sh"
source "$testdir/common.sh"

function cleanup() {
	killprocess $tgtpid
	killprocess $smapid
	killprocess $qmp_0_pid
	killprocess $qmp_1_pid
	rm -r /tmp/vfio-user/sma
}

trap "cleanup; exit 1" SIGINT SIGTERM EXIT

$rootdir/build/bin/spdk_tgt &
tgtpid=$!
waitforlisten $tgtpid

# Prepare the target
rpc_cmd bdev_null_create null0 100 4096
rpc_cmd bdev_null_create null1 100 4096

$rootdir/scripts/sma.py -c <(
	cat <<- EOF
		subsystems:
		  - name: 'vfiouser'
		    params:
		      root_path: '/tmp/vfio-user/sma'
		      hosts:
		        - id: ${qmp_mock_0_id}
		          bus: 'spdk_bus'
		          address: '${qmp_mock_0_addr}'
		          port: ${qmp_mock_0_port}
		        - id: ${qmp_mock_1_id}
		          bus: 'spdk_bus'
		          address: '${qmp_mock_1_addr}'
		          port: ${qmp_mock_1_port}
	EOF
) &
smapid=$!

# Wait until the SMA starts listening
sma_waitforlisten

$rootdir/test/sma/qmp_mock.py --address ${qmp_mock_0_addr} --port ${qmp_mock_0_port} &
qmp_0_pid=$!

$rootdir/test/sma/qmp_mock.py --address ${qmp_mock_1_addr} --port ${qmp_mock_1_port} &
qmp_1_pid=$!

# Wait until the QMP server starts listening
qmp_waitforlisten ${qmp_mock_0_addr} ${qmp_mock_0_port}
qmp_waitforlisten ${qmp_mock_1_addr} ${qmp_mock_1_port}

# Make sure a TCP transport has been created
rpc_cmd nvmf_get_transports --trtype VFIOUSER

# Create a couple of devices and verify them via RPC
devid_0_0=$(create_device ${qmp_mock_0_id} 1 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0)

devid_1_0=$(create_device ${qmp_mock_1_id} 1 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0)

devid_0_1=$(create_device ${qmp_mock_0_id} 2 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0)
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 2 0)
[[ "$devid_0_0" != "$devid_0_1" ]]

devid_1_1=$(create_device ${qmp_mock_1_id} 2 | jq -r '.id')
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0)
rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 2 0)
[[ "$devid_1_0" != "$devid_1_1" ]]

# Check that there are three subsystems (5 created above + discovery)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 5 ]]

# Verify the method is idempotent and sending the same gRPCs won't create new
# devices and will return the same IDs
tmp_0_0=$(create_device ${qmp_mock_0_id} 1 | jq -r '.id')
tmp_0_1=$(create_device ${qmp_mock_0_id} 2 | jq -r '.id')
tmp_1_0=$(create_device ${qmp_mock_1_id} 1 | jq -r '.id')
tmp_1_1=$(create_device ${qmp_mock_1_id} 2 | jq -r '.id')

[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 5 ]]
[[ "$tmp_0_0" == "$devid_0_0" ]]
[[ "$tmp_0_1" == "$devid_0_1" ]]
[[ "$tmp_1_0" == "$devid_1_0" ]]
[[ "$tmp_1_1" == "$devid_1_1" ]]

# Now remove both of them verifying via RPC
delete_device "$devid_0_0"
NOT rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 4 ]]

delete_device "$devid_1_0"
NOT rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 3 ]]

delete_device "$devid_0_1"
NOT rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 2 0)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 2 ]]

delete_device "$devid_1_1"
NOT rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 2 0)
[[ $(rpc_cmd nvmf_get_subsystems | jq -r '. | length') -eq 1 ]]

# Finally check that removing a non-existing device is also sucessful
delete_device "$devid_0_0"
delete_device "$devid_0_1"
delete_device "$devid_1_0"
delete_device "$devid_1_1"

# Check volume attach/detach
devid_0_0=$(create_device ${qmp_mock_0_id} 1 | jq -r '.id')
devid_0_1=$(create_device ${qmp_mock_0_id} 2 | jq -r '.id')
uuid_0=$(rpc_cmd bdev_get_bdevs -b null0 | jq -r '.[].uuid')

devid_1_0=$(create_device ${qmp_mock_1_id} 1 | jq -r '.id')
devid_1_1=$(create_device ${qmp_mock_1_id} 2 | jq -r '.id')
uuid_1=$(rpc_cmd bdev_get_bdevs -b null1 | jq -r '.[].uuid')

# Attach the volume to a first device on each host
attach_volume "$devid_0_0" "$uuid_0"
attach_volume "$devid_1_0" "$uuid_1"
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces[0].uuid') == "$uuid_0" ]]

[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces[0].uuid') == "$uuid_1" ]]

# Attach the same device again and see that it won't fail
attach_volume "$devid_0_0" "$uuid_0"
attach_volume "$devid_1_0" "$uuid_1"
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces[0].uuid') == "$uuid_0" ]]

[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces[0].uuid') == "$uuid_1" ]]

# Cross detach volumes and verify they not fail and have not been removed from the subsystems
detach_volume "$devid_0_0" "$uuid_1"
detach_volume "$devid_0_1" "$uuid_1"
detach_volume "$devid_1_0" "$uuid_0"
detach_volume "$devid_1_1" "$uuid_0"
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces[0].uuid') == "$uuid_0" ]]

[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces | length') -eq 1 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces[0].uuid') == "$uuid_1" ]]

# Detach volumes and verify they have been removed from the subsystems
detach_volume "$devid_0_0" "$uuid_0"
detach_volume "$devid_1_0" "$uuid_1"
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 1 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_0_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 1 0) | jq -r '.[0].namespaces | length') -eq 0 ]]
[[ $(rpc_cmd nvmf_get_subsystems $(get_nqn_from_params ${qmp_mock_1_id} 2 0) | jq -r '.[0].namespaces | length') -eq 0 ]]

# Detach and cross detach volumes again and verify it suceeds
detach_volume "$devid_0_0" "$uuid_1"
detach_volume "$devid_0_1" "$uuid_1"
detach_volume "$devid_1_0" "$uuid_0"
detach_volume "$devid_1_1" "$uuid_0"
detach_volume "$devid_0_0" "$uuid_0"
detach_volume "$devid_1_0" "$uuid_1"

cleanup
trap - SIGINT SIGTERM EXIT
