#!/usr/bin/env python3

from argparse import ArgumentParser
import grpc
import google.protobuf.json_format as json_format
import json
import os
import sys

sys.path.append(os.path.dirname(__file__) + '/../lib/python')

import spdk.sma.proto.sma_pb2 as sma_pb2                        # noqa
import spdk.sma.proto.sma_pb2_grpc as sma_pb2_grpc              # noqa


class Client:
    def __init__(self, addr, port, root_cert=None, priv_key=None, cert_chain=None):
        self._service = sma_pb2.DESCRIPTOR.services_by_name['StorageManagementAgent']
        self.addr = addr
        self.port = port
        if root_cert is not None:
            with open(root_cert, 'rb') as f:
                root_certificate = f.read()
            with open(priv_key, 'rb') as f:
                private_key = f.read()
            with open(cert_chain, 'rb') as f:
                certificate_chain = f.read()
            self.creds = grpc.ssl_channel_credentials(root_certificate,
                                                      private_key, certificate_chain)
        else:
            self.creds = None

    def _get_message_type(self, descriptor):
        return getattr(sma_pb2, descriptor.name)

    def _get_method_types(self, method_name):
        method = self._service.methods_by_name.get(method_name)
        return (self._get_message_type(method.input_type),
                self._get_message_type(method.output_type))

    def _get_channel(self):
        if self.creds is not None:
            return grpc.secure_channel(f'{self.addr}:{self.port}', creds)
        else:
            return grpc.insecure_channel(f'{self.addr}:{self.port}')

    def call(self, method, params):
        stub = sma_pb2_grpc.StorageManagementAgentStub(self._get_channel())
        func = getattr(stub, method)
        input, output = self._get_method_types(method)
        response = func(request=json_format.ParseDict(params, input()))
        return json_format.MessageToDict(response,
                                         preserving_proto_field_name=True)


def parse_argv():
    parser = ArgumentParser(description='Storage Management Agent client')
    parser.add_argument('--address', '-a', default='localhost',
                        help='IP address of SMA instance to connect to')
    parser.add_argument('--port', '-p', default=50051, type=int,
                        help='Port number of SMA instance to connect to')
    parser.add_argument('--priv-key', '-k',
                        help='The PEM-encoded private key as a byte string')
    parser.add_argument('--cert-chain', '-c',
                        help='The PEM-encoded certificate chain as a byte string')
    parser.add_argument('--root-cert', '-r',
                        help='The PEM-encoded root certificates as a byte string')
    return parser.parse_args()


def main(args):
    argv = parse_argv()
    client = Client(argv.address, argv.port, argv.root_cert, argv.priv_key, argv.cert_chain)
    request = json.loads(sys.stdin.read())
    result = client.call(request['method'], request.get('params', {}))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main(sys.argv[1:])
