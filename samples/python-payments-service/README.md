# python-payments-service (sample repo A)

Tiny Flask + pytest service used as a **fixture** for Sentinel's analysis tools
(git_diff / ast_analyzer / dependency_graph) and the demo runs. Not production.

Import graph (drives dependency_graph blast radius):

```
app.api ──> app.auth.login ──> app.db
   └──────> app.payments.processor ──> app.auth.login, app.db
```

`app/auth/login.py` is the **sensitive (auth) module** and the Demo-2 SQL-injection
plant site — baseline here is a safe parameterized query.

Run: `pip install -r requirements.txt && pytest`
