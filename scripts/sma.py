#!/usr/bin/env python3

from argparse import ArgumentParser
import logging
import os
import sys

sys.path.append(os.path.dirname(__file__) + '/../python')

import spdk.sma as sma                      # noqa
from spdk.rpc.client import JSONRPCClient   # noqa


def build_client():
    return JSONRPCClient('/var/tmp/spdk.sock')


def register_subsystem(agent, subsystem):
    subsystem.init(None)
    agent.register_subsystem(subsystem)


if __name__ == '__main__':
    logging.basicConfig(level=os.environ.get('SMA_LOGLEVEL', 'WARNING').upper())
    agent = sma.StorageManagementAgent('localhost', 8080)
    register_subsystem(agent, sma.NvmfTcpSubsystem(build_client))
    agent.run()
