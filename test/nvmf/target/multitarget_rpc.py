#!/usr/bin/env python3

# Not for use in production. Please see the changelog for v19.10.

from spdk.rpc.client import print_dict, JSONRPCException

import logging
import argparse
import spdk.rpc as rpc
import sys
import shlex

try:
    from shlex import quote
except ImportError:
    from pipes import quote

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='NVMe-oF RPC command line interface. NOTE: spdk/scripts/ is expected in PYTHONPATH')
    parser.add_argument('-s', dest='server_addr',
                        help='RPC domain socket path or IP address', default='/var/tmp/spdk.sock')
    parser.add_argument('-p', dest='port',
                        help='RPC port number (if server_addr is IP address)',
                        default=5260, type=int)
    parser.add_argument('-t', dest='timeout',
                        help='Timeout as a floating point number expressed in seconds waiting for response. Default: 60.0',
                        default=60.0, type=float)
    parser.add_argument('-v', dest='verbose', action='store_const', const="INFO",
                        help='Set verbose mode to INFO', default="ERROR")
    parser.add_argument('--verbose', dest='verbose', choices=['DEBUG', 'INFO', 'ERROR'],
                        help="""Set verbose level. """)
    subparsers = parser.add_subparsers(help='RPC methods')

    def nvmf_create_target(args):
        print_dict(rpc.nvmf.nvmf_create_target(args.client,
                                               name=args.name,
                                               max_subsystems=args.max_subsystems))

    p = subparsers.add_parser('nvmf_create_target', help='Create a new NVMe-oF target')
    p.add_argument('-n', '--name', help='Target name (unique to application)', type=str, required=True)
    p.add_argument('-s', '--max-subsystems', help='Max number of NVMf subsystems defaults to SPDK_NVMF_DEFAULT_MAX_SUBSYSTEMS',
                   type=int, required=False)
    p.set_defaults(func=nvmf_create_target)

    def nvmf_delete_target(args):
        print_dict(rpc.nvmf.nvmf_delete_target(args.client,
                                               name=args.name))

    p = subparsers.add_parser('nvmf_delete_target', help='Destroy the given NVMe-oF Target')
    p.add_argument('-n', '--name', help='Target name (unique to application)', type=str, required=True)
    p.set_defaults(func=nvmf_delete_target)

    def nvmf_get_targets(args):
        print_dict(rpc.nvmf.nvmf_get_targets(args.client))

    p = subparsers.add_parser('nvmf_get_targets', help='Get the list of NVMe-oF Targets')
    p.set_defaults(func=nvmf_get_targets)

    def call_rpc_func(args):
        try:
            args.func(args)
        except JSONRPCException as ex:
            print(ex.message)
            exit(1)

    def execute_script(parser, client, fd):
        for rpc_call in map(str.rstrip, fd):
            if not rpc_call.strip():
                continue
            args = parser.parse_args(shlex.split(rpc_call))
            args.client = client
            call_rpc_func(args)

    args = parser.parse_args()
    args.client = rpc.client.JSONRPCClient(args.server_addr, args.port, args.timeout, log_level=getattr(logging, args.verbose.upper()))
    if hasattr(args, 'func'):
        call_rpc_func(args)
    elif sys.stdin.isatty():
        # No arguments and no data piped through stdin
        parser.print_help()
        exit(1)
    else:
        execute_script(parser, args.client, sys.stdin)
