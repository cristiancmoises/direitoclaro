"""Sign private command requests or an off-host access policy."""
import argparse
import base64
import getpass
import json
from pathlib import Path
import warnings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.access_control import (
    OPERATION_ROLES, POLICY_SCHEMA, digest, load_policy, read_private_bytes, read_private_json,
    request_payload,
)
from app.publication_boundary import require_private_process
from app.signed_approvals import canonical, fingerprint, public_bytes
from scripts.quality import save_private


def _load_encrypted_key(path):
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        passphrase=getpass.getpass('Senha da chave privada Ed25519: ').encode('utf-8')
    key=serialization.load_pem_private_key(read_private_bytes(path,16*1024),password=passphrase)
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError('Apenas chaves Ed25519 são aceitas')
    return key


def _signature(key, document):
    return base64.b64encode(key.sign(canonical(document))).decode('ascii')


def main(argv=None):
    require_private_process()
    parser=argparse.ArgumentParser(description='Autorizações privadas de operações com Ed25519')
    sub=parser.add_subparsers(dest='command',required=True)
    policy=sub.add_parser('sign-policy',help='Executar no host separado que guarda a chave raiz')
    policy.add_argument('--policy-json',required=True,type=Path)
    policy.add_argument('--private-key',required=True,type=Path)
    policy.add_argument('--out',required=True,type=Path)
    operation=sub.add_parser('sign-operation',help='Assinar o comando privado já conferido pelo titular')
    operation.add_argument('--operation',required=True,choices=sorted(OPERATION_ROLES))
    operation.add_argument('--actor',required=True)
    operation.add_argument('--command-json',required=True,type=Path)
    operation.add_argument('--private-key',required=True,type=Path)
    operation.add_argument('--out',required=True,type=Path)
    sub.add_parser('check-policy',help='Verificar assinatura e configuração externa da política')
    args=parser.parse_args(argv)
    if args.command=='check-policy':
        revision,identities,_=load_policy()
        print(json.dumps({'policy_revision':revision,'accounts':len(identities),
                          'professional_credentials_verified':0,'publication_authorized':False}))
        return
    if args.command=='sign-policy':
        document=read_private_json(args.policy_json)
        if not isinstance(document,dict) or document.get('schema')!=POLICY_SCHEMA:
            parser.error('Esquema da política privada inválido')
        key=_load_encrypted_key(args.private_key)
        raw=public_bytes(key)
        save_private(args.out,{'policy':document,'root_public_key_base64':base64.b64encode(raw).decode(),
                               'signature_base64':_signature(key,document)})
        print(json.dumps({'root_public_key_sha256':fingerprint(raw),'publication_authorized':False}))
        return
    command=read_private_json(args.command_json)
    revision,identities,policy=load_policy()
    account=identities.get(args.actor)
    if not account or OPERATION_ROLES[args.operation] not in account['roles']:
        parser.error('Conta sem o papel exigido na política confiável')
    key=_load_encrypted_key(args.private_key)
    if fingerprint(public_bytes(key))!=account['fingerprint']:
        parser.error('A chave não corresponde à conta na política confiável')
    payload=request_payload(args.operation,args.actor,command,revision,digest(policy))
    save_private(args.out,{'payload':payload,'signature_base64':_signature(key,payload)})


if __name__=='__main__':
    main()
