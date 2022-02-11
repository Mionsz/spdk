#!/usr/bin/env python3

from argparse import ArgumentParser
import importlib
import logging
import os
import signal
import sys
import threading
import time
import yaml

sys.path.append(os.path.dirname(__file__) + '/../python')

import spdk.sma as sma               # noqa
import spdk.rpc.client as rpcclient  # noqa


def parse_config(path):
    if path is None:
        return {}
    with open(path, 'r') as cfgfile:
        config = yaml.load(cfgfile, Loader=yaml.FullLoader)
        return {**config} if config is not None else {}


def parse_argv():
    parser = ArgumentParser(description='Storage Management Agent command line interface')
    parser.add_argument('--address', '-a', help='IP address to listen on')
    parser.add_argument('--socket', '-s', help='SPDK RPC socket')
    parser.add_argument('--port', '-p', type=int, help='IP port to listen on')
    parser.add_argument('--config', '-c', help='Path to config file')
    parser.add_argument('--priv-key', help='The PEM-encoded private key as a byte string')
    parser.add_argument('--cert-chain', help='The PEM-encoded certificate chain as a byte string')
    parser.add_argument('--root-cert', help='The PEM-encoded root certificates as a byte string')
    defaults = {'address': 'localhost',
                'socket': '/var/tmp/spdk.sock',
                'port': 8080,
                'priv_key': None,
                'cert_chain': None,
                'root_cert': None}
    # Merge the default values, config file, and the command-line
    args = vars(parser.parse_args())
    config = parse_config(args.get('config'))
    for argname, argvalue in defaults.items():
        if args.get(argname) is not None:
            if config.get(argname) is not None:
                logging.info(f'Overriding "{argname}" value from command-line')
            config[argname] = args[argname]
        if config.get(argname) is None:
            config[argname] = argvalue
    return config


def get_build_client(sock):
    def build_client():
        return rpcclient.JSONRPCClient(sock)

    return build_client


def register_subsystems(agent, subsystems, config):
    for subsys_config in config.get('subsystems') or []:
        name = subsys_config.get('name')
        subsys = next(filter(lambda s: s.name == name, subsystems), None)
        if subsys is None:
            logging.error(f'Couldn\'t find subsystem: {name}')
            sys.exit(1)
        logging.info(f'Registering subsystem: {name}')
        subsys.init(subsys_config.get('params'))
        agent.register_subsystem(subsys)


def load_plugins(plugins, client):
    subsystems = []
    for plugin in plugins:
        module = importlib.import_module(plugin)
        for subsystem in getattr(module, 'subsystems', []):
            logging.debug(f'Loading external subsystem: {plugin}.{subsystem.__name__}')
            subsystems.append(subsystem(client))
    return subsystems


def wait_for_listen(client, timeout):
    start = time.monotonic()
    while True:
        try:
            with client() as _client:
                _client.call('rpc_get_methods')
            # If we got here, the process is responding to RPCs
            break
        except rpcclient.JSONRPCException:
            logging.debug('The SPDK process is not responding for {}s'.format(
                          int(time.monotonic() - start)))

        if time.monotonic() > start + timeout:
            logging.error('Timed out while waiting for SPDK process to respond')
            sys.exit(1)
        time.sleep(1)


def run(agent):
    event = threading.Event()

    def signal_handler(signum, frame):
        event.set()

    for signum in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(signum, signal_handler)

    agent.start()
    event.wait()
    agent.stop()


if __name__ == '__main__':
    logging.basicConfig(level=os.environ.get('SMA_LOGLEVEL', 'WARNING').upper())

    config = parse_argv()
    client = get_build_client(config['socket'])

    # Wait until the SPDK process starts responding to RPCs
    wait_for_listen(client, timeout=60.0)
    agent = sma.StorageManagementAgent(config['address'], config['port'], config['root_cert'],
                                       config['priv_key'], config['cert_chain'])

    subsystems = [sma.NvmfTcpSubsystem(client), sma.NvmfVfioSubsystem(client)]
    subsystems += load_plugins(config.get('plugins') or [], client)
    subsystems += load_plugins(filter(None, os.environ.get('SMA_PLUGINS', '').split(':')),
                               client)
    register_subsystems(agent, subsystems, config)
    run(agent)
