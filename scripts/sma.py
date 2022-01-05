#!/usr/bin/env python3

from argparse import ArgumentParser
import logging
import os
import sys

sys.path.append(os.path.dirname(__file__) + '/../lib/python')

import spdk.sma as sma                      # noqa
from spdk.rpc.client import JSONRPCClient   # noqa


def build_client():
    return JSONRPCClient('/var/tmp/spdk.sock')


if __name__ == '__main__':
    logging.basicConfig(level=os.environ.get('SMA_LOGLEVEL', 'WARNING').upper())
    agent = sma.StorageManagementAgent(build_client, 'localhost', 50051)
    agent.register_subsystem(sma.NvmfTcpSubsystem)
    agent.run()
