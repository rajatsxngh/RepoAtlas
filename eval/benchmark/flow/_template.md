# repo flow questions

- Written by: <your name>
- Reviewed by: pending

## repo-flow-001

**Question:** How does a request reach the database?

**Must cite:**
- `py:src/pkg/app.py:create_app:function`
- `py:src/pkg/routes.py:Router.dispatch:method`
- `py:src/pkg/db.py:Session.execute:method`

**Reference outline:**
1. `create_app` registers the routes on a `Router`.
2. `Router.dispatch` matches the path and calls the handler.
3. The handler runs its query through `Session.execute`.
