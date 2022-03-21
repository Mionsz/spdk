function qmp_waitforlisten() {
	local qmp_addr=${1:-127.0.0.1}
	local qmp_port=${2:-10500}

	for ((i = 0; i < 5; i++)); do
		if nc -z $qmp_addr $qmp_port; then
			return 0
		fi
		sleep 1s
	done
	return 1
}

function create_device() {
	local pfid=${1:-1}
	local vfid=${2:-0}

	"$rootdir/scripts/sma-client.py" <<- EOF
		{
			"method": "CreateDevice",
			"params": {
				"nvme": {
					"physical_id": "$pfid",
					"virtual_id": "$vfid"
				}
			}
		}
	EOF
}

function delete_device() {
	"$rootdir/scripts/sma-client.py" <<- EOF
		{
			"method": "DeleteDevice",
			"params": {
				"id": "$1"
			}
		}
	EOF
}

function attach_volume() {
	"$rootdir/scripts/sma-client.py" <<- EOF
		{
			"method": "AttachVolume",
			"params": {
				"device_id": "$1",
				"volume_guid": "$2"
			}
		}
	EOF
}

function detach_volume() {
	"$rootdir/scripts/sma-client.py" <<- EOF
		{
			"method": "DetachVolume",
			"params": {
				"device_id": "$1",
				"volume_guid": "$2"
			}
		}
	EOF
}

function get_nqn_from_params() {
	local pfid=${1:-1}
	local vfid=${2:-0}

	echo "nqn.2016-06.io.spdk:vfiouser-${pfid}-${vfid}"
}

function vm_count_nvme() {
	vm_exec $1 grep -l SPDK /sys/class/nvme/*/model | wc -l
}

function vm_check_subsys_volume() {
	local vm_id=$1
	local nqn=$2
	local uuid=$3

	NVME="$(vm_exec $vm_id grep -l "$nqn" /sys/class/nvme/*/subsysnqn | awk -F/ '{print $5}')"
	if [[ -z "$NVME" ]]; then
		error "FAILED no NVMe on vm=$vm_id with nqn=$nqn"
		return 1
	fi

	VOLUUID="$(vm_exec $vm_id grep -l "$uuid" /sys/class/nvme/$NVME/nvme*/uuid)"
	if [[ -z "$VOLUUID" ]]; then
		return 1
	fi
}

function vm_check_subsys_nqn() {
	SUBSYSNQN="$(vm_exec $1 grep -l "$2" /sys/class/nvme/*/subsysnqn)"
	if [[ -z "$SUBSYSNQN" ]]; then
		error "FAILED no NVMe on vm=$1 with nqn=$2"
		return 1
	fi
}
