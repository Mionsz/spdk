#!/usr/bin/env bash

testdir=$(readlink -f "$(dirname "$0")")
rootdir=$(readlink -f "$testdir/../..")

source "$rootdir/test/common/autotest_common.sh"
source "$testdir/common.sh"

function cleanup() {
	killprocess $tgtpid
	killprocess $smapid
}

function create_device() {
	"$rootdir/scripts/sma-client.py" <<- EOF
		{
			"method": "CreateDevice",
			"params": {
				"type": "$1"
			}
		}
	EOF
}

trap 'cleanup; exit 1' SIGINT SIGTERM EXIT

$rootdir/build/bin/spdk_tgt &
tgtpid=$!

# First check a single plugin with both its subsystems enabled in the config
PYTHONPATH=$testdir/plugins $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		plugins:
		  - 'plugin1'
		subsystems:
		  - name: 'plugin1-subsys1'
		  - name: 'plugin1-subsys2'
	EOF
) &
smapid=$!
# Wait for a while to make sure SMA starts listening
sma_waitforlisten

[[ $(create_device protocol1 | jq -r '.id') == 'protocol1:plugin1-subsys1' ]]
[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin1-subsys2' ]]

killprocess $smapid

# Check that it's possible to enable only a single subsystem from a plugin
PYTHONPATH=$testdir/plugins $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		plugins:
		  - 'plugin1'
		subsystems:
		  - name: 'plugin1-subsys2'
	EOF
) &
smapid=$!
sma_waitforlisten

[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin1-subsys2' ]]
NOT create_device protocol1

killprocess $smapid

# Load two different plugins, but only enable subsystems from one of them
PYTHONPATH=$testdir/plugins $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		plugins:
		  - 'plugin1'
		  - 'plugin2'
		subsystems:
		  - name: 'plugin1-subsys1'
		  - name: 'plugin1-subsys2'
	EOF
) &
smapid=$!
sma_waitforlisten

[[ $(create_device protocol1 | jq -r '.id') == 'protocol1:plugin1-subsys1' ]]
[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin1-subsys2' ]]

killprocess $smapid

# Check the same but take subsystems defined by the other plugin
PYTHONPATH=$testdir/plugins $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		plugins:
		  - 'plugin1'
		  - 'plugin2'
		subsystems:
		  - name: 'plugin2-subsys1'
		  - name: 'plugin2-subsys2'
	EOF
) &
smapid=$!
sma_waitforlisten

[[ $(create_device protocol1 | jq -r '.id') == 'protocol1:plugin2-subsys1' ]]
[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin2-subsys2' ]]

killprocess $smapid

# Now pick a subsystem from each plugin
PYTHONPATH=$testdir/plugins $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		plugins:
		  - 'plugin1'
		  - 'plugin2'
		subsystems:
		  - name: 'plugin1-subsys1'
		  - name: 'plugin2-subsys2'
	EOF
) &
smapid=$!
sma_waitforlisten

[[ $(create_device protocol1 | jq -r '.id') == 'protocol1:plugin1-subsys1' ]]
[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin2-subsys2' ]]

killprocess $smapid

# Check the same, but register plugins via a env var
PYTHONPATH=$testdir/plugins SMA_PLUGINS=plugin1:plugin2 $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		subsystems:
		  - name: 'plugin1-subsys1'
		  - name: 'plugin2-subsys2'
	EOF
) &
smapid=$!
sma_waitforlisten

[[ $(create_device protocol1 | jq -r '.id') == 'protocol1:plugin1-subsys1' ]]
[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin2-subsys2' ]]

killprocess $smapid

# Finally, register one plugin in a config and the other through env var
PYTHONPATH=$testdir/plugins SMA_PLUGINS=plugin1 $rootdir/scripts/sma.py -c <(
	cat <<- EOF
		plugins:
		  - 'plugin2'
		subsystems:
		  - name: 'plugin1-subsys1'
		  - name: 'plugin2-subsys2'
	EOF
) &
smapid=$!
sma_waitforlisten

[[ $(create_device protocol1 | jq -r '.id') == 'protocol1:plugin1-subsys1' ]]
[[ $(create_device protocol2 | jq -r '.id') == 'protocol2:plugin2-subsys2' ]]

cleanup
trap - SIGINT SIGTERM EXIT
