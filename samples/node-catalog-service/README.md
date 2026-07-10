# node-catalog-service (sample repo B)

Tiny Express + Jest service used as a **fixture** for Sentinel's JS/TS analysis tools
(git_diff / ast_analyzer / dependency_graph / test_mapper / test_runner) and the demo runs.
Not production. Mirrors `samples/python-payments-service`'s shape (auth + a domain module + API).

Import graph (drives dependency_graph blast radius):

```
src/index.js ──> src/auth/login.js ──> (none)
     └────────> src/catalog/processor.js ──> src/auth/login.js
```

`src/auth/login.js` is the **sensitive (auth) module** — baseline here is safe.

Run: `npm install && npm test`
