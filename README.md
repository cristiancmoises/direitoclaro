# Direito Claro

Portal gratuito para pesquisar normas brasileiras, consultar fontes oficiais e entender situações do dia a dia.

Acesse: [direito.securityops.co](https://direito.securityops.co) · [direito.securityops.com.br](https://direito.securityops.com.br)

**Prévia educativa v0.25.0, ainda em revisão.** O catálogo contém 117 referências e 25 guias ilustrativos, sem revisão jurídica independente. Nenhum artigo oficial foi publicado no portal. O conteúdo não substitui orientação jurídica; a liberação de produção permanece **NO_GO**.

![Página inicial](docs/images/home.png)

Busca por “garantia”:

![Resultados da busca](docs/images/search.png)

## Executar localmente

Requer Python 3.12 ou superior, com SQLite FTS5. Na raiz do projeto:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-bootstrap.lock
python -m pip install --require-hashes -r requirements-public.lock
mkdir -p local-data
DIREITO_DEPLOYMENT_MODE=development \
DIREITO_DATABASE="$PWD/local-data/demo.sqlite3" \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --no-access-log
```

Abra `http://127.0.0.1:8080`. O ambiente local inicia o catálogo e os exemplos distribuídos com o código.

## Testes e integridade

```sh
python -m pip install -r requirements-verification.txt
python -m pytest -q
sha256sum -c SHA256SUMS
```

Avaliação manual de acessibilidade e revisão jurídica profissional continuam pendentes.

## Código e licença

[SecurityOps .com.br](https://git.securityops.com.br/cristiancmoises/direitoclaro) · [SecurityOps .co](https://git.securityops.co/cristiancmoises/direitoclaro) · [GitHub](https://github.com/cristiancmoises/direitoclaro) · [Codeberg](https://codeberg.org/berkeley/direitoclaro)

Licença [MIT](LICENSE). Desenvolvido por [Cristian Cezar Moisés](https://linkedin.com/in/cristiancezarmoises).
