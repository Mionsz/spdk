#!/usr/bin/env python3

from argparse import ArgumentParser
import importlib
import logging
import os
import sys

sys.path.append(os.path.dirname(__file__) + '/../lib/python')

import spdk.sma as sma                      # noqa
from spdk.rpc.client import JSONRPCClient   # noqa


def parse_argv():
    parser = ArgumentParser(description='Storage Management Agent command line interface')
    parser.add_argument('--address', '-a', default='localhost',
                        help='IP address to listen on')
    parser.add_argument('--sock', '-s', default='/var/tmp/spdk.sock',
                        help='SPDK RPC socket')
    parser.add_argument('--port', '-p', default=50051, type=int,
                        help='IP port to listen on')
    parser.add_argument('--priv-key', '-k',
                        help='The PEM-encoded private key as a byte string')
    parser.add_argument('--cert-chain', '-c',
                        help='The PEM-encoded certificate chain as a byte string')
    parser.add_argument('--root-cert', '-r', default=None, dest='root_cert',
                        help='The PEM-encoded root certificates as a byte string')
    return parser.parse_args()


def get_build_client(sock):
    def build_client():
        return JSONRPCClient(sock)

    return build_client


def load_plugins(sma, plugins):
    for plugin in plugins:
        module = importlib.import_module(plugin)
        for subsystem in getattr(module, 'subsystems', []):
            logging.debug(f'Loading external subsystem: {plugin}.{subsystem.__name__}')
            sma.register_subsystem(subsystem)


if __name__ == '__main__':
    argv = parse_argv()
    logging.basicConfig(level=os.environ.get('SMA_LOGLEVEL', 'WARNING').upper())
    agent = sma.StorageManagementAgent(get_build_client(argv.sock), argv.address,
                                       argv.port, argv.root_cert, argv.priv_key, argv.cert_chain)
    agent.register_subsystem(sma.NvmfTcpSubsystem)
    agent.register_subsystem(sma.NvmfVfioSubsystem)
    load_plugins(agent, filter(None, os.environ.get('SMA_PLUGINS', '').split(':')))
    agent.run()
