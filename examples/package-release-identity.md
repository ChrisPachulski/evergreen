# Package release identity

This example uses only checked-in evidence. It does not query a registry or deployed site.

## One package stream

The package's source of truth is `package.json`, whose version is `1.4.0`. The implementation of
`tool --version` reads that field, so its version-reporting CLI output also resolves to `1.4.0`.

The living README badge, installed-command example, and generated API header still show `1.3.2`.
Those are linked claims about the same package release.

Expected: release_identity_drift — reconcile the checked-in badge/example and the API-doc source
with `package.json`; regenerate owned output rather than hand-editing it.

Expected: external release state unverified — no package registry or deployed docs endpoint was
queried, so this evidence cannot establish which version is public.

## Independent package

A workspace plugin has its own manifest and release policy. It is an independent release stream;
the `1.4.0` package correction does not justify changing the plugin's version.
