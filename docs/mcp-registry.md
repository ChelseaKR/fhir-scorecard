# MCP registry listing

Last verified: 2026-10-17. Recheck when `server.json` changes, when a release is
cut, and before any `mcp-publisher publish`. The registry's schema and rules move;
the sources are listed at the end.

## Where the server is distributed today

`fhir-scorecard mcp --site <dir>` is a subcommand of the `fhir-scorecard`
package. It speaks MCP over stdio and reads only the dataset files under
`<dir>/api/`. It is distributed as:

- a signed `vX.Y.Z` tag, installable with
  `uvx --from "git+https://github.com/ChelseaKR/fhir-scorecard@v0.2.0" fhir-scorecard mcp --site <dir>`
- the sdist and wheel attached, with build provenance, to the GitHub Release for
  that tag (`.github/workflows/release.yml`)

It is **not** on PyPI (`https://pypi.org/pypi/fhir-scorecard/json` returned 404 on
2026-10-17) and not in the MCP registry. `<dir>` is a site built by
`fhir-scorecard grade`; the dataset is not bundled in the package.

## What `server.json` says, and why it has no `packages`

The registry installs a server from a package on npm, PyPI, NuGet, a container
registry or an MCPB bundle on GitHub Releases. None exists for this project, so
`server.json` lists none. Earlier it declared a PyPI package that has never been
uploaded, which a client would have tried to run and failed. It also passed the
subcommand as a `runtimeArguments` entry (those are flags for the runner, such as
`uvx`; arguments for the program are `packageArguments`), used an older field
spelling, and carried a description over the registry's 100-character limit.

As it stands the file validates against the 2025-12-11 schema and describes the
server by name, version and repository. Published as is, the registry entry is a
pointer to this repository, with nothing a client can install from it.
`version` is the released version and has to be updated with each release.

Check it after any edit:

```bash
curl -sSfo /tmp/server.schema.json \
  https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
uv run --with jsonschema python -c '
import json, sys
from jsonschema import Draft7Validator
schema = json.load(open("/tmp/server.schema.json"))
doc = json.load(open("server.json"))
errors = list(Draft7Validator(schema).iter_errors(doc))
for e in errors:
    print(list(e.path), e.message)
sys.exit(1 if errors else 0)'
```

## Publishing the entry as it is

1. Install `mcp-publisher` from the registry's releases and run
   `mcp-publisher login github` as the `ChelseaKR` account. The name
   `io.github.chelseakr/fhir-scorecard` is in the `io.github.<login>/` namespace,
   which that login proves.
2. `mcp-publisher publish` from the repository root.

The server name is lower case while the GitHub login is `ChelseaKR`. Whether the
registry compares the two case-insensitively was not verified. If publishing is
refused on the namespace, the name has to change to match, in `server.json` and in
`SERVER_INFO` in `src/fhir_scorecard/mcp.py`.

## Listing an installable package later

Publish `fhir-scorecard` to PyPI first (the Trusted Publishing setup is the
project's to make; it is not described here). Then:

1. Add this line to `README.md`, which becomes the PyPI description. The registry
   looks for it to confirm the package belongs to this server name:

   ```markdown
   <!-- mcp-name: io.github.chelseakr/fhir-scorecard -->
   ```

2. Add a `packages` entry whose `version` is the version actually on PyPI:

   ```json
   "packages": [
     {
       "registryType": "pypi",
       "identifier": "fhir-scorecard",
       "version": "0.2.0",
       "runtimeHint": "uvx",
       "transport": { "type": "stdio" },
       "packageArguments": [
         { "type": "positional", "value": "mcp" },
         {
           "type": "named",
           "name": "--site",
           "description": "Directory written by `fhir-scorecard grade`, containing api/index.json",
           "format": "filepath",
           "default": "site"
         }
       ]
     }
   ]
   ```

3. Re-run the schema check above and publish a new `server.json` version.

Do not add the `packages` entry before the upload: the registry verifies the
package at publish time, and a client that reads the entry will try to install it.

## Sources

- `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
- `docs/reference/server-json/` and `docs/modelcontextprotocol-io/package-types.mdx`
  in `github.com/modelcontextprotocol/registry`, read 2026-10-17
