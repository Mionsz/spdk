#!/usr/bin/env python3

from argparse import ArgumentParser
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
    return parser.parse_args()


def get_build_client(sock):
    def build_client():
        return JSONRPCClient(sock)

    return build_client


if __name__ == '__main__':
    argv = parse_argv()
    logging.basicConfig(level=os.environ.get('SMA_LOGLEVEL', 'WARNING').upper())
    agent = sma.StorageManagementAgent(get_build_client(argv.sock), argv.address,
                                       argv.port, argv.priv_key, argv.cert_chain)
    agent.register_subsystem(sma.NvmfTcpSubsystem)
    agent.run()
