"""Create/verify an independent audit seal. Secret must never be stored in repo."""
import argparse
import json
import os
from pathlib import Path
from app.auditseal import seal,verify
from scripts.quality import save_private


def main(argv=None):
    parser=argparse.ArgumentParser(description='Assina/verifica snapshot local de eventos e capturas (não autentica lei)')
    g=parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--create',type=Path,help='Criar arquivo novo privado (0600)')
    g.add_argument('--verify',type=Path,help='Conferir selo e conteúdo atual do SQLite')
    args=parser.parse_args(argv)
    key=os.environ.get('DIREITO_AUDIT_HMAC_KEY')
    if not key or len(key.encode())<32:
        parser.error('Forneça DIREITO_AUDIT_HMAC_KEY com pelo menos 32 bytes de entropia adequada')
    if args.create:
        save_private(args.create,seal(key=key))
        print('Selo de auditoria salvo em arquivo privado. Proteja a chave fora do portal.')
    else:
        try:
            data=json.loads(args.verify.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:
            parser.error(f'Não foi possível ler o selo: {exc}')
        if not verify(data,key=key):
            parser.exit(1,'FALHA: selo ou banco atual não corresponde ao registro assinado.\n')
        print('VERIFICADO: snapshot atual corresponde ao registro HMAC assinado.')

if __name__=='__main__':
    main()
