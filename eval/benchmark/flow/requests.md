# requests flow questions

- Written by: Claude (AI draft from reading the source at the pinned commit; needs owner review)
- Reviewed by: pending

## requests-flow-001

**Question:** How does a call to `requests.get(url)` turn into an HTTP request on a urllib3 connection and come back as a `Response`?

**Must cite:**
- `py:src/requests/api.py:get:function`
- `py:src/requests/api.py:request:function`
- `py:src/requests/sessions.py:Session.request:method`
- `py:src/requests/sessions.py:Session.prepare_request:method`
- `py:src/requests/sessions.py:Session.send:method`
- `py:src/requests/sessions.py:Session.get_adapter:method`
- `py:src/requests/adapters.py:HTTPAdapter.send:method`
- `py:src/requests/adapters.py:HTTPAdapter.build_response:method`

**Reference outline:**
1. The module-level `get` forwards to the module-level `request` with the method name "get".
2. `request` opens a throwaway `Session` in a `with` block and delegates to `Session.request`.
3. `Session.request` builds a `Request`, turns it into a `PreparedRequest` via `Session.prepare_request` (which merges session headers, params, auth, cookies and hooks), merges environment settings, and calls `Session.send`.
4. `Session.send` picks the transport with `Session.get_adapter`, which returns the first mounted adapter whose URL prefix matches (the session mounts an `HTTPAdapter` for "http://" and "https://" at construction).
5. `HTTPAdapter.send` gets a pooled urllib3 connection for the request's TLS and proxy settings, adds headers, resolves the timeout, and calls `urlopen` with redirects disabled, translating urllib3 errors into requests exceptions.
6. `HTTPAdapter.build_response` wraps the urllib3 response in a `Response` (status, case-insensitive headers, encoding, URL, cookies, and a back-reference to the request and adapter).
7. Back in `Session.send`, response hooks run, cookies are stored on the session, redirects are followed if allowed, and the body is read unless streaming was requested.

## requests-flow-002

**Question:** When the server answers with a redirect, how does `Session.send` follow it, and how are the HTTP method and the Authorization header adjusted for the next request?

**Must cite:**
- `py:src/requests/sessions.py:Session.send:method`
- `py:src/requests/sessions.py:SessionRedirectMixin.resolve_redirects:method`
- `py:src/requests/sessions.py:SessionRedirectMixin.get_redirect_target:method`
- `py:src/requests/sessions.py:SessionRedirectMixin.rebuild_method:method`
- `py:src/requests/sessions.py:SessionRedirectMixin.rebuild_auth:method`
- `py:src/requests/sessions.py:SessionRedirectMixin.should_strip_auth:method`

**Reference outline:**
1. After the first adapter call, `Session.send` (with redirects allowed) drains the `resolve_redirects` generator into a history list, then returns the last response with the earlier ones in its `history`.
2. `resolve_redirects` asks `get_redirect_target` for the next URL, which returns the Location header (re-decoded as UTF-8) only when the response is a redirect.
3. For each hop it copies the prepared request, consumes and closes the old response, raises `TooManyRedirects` once the history reaches `max_redirects`, and resolves scheme-relative and relative locations against the previous URL while carrying the fragment over.
4. `rebuild_method` switches the method to GET for a 303 (except HEAD), for a 302 (except HEAD), and for a POST answered with a 301; for codes other than 307/308 the body and content headers are dropped.
5. Cookies are re-extracted and merged into the new request, proxies are rebuilt, and `rebuild_auth` removes the Authorization header when `should_strip_auth` reports a change of host (or a non-default scheme/port change other than http to https on standard ports), then reapplies netrc auth if the session trusts the environment.
6. The new request is sent with `self.send(..., allow_redirects=False)`, cookies from its response go into the session jar, and the loop repeats with the next redirect target.

## requests-flow-003

**Question:** How does `HTTPDigestAuth` get a request authenticated when the server first answers 401 with a Digest challenge?

**Must cite:**
- `py:src/requests/models.py:PreparedRequest.prepare_auth:method`
- `py:src/requests/auth.py:HTTPDigestAuth.__call__:method`
- `py:src/requests/models.py:RequestHooksMixin.register_hook:method`
- `py:src/requests/sessions.py:Session.send:method`
- `py:src/requests/hooks.py:dispatch_hook:function`
- `py:src/requests/auth.py:HTTPDigestAuth.handle_401:method`
- `py:src/requests/auth.py:HTTPDigestAuth.build_digest_header:method`
- `py:src/requests/adapters.py:HTTPAdapter.send:method`

**Reference outline:**
1. While the request is prepared, `prepare_auth` calls the auth object on the `PreparedRequest`, so `HTTPDigestAuth.__call__` runs.
2. `__call__` sets up per-thread state, adds an Authorization header right away only if a nonce from an earlier exchange is saved, records the body position if the body supports `tell`, and registers `handle_401` and `handle_redirect` as response hooks via `register_hook`.
3. `Session.send` sends the request through the adapter and then runs the response hooks with `dispatch_hook`, which calls each hook and keeps any non-None return value as the response.
4. `handle_401` acts only on a 4xx response whose WWW-Authenticate header mentions digest and only on the first such attempt; it rewinds the body if a position was saved, parses the challenge into per-thread state, consumes and closes the response, and copies the request with its cookies refreshed.
5. `build_digest_header` picks the hash from the challenge's algorithm (MD5, MD5-SESS, SHA, SHA-256, SHA-512), computes HA1/HA2, updates the nonce count, makes a client nonce, and assembles the "Digest ..." header value (returning None for unsupported algorithms or qop).
6. `handle_401` sets that header on the copy and resends it with `r.connection.send`, which is the `HTTPAdapter` that built the 401 response, appends the 401 to the new response's history, and returns the new response as the result of the hook.

## requests-flow-004

**Question:** When a caller passes both `files=` and a dict `data=`, how does requests build the multipart body and set its Content-Type and Content-Length headers?

**Must cite:**
- `py:src/requests/sessions.py:Session.prepare_request:method`
- `py:src/requests/models.py:PreparedRequest.prepare:method`
- `py:src/requests/models.py:PreparedRequest.prepare_body:method`
- `py:src/requests/models.py:RequestEncodingMixin._encode_files:method`
- `py:src/requests/models.py:PreparedRequest.prepare_content_length:method`
- `py:src/requests/utils.py:super_len:function`

**Reference outline:**
1. `Session.prepare_request` builds a `PreparedRequest` and calls its `prepare` with the request's files and data.
2. `prepare` runs the steps in order (method, URL, headers, cookies, body, auth, hooks); the body step is `prepare_body`.
3. A dict is not treated as a streamed body, so `prepare_body` takes the non-streaming branch and, because files were given, calls `_encode_files` with the files and data.
4. `_encode_files` rejects a missing files value or string data, turns each data field into a (name, bytes) pair, turns each file entry (a bare file object or a 2-, 3- or 4-tuple of filename, file object, content type, headers) into a urllib3 `RequestField`, reading file objects, and encodes everything with urllib3's `encode_multipart_formdata`, returning the body and a multipart Content-Type with its boundary.
5. `prepare_body` then calls `prepare_content_length`, which measures the body with `super_len` and sets Content-Length when the length is non-zero.
6. Finally `prepare_body` sets the multipart Content-Type only if the caller did not supply one, and stores the body on the request.
