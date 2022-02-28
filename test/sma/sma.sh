#!/usr/bin/env bash

testdir=$(readlink -f "$(dirname "$0")")
rootdir=$(readlink -f "$testdir/../..")

source "$rootdir/test/common/autotest_common.sh"

run_test "sma_nvmf_tcp" $testdir/nvmf_tcp.sh
run_test "sma_vfiouser_mock" $testdir/vfiouser_mock.sh
run_test "sma_plugins" $testdir/plugins.sh
