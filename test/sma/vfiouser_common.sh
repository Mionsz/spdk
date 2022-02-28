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
	local host_id=${1:-0}
	local pfid=${2:-1}
	local vfid=${3:-0}

	"$rootdir/scripts/sma-client.py" <<- EOF
		{
			"method": "CreateDevice",
			"params": {
				"type": "nvme",
				"params": {
					"@type": "/sma.nvme.CreateDeviceParameters",
					"host_id": "$host_id",
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
	local host_id=${1:-0}
	local pfid=${2:-1}
	local vfid=${3:-0}

	echo "nqn.2016-06.io.spdk:vfiouser-${host_id}-${pfid}-${vfid}"
}
