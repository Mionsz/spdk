#!/usr/bin/env python3

from argparse import ArgumentParser
import importlib
import logging
import os
import signal
import sys
import threading
import yaml

sys.path.append(os.path.dirname(__file__) + '/../python')

import spdk.sma as sma                      # noqa
from spdk.rpc.client import JSONRPCClient   # noqa


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
    defaults = {'address': 'localhost',
                'socket': '/var/tmp/spdk.sock',
                'port': 8080}
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
        return JSONRPCClient(sock)

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

    agent = sma.StorageManagementAgent(config['address'], config['port'])

    subsystems = [sma.NvmfTcpSubsystem(client)]
    subsystems += load_plugins(config.get('plugins') or [], client)
    subsystems += load_plugins(filter(None, os.environ.get('SMA_PLUGINS', '').split(':')),
                               client)
    register_subsystems(agent, subsystems, config)
    run(agent)
