function sma_waitforlisten() {
	local sma_addr=${1:-127.0.0.1}
	local sma_port=${2:-8080}

	for ((i = 0; i < 5; i++)); do
		if nc -z $sma_addr $sma_port; then
			return 0
		fi
		sleep 1s
	done
	return 1
}

function qmp_waitforlisten() {
	local qmp_addr=${1:-127.0.0.1}
	local qmp_port=${2:-45556}

	for ((i = 0; i < 5; i++)); do
		if nc -z $qmp_addr $qmp_port; then
			return 0
		fi
		sleep 1s
	done
	return 1
}
